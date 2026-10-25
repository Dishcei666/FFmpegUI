# -*- coding: utf-8 -*-
"""音频工具与视频拼接核心冒烟测试：python test/smoke_av.py"""
import os
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.core.audio import build_audio                    # noqa: E402
from app.core.merging import (build_fast_join, build_join,  # noqa: E402
                              check_compatible)
from app.core.probe import probe_file                     # noqa: E402
from app.core.runner import Task, run_task                # noqa: E402

TMP = os.path.join(_ROOT, "test", "tmp")


def assert_(cond, msg):
    if not cond:
        raise SystemExit(f"✗ 断言失败：{msg}")
    print(f"  ✓ {msg}")


def run_task_for(name, src, inp, out, ext, duration, custom_input=False,
                 base=None):
    preset = {"args": out, "ext": ext, "name": f"测试·{name}"}
    task = Task(src, preset, out_dir=TMP, duration=duration, base_name=base,
                task_id=f"{src}#{name}", input_args=inp,
                custom_input=custom_input)
    run_task(task, on_progress=lambda t: None, on_log=lambda l: None)
    assert_(task.status == "done",
            f"{name} 执行成功（{(task.error or '')[-160:] if task.error else ''}）")
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

    wav = os.path.join(TMP, "测试音频.wav")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3", "-c:a", "pcm_s16le", wav],
        check=True, capture_output=True)

    # 1. 音频转换（归一化 + 降噪链）
    out, ext, label = build_audio("mp3-320", normalize=True, denoise=True)
    assert_("归一化" in label and "降噪" in label, f"处理链标签：{label}")
    p = run_task_for("audio", wav, [], out, ext, 3.0, base="音频输出_归一化")
    info = probe_file(p)
    assert_(info["audio"]["codec"] == "mp3", "输出为 MP3")
    assert_(2.5 <= info["duration"] <= 3.5,
            f"时长 {info['duration']:.2f}s（≈3）")

    # 2. 从视频提取音轨 -> M4A
    out, ext, _ = build_audio("aac-256")
    p = run_task_for("m4a", src, [], out, ext, 8.0, base="提取音轨")
    info = probe_file(p)
    assert_(info["audio"]["codec"] == "aac", "提取输出为 AAC")

    # 3. 兼容性检查
    i1, i_small_info = probe_file(src), None
    ok, reason = check_compatible([i1, i1])
    assert_(ok and "一致" in reason, f"相同参数判定可快速合并：{reason}")
    small = os.path.join(TMP, "小尺寸.mp4")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", src, "-vf", "scale=320:180",
         "-c:a", "copy", small], check=True, capture_output=True)
    i_small_info = probe_file(small)
    ok, reason = check_compatible([i1, i_small_info])
    assert_(not ok and "分辨率" in reason, f"分辨率不同判定需重编码：{reason}")

    # 4. 快速合并（特殊字符文件名：中文+空格+引号，验证 concat 清单转义）
    special = os.path.join(TMP, "测试'引号 片段.mp4")
    shutil.copyfile(src, special)
    inp, out, ext, label, list_path = build_fast_join([src, special])
    p = run_task_for("fastjoin", src, inp, out, ext, 16.0,
                     custom_input=True, base="拼接_快速")
    info = probe_file(p)
    assert_(15.4 <= info["duration"] <= 16.6,
            f"快速合并时长 {info['duration']:.2f}s（≈16，免转码）")
    os.remove(list_path)

    # 5. 重编码合并（不同分辨率统一到 640×360）
    inp, out, ext, label, total = build_join([src, small],
                                             [i1, i_small_info], "x264-hq")
    p = run_task_for("rejoin", src, inp, out, "mp4", total,
                     custom_input=True, base="拼接_重编码")
    info = probe_file(p)
    assert_(info["video"]["w"] == 640 and info["video"]["h"] == 360,
            f"统一分辨率 {info['video']['w']}×{info['video']['h']}")
    assert_(abs(info["duration"] - 16) < 0.8,
            f"重编码合并时长 {info['duration']:.2f}s（≈16）")

    print("\n音频与拼接模块冒烟测试全部通过 ✔")


if __name__ == "__main__":
    main()
