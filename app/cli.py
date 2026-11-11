# -*- coding: utf-8 -*-
"""无界面自动化入口：python -m app.cli --help。stdout 始终保留给结果。"""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading

from app.api import execute


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parser():
    common = JsonArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出 JSON（默认行为，供 AI 或脚本使用）")
    parser = JsonArgumentParser(
        description="FFmpegUI 无界面自动化；stdout 为单个 UTF-8 JSON 结果。",
        parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("formats", parents=[common], help="列出可用输出格式及兼容编码器")
    commands.add_parser("capabilities", parents=[common], help="查询本机 FFmpeg 完整能力列表")
    probe = commands.add_parser("probe", parents=[common], help="探测本地媒体文件")
    probe.add_argument("inputs", nargs="+", help="一个或多个输入文件")
    test = commands.add_parser("test-encoder", parents=[common], help="测试指定编码器")
    test.add_argument("encoder")
    test.add_argument("--kind", choices=("video", "audio", "subtitle"), default="video")
    convert = commands.add_parser("convert", parents=[common], help="执行单个或批量转换")
    convert.add_argument("inputs", nargs="+", help="一个或多个本地输入文件")
    mode = convert.add_mutually_exclusive_group()
    mode.add_argument("--format", dest="format_id", help="输出格式 ID，默认 mp4；用 formats 查询")
    mode.add_argument("--preset", help="常用预设 ID，例如 copy-mp4 / x264-hq / audio-mp3")
    convert.add_argument("--video-encoder", help="视频编码器，copy 复制，none 禁用")
    convert.add_argument("--audio-encoder", help="音频编码器，copy 复制，none 禁用")
    for option, help_text in (
            ("quality", "画质 0–51，越低质量越高"),
            ("video-bitrate", "视频码率 kbps，0 使用质量模式"),
            ("audio-bitrate", "音频码率 kbps，0 使用编码器默认"),
            ("width", "输出宽度，0 保持或按比例计算"),
            ("height", "输出高度，0 保持或按比例计算"),
            ("sample-rate", "音频采样率 Hz，0 保持"),
            ("channels", "声道数，0 保持")):
        convert.add_argument(f"--{option}", type=int, help=help_text)
    convert.add_argument("--fps", type=float, help="帧率，0 保持")
    convert.add_argument("--output-dir", "--out-dir", dest="output_dir",
                         help="输出目录，默认源目录；支持创建目录")
    convert.add_argument("--concurrency", type=int, default=1, help="并发数 1–4，默认 1")
    convert.add_argument("--progress", action="store_true", help="向 stderr 输出 NDJSON 进度与日志")
    run = commands.add_parser("run", parents=[common], help="从 JSON 文件或 stdin 执行请求")
    run.add_argument("--request", default="-", help="UTF-8 JSON 文件，- 表示 stdin（默认）")
    run.add_argument("--progress", action="store_true", help="向 stderr 输出 NDJSON 进度与日志")
    return parser


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def _load_request(filename):
    try:
        if filename == "-":
            raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
            text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")
        else:
            text = Path(filename).read_text(encoding="utf-8-sig")
        def invalid_constant(value):
            raise ValueError(f"JSON 不允许非有限数字：{value}")
        request = json.loads(text, object_pairs_hook=_no_duplicates,
                             parse_constant=invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON 请求：{exc}") from None
    if not isinstance(request, dict):
        raise ValueError("JSON 请求必须是对象")
    return request


def _request(args):
    if args.command == "run":
        return _load_request(args.request)
    if args.command in ("formats", "capabilities"):
        return {"action": args.command}
    if args.command == "probe":
        return {"action": "probe", "inputs": args.inputs}
    if args.command == "test-encoder":
        return {"action": "test-encoder", "encoder": args.encoder, "kind": args.kind}
    options = {name: getattr(args, name) for name in (
        "format_id", "video_encoder", "audio_encoder", "quality", "video_bitrate",
        "audio_bitrate", "width", "height", "fps", "sample_rate", "channels")
        if getattr(args, name) is not None}
    if args.preset and options:
        raise ValueError("--preset 不能与自定义编码、画质或尺寸参数同时使用")
    request = {"action": "convert", "inputs": args.inputs, "concurrency": args.concurrency}
    if args.preset:
        request["preset"] = args.preset
    else:
        request["options"] = options
    if args.output_dir is not None:
        request["output_dir"] = args.output_dir
    return request


def _json(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()


def main(argv=None):
    """返回 0 成功、1 执行失败、2 请求错误、130 取消。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = None
    action = None
    cancel = threading.Event()
    old_handler = None
    has_handler = False
    event_lock = threading.Lock()

    def interrupt(_signum, _frame):
        if cancel.is_set():
            raise KeyboardInterrupt
        cancel.set()

    def progress(event):
        with event_lock:
            _json(sys.stderr, event)

    try:
        args = _parser().parse_args(argv)
        request = _request(args)
        action = request.get("action")
        if threading.current_thread() is threading.main_thread():
            old_handler = signal.signal(signal.SIGINT, interrupt)
            has_handler = True
        result = execute(request, on_event=progress if getattr(args, "progress", False) else None,
                         cancel_event=cancel)
        if cancel.is_set():
            result["cancelled"] = True
            result["ok"] = False
        _json(sys.stdout, result)
        if result.get("cancelled"):
            return 130
        return 0 if result.get("ok") else 1
    except KeyboardInterrupt:
        _json(sys.stdout, {"ok": False, "action": action, "cancelled": True,
                           "error": "操作已取消", "error_type": "cancelled"})
        return 130
    except (ValueError, TypeError) as exc:
        _json(sys.stdout, {"ok": False, "action": action, "error": str(exc),
                           "error_type": "validation"})
        return 2
    except Exception as exc:
        _json(sys.stdout, {"ok": False, "action": action, "error": str(exc),
                           "error_type": "runtime"})
        return 1
    finally:
        if has_handler:
            signal.signal(signal.SIGINT, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
