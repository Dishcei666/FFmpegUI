# -*- coding: utf-8 -*-
"""水印与字幕烧录的命令构建。

所有要嵌入滤镜串的路径（字幕文件、drawtext 文本）都通过临时文件
规避特殊字符问题；图片水印走第二个 -i 输入，路径不进滤镜串。
"""
import os
import tempfile

from .presets import get_preset
from .utils import escape_filter_path

# 九宫格位置 -> 表达式（drawtext 用小写 w/h，overlay 用大写 W/H 为主画面）
X_TEXT = {"left": "{m}", "center": "(w-text_w)/2", "right": "w-text_w-{m}"}
Y_TEXT = {"top": "{m}", "middle": "(h-text_h)/2", "bottom": "h-text_h-{m}"}
X_OVERLAY = {"left": "{m}", "center": "(W-w)/2", "right": "W-w-{m}"}
Y_OVERLAY = {"top": "{m}", "middle": "(H-h)/2", "bottom": "H-h-{m}"}

SUBTITLE_STYLES = [
    ("跟随字幕文件（ASS 样式）", ""),
    ("白字黑边（清晰通用）",
     "FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
     "Outline=1.6,Bold=1,Shadow=0"),
    ("黄字黑边（经典）",
     "FontSize=18,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
     "Outline=1.6,Bold=1,Shadow=0"),
    ("底部大字",
     "FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
     "Outline=2,Bold=1,MarginV=36"),
]

_FONT_CANDIDATES = ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
                    "arial.ttf")


def find_font():
    """返回第一个存在的系统中文字体路径（微软雅黑优先），找不到返回 None。"""
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _FONT_CANDIDATES:
        path = os.path.join(fonts_dir, name)
        if os.path.exists(path):
            return path
    return None


def build_text_watermark(text, font_size, color_hex, opacity, pos_x, pos_y,
                         margin, encode_preset_id):
    """文字水印。返回 (out_args, ext, label, 临时文件清理函数)。

    文本写入临时文件由 textfile 引用，规避 drawtext 的转义地狱。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    font = find_font()
    m = max(0, int(margin))
    color = (color_hex or "#FFFFFF").lstrip("#")
    alpha = max(0.05, min(1.0, opacity / 100.0))

    fd, txt_path = tempfile.mkstemp(suffix=".txt", prefix="ffgui-wm-",
                                    text=False)
    os.close(fd)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    head = "drawtext="
    if font:
        head += f"fontfile={escape_filter_path(font)}:"
        # 无系统字体时仍尝试渲染（中文可能变方块，但不至于失败）
    vf = (f"{head}textfile={escape_filter_path(txt_path)}"
          f":fontsize={max(8, int(font_size))}"
          f":fontcolor=0x{color}@{alpha:.2f}"
          f":borderw=2:bordercolor=black@0.55"
          f":x={X_TEXT[pos_x].format(m=m)}"
          f":y={Y_TEXT[pos_y].format(m=m)}")
    out = ["-vf", vf, *preset["args"]]

    def cleanup():
        try:
            os.remove(txt_path)
        except OSError:
            pass

    return out, "mp4", preset["name"], cleanup


def build_image_watermark(image_path, scale_pct, opacity, pos_x, pos_y,
                          margin, encode_preset_id):
    """图片水印（第二个输入 + overlay）。返回 (input_args, out_args, ext, label)。

    input_args 已含视频与图片两个 -i，配合 Task(custom_input=True) 使用。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    m = max(0, int(margin))
    alpha = max(0.05, min(1.0, opacity / 100.0))
    scale = max(2, min(100, scale_pct)) / 100.0

    graph = (
        f"[1:v]scale=iw*{scale:.3f}:-1:flags=lanczos,"
        f"colorchannelmixer=aa={alpha:.2f}[wm];"
        f"[0:v][wm]overlay=x={X_OVERLAY[pos_x].format(m=m)}:"
        f"y={Y_OVERLAY[pos_y].format(m=m)}:format=auto[v]"
    )
    inp = ["-i", "{VIDEO}", "-i", image_path]      # {VIDEO} 由调用方替换为源
    out = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
           *preset["args"]]
    return inp, out, "mp4", preset["name"]


def build_subtitle(sub_path, style_index, encode_preset_id, video_path=None):
    """字幕烧录。返回 (out_args, ext, label, 清理函数)。

    字幕文件复制为 ASCII 临时名再引用，规避路径特殊字符。
    ASS 保留原样式（style_index=0 时 force_style 为空）。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    style = SUBTITLE_STYLES[max(0, min(len(SUBTITLE_STYLES) - 1,
                                       style_index))][1]

    fd, tmp_sub = tempfile.mkstemp(suffix=".srt", prefix="ffgui-sub-")
    os.close(fd)
    import shutil
    shutil.copyfile(sub_path, tmp_sub)

    vf = f"subtitles=filename={escape_filter_path(tmp_sub)}"
    if style:
        vf += f":force_style='{style}'"
    out = ["-vf", vf, *preset["args"]]

    def cleanup():
        try:
            os.remove(tmp_sub)
        except OSError:
            pass

    return out, "mp4", preset["name"], cleanup
