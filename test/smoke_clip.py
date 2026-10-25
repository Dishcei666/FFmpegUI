# -*- coding: utf-8 -*-
"""剪辑核心冒烟测试：无损/精确/旋转/变速/合并/关键帧/黑边。运行：python test/smoke_clip.py"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.core.clipping import (build_merge, build_segment,   # noqa: E402
                               detect_crop, probe_keyframes)
from app.core.probe import probe_file                        # noqa: E402
from app.core.runner import Task, run_task                   # noqa: E402

TMP = os.path.join(_ROOT, "test", "tmp")


def assert_(cond, msg):
    if not cond:
        raise SystemExit(f"✗ 断言失败：{msg}")
    print(f"  ✓ {msg}")


def run_clip(name, src, inp, out, ext, duration, custom_input=False,
             base=None):
    preset = {"args": out, "ext": ext, "name": f"测试·{name}"}
    task = Task(src, preset, out_dir=TMP, duration=duration,
                base_name=base, task_id=f"{src}#{name}",
                input_args=inp, custom_input=custom_input)
    run_task(task, on_progress=lambda t: None, on_log=lambda l: None)
    assert_(task.status == "done", f"{name} 执行成功（{(task.error or '')[:160]}）")
    return task.output_path


def main():
    os.makedirs(TMP, exist_ok=True)
    import subprocess
    src = os.path.join(TMP, "剪辑样片.mp4")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
         "-c:a", "aac", "-shortest", src],
        check=True, capture_output=True)
    assert_(os.path.exists(src), "生成 8 秒样片")

    # 1. 无损快切（切点对齐关键帧，时长允许偏差）
    inp, out, ext, _ = build_segment(src, 1.0, 4.0, "lossless", "x264-hq", {})
    p = run_clip("lossless", src, inp, out, ext, 3.0, base="剪辑样片_无损")
    info = probe_file(p)
    assert_(2.0 <= info["duration"] <= 4.5, f"无损切时长 {info['duration']:.2f}s（关键帧对齐）")

    # 2. 精确剪切 + 旋转 90°
    tf = {"rotate": 90, "crop": None, "speed": 1.0, "has_audio": True}
    inp, out, ext, _ = build_segment(src, 1.0, 4.0, "precise", "x264-hq", tf)
    p = run_clip("rotate", src, inp, out, ext, 3.0, base="剪辑样片_旋转")
    info = probe_file(p)
    assert_(info["video"]["h"] > info["video"]["w"],
            f"旋转后 {info['video']['w']}x{info['video']['h']}（应为竖向）")
    assert_(abs(info["duration"] - 3.0) < 0.4, f"精确时长 {info['duration']:.2f}s")

    # 3. 变速 1.5×（含音轨，atempo 路径）
    tf = {"rotate": 0, "crop": None, "speed": 1.5, "has_audio": True}
    inp, out, ext, _ = build_segment(src, 1.0, 4.0, "precise", "x264-hq", tf)
    p = run_clip("speed", src, inp, out, ext, 2.0, base="剪辑样片_变速")
    info = probe_file(p)
    assert_(abs(info["duration"] - 2.0) < 0.5, f"变速后时长 {info['duration']:.2f}s（≈3/1.5）")

    # 4. 两段合并（多输入 concat，重编码）
    inp, out, ext, _, total = build_merge(
        src, [(0.0, 2.0), (4.0, 6.0)], "x264-hq",
        {"rotate": 0, "crop": None, "speed": 1.0, "has_audio": True})
    p = run_clip("merge", src, inp, out, ext, total, custom_input=True,
                 base="剪辑样片_合并")
    info = probe_file(p)
    assert_(abs(info["duration"] - 4.0) < 0.6, f"合并时长 {info['duration']:.2f}s（≈4.0）")

    # 5. 关键帧探测
    kf = probe_keyframes(src)
    assert_(len(kf) >= 5 and 0.0 in kf, f"关键帧 {len(kf)} 个，含 0 时刻")

    # 6. 黑边检测（pad 出黑边的样片）
    padded = os.path.join(TMP, "带黑边样片.mp4")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", src,
         "-vf", "pad=iw+80:ih+60:40:30:black", "-c:a", "copy", padded],
        check=True, capture_output=True)
    crop = detect_crop(padded)
    assert_(crop is not None, f"黑边检测识别有效区域：{crop}")

    print("\n剪辑模块冒烟测试全部通过 ✔")


if __name__ == "__main__":
    main()
