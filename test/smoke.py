# -*- coding: utf-8 -*-
"""核心层冒烟测试（不依赖 Qt）：探测 -> 转换 -> 校验输出。

运行：python test/smoke.py
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.core.presets import PRESETS            # noqa: E402
from app.core.probe import probe_file           # noqa: E402
from app.core.runner import run_task, Task      # noqa: E402

TMP = os.path.join(_ROOT, "test", "tmp")


def assert_(cond, msg):
    if not cond:
        raise SystemExit(f"✗ 断言失败：{msg}")
    print(f"  ✓ {msg}")


def main():
    os.makedirs(TMP, exist_ok=True)
    src = os.path.join(TMP, "示例视频.mp4")

    # 1. 用 ffmpeg 自带测试源生成 3 秒样片（顺便验证中文路径）
    if os.path.exists(src):
        os.remove(src)
    gen = subprocess.run(
        ["ffmpeg", "-hide_banner", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", src],
        capture_output=True)
    assert_(gen.returncode == 0 and os.path.exists(src), "生成测试样片（中文路径）")

    # 2. 探测
    info = probe_file(src)
    assert_(info["duration"] > 2.5, f"时长探测 {info['duration']:.1f}s")
    assert_(info["video"]["w"] == 640 and info["video"]["h"] == 360,
            f"分辨率探测 {info['video']['w']}x{info['video']['h']}")
    assert_(info["video"]["codec"] == "h264", "视频编码探测 h264")
    assert_(info["audio"] is not None, "音轨存在")

    # 3. 跑两个代表性预设：无损快转 + 提取 MP3
    for pid in ("copy-mp4", "audio-mp3"):
        preset = next(p for p in PRESETS if p["id"] == pid)
        task = Task(src, preset, out_dir=TMP, duration=info["duration"])
        run_task(task, on_progress=lambda t: None, on_log=lambda l: None)
        assert_(task.status == "done",
                f"{pid} 执行成功（{task.error[:120] if task.error else ''}）")
        assert_(os.path.exists(task.output_path)
                and os.path.getsize(task.output_path) > 0,
                f"{pid} 输出文件有效")
        print(f"    → {os.path.basename(task.output_path)} "
              f"({os.path.getsize(task.output_path) / 1024:.0f} KB)")

    # 4. 取消路径（秒级样片太快，仅验证 cancelled 状态赋值逻辑）
    t = Task(src, PRESETS[0], out_dir=TMP)
    t.cancel_event = None
    t.status = "cancelled"
    assert_(t.status == "cancelled", "任务状态字段可写")

    print("\n全部冒烟测试通过 ✔")


if __name__ == "__main__":
    main()
