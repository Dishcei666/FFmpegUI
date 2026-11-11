# -*- coding: utf-8 -*-
"""视频拼接核心：兼容性检查 + concat 快速合并 + 重编码统一合并。"""
import os
import tempfile
from fractions import Fraction

from .presets import get_preset
from .filters import split_video_filters


def _different(streams, key):
    values = set()
    for stream in streams:
        value = stream.get(key)
        if value in (None, "", 0, "0/0"):
            continue
        if key in ("frame_rate", "time_base"):
            try:
                value = Fraction(str(value))
            except (ValueError, ZeroDivisionError):
                pass
        values.add(value)
    return len(values) > 1


def check_compatible(infos):
    """判断能否用 concat 流复制快速合并。返回 (ok, reason)。

    infos: probe_file 输出列表（顺序与文件一致）。
    """
    if len(infos) < 2:
        return False, "至少需要 2 个文件"
    if any(not i.get("video") for i in infos):
        return False, "存在不含视频流的文件"
    codecs = {i["video"]["codec"] for i in infos}
    if len(codecs) > 1:
        return False, f"编码不一致（{'/'.join(sorted(codecs))}）"
    sizes = {(i["video"]["w"], i["video"]["h"]) for i in infos}
    if len(sizes) > 1:
        return False, "分辨率不一致（" + "、".join(f"{w}×{h}" for w, h in
                                                sorted(sizes, reverse=True)) + "）"
    for key, label in (("fps", "帧率"), ("frame_rate", "帧率"),
                       ("pix_fmt", "像素格式"), ("profile", "视频 profile"),
                       ("level", "视频 level"), ("time_base", "视频时间基")):
        if _different([i["video"] for i in infos], key):
            return False, f"{label}不一致"
    audios = [i.get("audio") for i in infos]
    if any(audios) and not all(audios):
        return False, "部分文件有音轨、部分没有"
    if all(audios):
        acodecs = {a["codec"] for a in audios}
        rates = {a["sample_rate"] for a in audios}
        if len(acodecs) > 1:
            return False, f"音频编码不一致（{'/'.join(sorted(acodecs))}）"
        if len(rates) > 1:
            return False, "音频采样率不一致"
        for key, label in (("channels", "声道数"), ("channel_layout", "声道布局"),
                           ("sample_fmt", "音频采样格式"), ("time_base", "音频时间基")):
            if _different(audios, key):
                return False, f"{label}不一致"
    return True, "编码参数一致，可快速合并（不重编码）"


def write_concat_list(paths):
    """生成 concat 清单临时文件，返回其路径（用完由调用方删除）。"""
    lines = []
    for p in paths:
        esc = p.replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{esc}'")
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="ffgui-merge-",
                                     text=False)
    os.close(fd)
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return list_path


def build_fast_join(paths, out_ext=None):
    """concat 流复制合并。返回 (input_args, out_args, ext, label, 清单文件路径)。"""
    list_path = write_concat_list(paths)
    ext = out_ext or os.path.splitext(paths[0])[1][1:].lower() or "mp4"
    inp = ["-f", "concat", "-safe", "0", "-i", list_path]
    out = ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    return inp, out, ext, "快速合并", list_path


def build_join(paths, infos, encode_preset_id):
    """重编码合并：统一缩放到首个文件的分辨率/帧率再 concat。

    返回 (input_args, out_args, ext, label, total_duration)。input_args 已含全部 -i。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    encode_args, preset_vf = split_video_filters(preset["args"])
    if not paths or len(paths) != len(infos) or any(not i.get("video") for i in infos):
        raise ValueError("拼接文件必须都含视频流，且媒体信息与文件一一对应")
    v0 = infos[0]["video"]
    w, h = max(v0["w"] // 2 * 2, 2), max(v0["h"] // 2 * 2, 2)
    fps = v0.get("fps") or 0
    normalize = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                 f"format=yuv420p,setpts=PTS-STARTPTS")
    if fps > 0:
        normalize += f",fps={fps:.3f}"

    audios = [i.get("audio") for i in infos]
    use_audio = any(audios)                 # 缺少音轨的片段补静音，保留其余片段声音
    n = len(paths)

    inp = []
    graph_parts = []
    seg_labels = []
    for i, p in enumerate(paths):
        inp += ["-i", p]
        graph_parts.append(f"[{i}:v]{normalize}[v{i}]")
        seg_labels.append(f"[v{i}]")
        if use_audio:
            duration = float(infos[i].get("duration") or 0)
            if not audios[i] and duration <= 0:
                raise ValueError("无音轨片段的时长未知，无法安全补静音")
            if audios[i]:
                audio = (f"[{i}:a:0]aresample=48000,"
                         "aformat=sample_fmts=fltp:channel_layouts=stereo")
                if duration > 0:
                    audio += f",apad,atrim=duration={duration:.6f}"
                audio += ",asetpts=PTS-STARTPTS"
            else:
                audio = ("anullsrc=r=48000:cl=stereo,"
                         f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS")
            graph_parts.append(f"{audio}[a{i}]")
            seg_labels.append(f"[a{i}]")

    # concat 滤镜的输入按"每段 视频+音频"交错排列；链与链之间用 ; 分隔
    joined = ";".join(graph_parts)
    if use_audio:
        graph = (joined + ";" + "".join(seg_labels) +
                 f"concat=n={n}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        graph = (joined + ";" + "".join(seg_labels) +
                 f"concat=n={n}:v=1[v]")
        maps = ["-map", "[v]"]

    if preset_vf:
        graph += f";[v]{preset_vf}[vout]"
        maps[1] = "[vout]"
    out = ["-filter_complex", graph, *maps, *encode_args]
    total = sum((i.get("duration") or 0) for i in infos)
    return inp, out, "mp4", preset["name"], total
