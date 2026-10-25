# -*- coding: utf-8 -*-
"""水印与字幕模块冒烟测试：python test/smoke_wm.py"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.core.runner import Task, run_task                    # noqa: E402
from app.core.watermark import (build_image_watermark,        # noqa: E402
                                build_subtitle, build_text_watermark,
                                find_font)

TMP = os.path.join(_ROOT, "test", "tmp")


def assert_(cond, msg):
    if not cond:
        raise SystemExit(f"✗ 断言失败：{msg}")
    print(f"  ✓ {msg}")


def run_task_for(name, src, inp, out, ext, duration, custom_input=False,
                 base=None, cleanup=None):
    preset = {"args": out, "ext": ext, "name": f"测试·{name}"}
    task = Task(src, preset, out_dir=TMP, duration=duration, base_name=base,
                task_id=f"{src}#{name}", input_args=inp,
                custom_input=custom_input)
    run_task(task, on_progress=lambda t: None, on_log=lambda l: None)
    if cleanup:
        cleanup()
    assert_(task.status == "done",
            f"{name} 执行成功（{(task.error or '')[-160:] if task.error else ''}）")
    assert_(os.path.getsize(task.output_path) > 0, f"{name} 输出有效")
    return task.output_path


def main():
    os.makedirs(TMP, exist_ok=True)
    src = os.path.join(TMP, "剪辑样片.mp4")
    if not os.path.exists(src):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=30",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
             "-c:a", "aac", "-shortest", src],
            check=True, capture_output=True)

    # 0. 系统中文字体可用（drawtext 渲染中文的前提）
    assert_(find_font() is not None, f"找到系统中文字体：{find_font()}")

    # 1. 文字水印（中文 + 引号 + 冒号，验证 textfile 临时文件方案）
    out, ext, label, cleanup = build_text_watermark(
        "FFmpegUI 水印'测试: v0.4", 28, "#FFFFFF", 70,
        "right", "bottom", 24, "x264-hq")
    run_task_for("textwm", src, [], out, ext, 8.0, base="水印_文字",
                 cleanup=cleanup)

    # 2. 图片水印（PIL 生成半透明 PNG，第二个输入 overlay）
    from PIL import Image, ImageDraw
    png = os.path.join(TMP, "水印图.png")
    img = Image.new("RGBA", (200, 72), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, 199, 71], radius=14,
                           fill=(109, 124, 255, 200))
    draw.text((26, 18), "FFmpegUI", fill=(255, 255, 255, 255))
    img.save(png)
    inp, out, ext, label = build_image_watermark(
        png, 20, 80, "left", "top", 18, "x264-hq")
    inp = [src if a == "{VIDEO}" else a for a in inp]
    run_task_for("imgwm", src, inp, out, ext, 8.0, custom_input=True,
                 base="水印_图片")

    # 3. 字幕烧录（中文字幕 + 白字黑边样式；引号路径的源文件）
    srt = os.path.join(TMP, "测试字幕.srt")
    with open(srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,500 --> 00:00:03,000\n这是第一条测试字幕\n\n"
                "2\n00:00:03,500 --> 00:00:07,800\n第二条：水印与字幕功能测试\n")
    special = os.path.join(TMP, "测试'引号 片段.mp4")
    if not os.path.exists(special):
        import shutil
        shutil.copyfile(src, special)
    out, ext, label, cleanup = build_subtitle(srt, 1, "x264-hq")
    run_task_for("subtitle", special, [], out, ext, 8.0, base="字幕烧录",
                 cleanup=cleanup)

    # 4. whisper AI 字幕（模型已在部署时下载；缺失则提示跳过）
    from app.core.whisper import is_downloaded, transcribe
    if is_downloaded("base"):
        wav = os.path.join(TMP, "speech.wav")
        if not os.path.exists(wav):
            print("  - 无语音样本，跳过 whisper 测试")
        else:
            out_srt = os.path.join(TMP, "AI字幕.srt")
            if os.path.exists(out_srt):
                os.remove(out_srt)
            transcribe(wav, "base", "auto", out_srt, duration=7.0)
            content = open(out_srt, encoding="utf-8").read()
            assert_("-->" in content and "1" in content,
                    f"AI 字幕生成成功（{len(content)} 字符）")
    else:
        print("  - whisper 模型未下载，跳过 AI 字幕测试")

    print("\n水印/字幕模块冒烟测试全部通过 ✔")


if __name__ == "__main__":
    main()
