# -*- coding: utf-8 -*-
"""检查共享目录中的 GUI 与 CLI 打包产物：python tools/verify_bundle.py。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class VerificationError(RuntimeError):
    pass


def _clean_environment(config):
    env = dict(os.environ, LOCALAPPDATA=str(config))
    for key in ("FFGUI_SCREENSHOT", "FFGUI_SHOT_DELAY", "FFGUI_PAGE",
                "FFGUI_CONVERT_MODE", "FFGUI_AUTOQUIT_MS", "PYTHONPATH",
                "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                "FFMPEG_PATH", "FFPROBE_PATH", "FFPLAY_PATH"):
        env.pop(key, None)
    return env


def _minimal_path(env):
    env = dict(env)
    if os.name == "nt":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    return env


def _run(command, config, env, label, timeout=30):
    print(f"CHECK {label}", flush=True)
    try:
        result = subprocess.run(
            list(map(str, command)), cwd=config, env=env,
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"{label} 无法执行或超时：{exc}") from exc
    if "Traceback" in result.stderr or "DLL load failed" in result.stderr:
        raise VerificationError(f"{label} 运行时错误：{result.stderr}")
    return result


def _json_result(result, label, expected_code=0, expected_ok=True):
    if result.returncode != expected_code:
        raise VerificationError(
            f"{label} 退出码 {result.returncode}，期望 {expected_code}："
            f"{result.stdout}\n{result.stderr}")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise VerificationError(
            f"{label} stdout 不是单一 JSON 结果：{result.stdout}\n{result.stderr}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not expected_ok:
        raise VerificationError(f"{label} JSON ok 状态错误：{payload}")
    return payload


def _verify_cli(executable, config):
    # CLI 保留原 PATH 以访问用户安装的 FFmpeg；配置仍完全隔离。
    env = _clean_environment(config)
    result = _run([executable, "--help"], config, _minimal_path(env), "CLI --help")
    if result.returncode or "usage:" not in result.stdout.lower():
        raise VerificationError(f"CLI --help 失败：{result.stdout}\n{result.stderr}")
    print("PASS CLI --help（无需 FFmpeg）", flush=True)

    result = _run([executable], config, env, "CLI missing arguments")
    payload = _json_result(result, "CLI missing arguments", 2, False)
    if not payload.get("error"):
        raise VerificationError(f"CLI 缺失参数未返回 JSON error：{payload}")
    print("PASS CLI JSON argument error", flush=True)

    ffmpeg = shutil.which("ffmpeg", path=env.get("PATH"))
    ffprobe = shutil.which("ffprobe", path=env.get("PATH"))
    result = _run([executable, "formats", "--json"], config, env, "CLI formats --json")
    _json_result(result, "CLI formats --json", 0 if ffmpeg else 1, bool(ffmpeg))
    print("PASS CLI formats JSON", flush=True)
    if not ffmpeg or not ffprobe:
        missing = ", ".join(name for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe))
                            if not path)
        print(f"SKIP CLI 真实 probe/convert：原 PATH 缺少 {missing}", flush=True)
        return False

    source = Path(config) / "cli-sample.mp4"
    generation = _run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=64x48:r=5:d=0.4",
         "-c:v", "mpeg4", "-q:v", "5", source], config, env, "CLI sample generation")
    if generation.returncode or not source.is_file():
        raise VerificationError(f"无法生成 CLI 测试样片：{generation.stderr}")

    result = _run([executable, "probe", source, "--json"], config, env, "CLI probe --json")
    payload = _json_result(result, "CLI probe --json")
    if payload.get("action") != "probe":
        raise VerificationError(f"CLI probe action 错误：{payload}")
    print("PASS CLI real probe", flush=True)

    output = Path(config) / "converted"
    result = _run(
        [executable, "convert", source, "--preset", "copy-mp4",
         "--output-dir", output, "--json"], config, env, "CLI convert --json")
    payload = _json_result(result, "CLI convert --json")
    results = payload.get("results")
    if payload.get("action") != "convert" or not isinstance(results, list) or len(results) != 1:
        raise VerificationError(f"CLI convert 结果结构错误：{payload}")
    converted = results[0]
    path = Path(converted.get("output_path") or "")
    if converted.get("status") != "done" or not path.is_file() or path.stat().st_size <= 0:
        raise VerificationError(f"CLI convert 未生成有效文件：{converted}")
    if path.resolve() == source.resolve() or not source.is_file():
        raise VerificationError("CLI convert 覆盖或删除了源文件")
    print("PASS CLI real convert", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", nargs="?", type=Path,
                        default=ROOT / "dist" / "FFmpegUI" / "FFmpegUI.exe")
    parser.add_argument("--cli", type=Path,
                        help="CLI 程序路径，默认使用 GUI 同目录的 FFmpegUI-cli.exe")
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        parser.error(f"找不到打包程序：{executable}")
    cli = (args.cli or executable.with_name(
        "FFmpegUI-cli.exe" if os.name == "nt" else "FFmpegUI-cli")).resolve()
    if not cli.is_file():
        parser.error(f"找不到 CLI 打包程序：{cli}")
    with tempfile.TemporaryDirectory(prefix="ffgui-bundle-test-") as config:
        env = _minimal_path(_clean_environment(config))
        env.update(QT_QPA_PLATFORM="offscreen", FFGUI_AUTOQUIT_MS="1200")
        try:
            for page in ("convert", "advanced", "clip", "audio", "merge", "wm", "settings"):
                env["FFGUI_PAGE"] = "convert" if page == "advanced" else page
                env["FFGUI_CONVERT_MODE"] = "advanced" if page == "advanced" else "presets"
                result = _run([executable], config, env, page, timeout=15)
                if result.returncode:
                    raise VerificationError(f"{page} 退出码 {result.returncode}: {result.stderr}")
                print(f"PASS {page}", flush=True)
            media_checked = _verify_cli(cli, config)
        except VerificationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    suffix = "CLI 真实媒体检查通过。" if media_checked else "CLI 媒体实测已明确跳过。"
    print(f"六个页面、高级转换与 CLI 基础检查通过；{suffix}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
