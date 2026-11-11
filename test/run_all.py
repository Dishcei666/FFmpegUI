# -*- coding: utf-8 -*-
"""隔离配置运行全部回归与冒烟测试：python test/run_all.py。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory(prefix="ffgui-tests-") as config:
        env = dict(os.environ, LOCALAPPDATA=config, QT_QPA_PLATFORM="offscreen",
                   PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        # 测试既不继承自定义媒体工具路径，也不触发截图/自动退出。
        for key in ("FFMPEG_PATH", "FFPROBE_PATH", "FFPLAY_PATH", "FFGUI_PAGE",
                    "FFGUI_SCREENSHOT", "FFGUI_AUTOQUIT_MS", "FFGUI_SHOT_DELAY",
                    "FFGUI_CONVERT_MODE"):
            env.pop(key, None)
        commands = [
            ["-m", "unittest", "discover", "-s", "test", "-p", "test_*regressions.py", "-v"],
            *[[f"test/{name}.py"] for name in (
                "smoke", "smoke_clip", "smoke_av", "smoke_wm", "smoke_settings")],
        ]
        try:
            for command in commands:
                print("RUN " + " ".join(command), flush=True)
                result = subprocess.run([sys.executable, *command], cwd=ROOT, env=env,
                                        capture_output=True, encoding="utf-8",
                                        errors="replace", timeout=180)
                if result.stdout:
                    print(result.stdout, end="", flush=True)
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr, flush=True)
                result.check_returncode()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"验证失败：{exc}", file=sys.stderr)
            return 1
    print("全部回归与冒烟测试通过。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
