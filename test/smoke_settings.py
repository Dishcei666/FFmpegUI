# -*- coding: utf-8 -*-
"""设置与性能模块冒烟测试：python test/smoke_settings.py"""
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.core import gpu, settings                     # noqa: E402
from app.core.presets import get_preset                # noqa: E402
from app.core.queue import TaskQueue                   # noqa: E402
from app.core.runner import Task                       # noqa: E402

TMP = os.path.join(_ROOT, "test", "tmp")


def assert_(cond, msg):
    if not cond:
        raise SystemExit(f"✗ 断言失败：{msg}")
    print(f"  ✓ {msg}")


def main():
    os.makedirs(TMP, exist_ok=True)

    # 1. 设置读写与持久化（备份-修改-重载-还原）
    backup_data = dict(settings._data)
    backup_file = None
    if os.path.exists(settings._settings_path()):
        backup_file = open(settings._settings_path(),
                           encoding="utf-8").read()
    try:
        settings.set("concurrency", 3)
        settings.set("out_dir", TMP)
        settings.load()
        assert_(settings.get("concurrency") == 3, "设置持久化往返一致")
        assert_(settings.ffmpeg().endswith(("ffmpeg", "ffmpeg.exe"))
                or os.path.exists(settings.ffmpeg()),
                f"ffmpeg 命令解析：{settings.ffmpeg()}")
    finally:
        settings._data = backup_data
        settings.save()
        if backup_file is not None:
            with open(settings._settings_path(), "w", encoding="utf-8") as f:
                f.write(backup_file)

    # 2. GPU 参数替换（纯函数）
    x264 = get_preset("x264-hq")["args"]
    nv = gpu.apply_hw_args(x264, "h264_nvenc")
    assert_("-c:v" in nv and nv[nv.index("-c:v") + 1] == "h264_nvenc",
            "NVENC：libx264 → h264_nvenc")
    assert_("-cq" in nv and nv[nv.index("-cq") + 1] == "18"
            and "-crf" not in nv, "NVENC：-crf 18 → -cq 18")
    assert_("-preset" in nv and nv[nv.index("-preset") + 1] == "p5",
            "NVENC：preset → p5")

    qsv = gpu.apply_hw_args(x264, "h264_qsv")
    assert_("-global_quality" in qsv and "-preset" in qsv,
            "QSV：-crf → -global_quality，preset 保留")

    amf = gpu.apply_hw_args(x264, "h264_amf")
    assert_("-crf" not in amf and "-quality" in amf and "-preset" not in amf,
            "AMF：移除 crf/preset，使用 quality 档")

    copy_args = get_preset("copy-mp4")["args"]
    assert_(gpu.apply_hw_args(copy_args, "h264_nvenc") == copy_args,
            "无损复制参数不受 GPU 替换影响")

    # 3. 队列并发：3 个任务、并发 2，全部完成且 queue-done 只触发一次
    src = os.path.join(TMP, "剪辑样片.mp4")
    if not os.path.exists(src):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=15",
             "-c:v", "libx264", "-preset", "ultrafast", src],
            check=True, capture_output=True)
    events = []
    queue = TaskQueue(on_event=events.append, concurrency_provider=lambda: 2)
    tasks = [Task(src, {"args": ["-c", "copy"], "ext": "mp4", "name": "t"},
                  out_dir=TMP, duration=2.0,
                  base_name=f"并发{i}", task_id=f"conc#{i}")
             for i in range(3)]
    queue.enqueue(tasks)
    deadline = time.time() + 30
    while time.time() < deadline:
        if all(t.status in ("done", "error", "cancelled") for t in tasks):
            break
        time.sleep(0.1)
    done_events = [e for e in events if e.get("event") == "queue-done"]
    assert_(all(t.status == "done" for t in tasks), "并发 2：3 个任务全部完成")
    assert_(len(done_events) == 1, f"queue-done 只触发一次（实际 {len(done_events)}）")

    # 4. 真实 GPU 检测（仅展示，不作断言依据——各机不同）
    results = gpu.detect_all()
    print("  · 硬件编码器检测结果：",
          ", ".join(f"{gpu.LABELS[k]}={'可用' if v else '不可用'}"
                    for k, v in results.items()))

    print("\n设置与性能模块冒烟测试全部通过 ✔")


if __name__ == "__main__":
    main()
