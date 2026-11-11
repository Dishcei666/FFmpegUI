# -*- coding: utf-8 -*-
"""不依赖 Qt 的同步 JSON API，供命令行、脚本和其他代理调用。"""
from copy import deepcopy
import math
import os
import re
import threading

from .core.capabilities import detect_capabilities, test_encoder
from .core.conversion import available_encoders, available_formats, build_conversion
from .core.presets import get_preset
from .core.probe import probe_file
from .core.queue import TaskQueue
from .core.runner import Task

_FIELDS = {
    "formats": {"action"},
    "capabilities": {"action"},
    "probe": {"action", "inputs"},
    "test-encoder": {"action", "encoder", "kind"},
    "convert": {"action", "inputs", "output_dir", "options", "preset", "concurrency"},
}
_STRING_OPTIONS = {"format_id", "video_encoder", "audio_encoder"}
_INTEGER_OPTIONS = {"quality", "video_bitrate", "audio_bitrate", "width", "height", "sample_rate", "channels"}
_NUMBER_OPTIONS = {"fps"}


def _keys(value, allowed, label):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label}必须为 JSON 对象")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{label}含未知字段：{', '.join(sorted(unknown))}")


def _path(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label}必须为非空本地文件路径")
    if "://" in value or re.match(r"^(?:pipe|file|concat|lavfi|http|https):", value, re.I):
        raise ValueError(f"{label}只支持本地文件路径，不接受 URL 或 FFmpeg 协议")
    try:
        return os.path.realpath(os.path.abspath(value))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label}无效：{exc}") from None


def _inputs(request):
    values = request.get("inputs")
    if not isinstance(values, list) or not values:
        raise ValueError("inputs 必须为至少包含一个本地文件路径的数组")
    paths, seen = [], set()
    for value in values:
        path = _path(value, "输入路径")
        if not os.path.isfile(path):
            raise ValueError(f"输入文件不存在或不是普通文件：{value}")
        key = os.path.normcase(path)
        if key in seen:
            raise ValueError(f"inputs 含重复文件：{value}")
        seen.add(key)
        paths.append(path)
    return paths


def _options(request):
    options = request.get("options", {})
    _keys(options, _STRING_OPTIONS | _INTEGER_OPTIONS | _NUMBER_OPTIONS, "options")
    for key, value in options.items():
        if key in _STRING_OPTIONS:
            valid = isinstance(value, str) and bool(value.strip())
        elif key in _INTEGER_OPTIONS:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value))
        if not valid:
            raise ValueError(f"options.{key} 类型错误")
    return options


def _preset_for(preset_id, info, caps):
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"未知预设：{preset_id}")
    video, audio = info.get("video"), info.get("audio")
    if preset_id == "copy-mp4":
        result = build_conversion({"format_id": "mp4", "video_encoder": "copy" if video else "none",
                                   "audio_encoder": "copy" if audio else "none", "audio_bitrate": 0}, info, caps)
        result.update(id=preset_id, name=preset["name"])
        return result
    if preset_id == "audio-mp3":
        if not audio:
            raise ValueError("提取音频预设要求源文件含有音轨")
        profile, venc, aenc = "mp3", "none", "libmp3lame"
    elif preset_id == "gif":
        if not video:
            raise ValueError("GIF 预设要求源文件含有视频流")
        profile, venc, aenc = "gif", "gif", "none"
    else:
        if not video:
            raise ValueError("视频预设要求源文件含有视频流，请选择音频预设或高级音频格式")
        profile, venc, aenc = "mp4", "libx264", "aac" if audio else "none"
    # 先利用高级构建器校验当前编译能力；执行时保留既有预设的质量和滤镜。
    build_conversion({"format_id": profile, "video_encoder": venc, "audio_encoder": aenc}, info, caps)
    result = deepcopy(preset)
    args = result["args"]
    if preset_id == "audio-mp3":
        args = ["-map", "0:a:0", *args]
    elif preset_id != "gif":
        args = ["-map", "0:v:0", *(["-map", "0:a:0"] if audio else ["-an"]), *args]
    result.update(args=["-sn", "-dn", *args], allow_hw_override=False)
    return result


def _notify(callback, event):
    if callback is not None:
        try:
            callback(dict(event))
        except Exception:
            pass                              # 观察者不能影响任务执行与管道排空


def _result(task):
    return {"input": task.input_path, "status": task.status,
            "output_path": task.output_path if task.status == "done" else "",
            "error": task.error, "percent": round(task.percent, 1),
            "output_size": task.output_size, "took_ms": task.took_ms}


def _cancelled_result(action):
    result = {"ok": False, "action": action, "cancelled": True}
    if action == "convert":
        result["results"] = []
    return result


def execute(request, on_event=None, cancel_event=None):
    """同步执行 JSON 请求。请求错误抛 ValueError，工具探测错误抛 RuntimeError。

    转换任务失败返回 ok=False；所有结果均可 JSON 序列化。on_event 接收 task、
    log、queue-done 事件，可能从工作线程调用，调用方应使回调线程安全。
    """
    if not isinstance(request, dict):
        raise ValueError("请求必须为 JSON 对象")
    action = request.get("action")
    if not isinstance(action, str) or action not in _FIELDS:
        raise ValueError("action 必须为 formats、capabilities、probe、test-encoder 或 convert")
    _keys(request, _FIELDS[action], "请求")
    if on_event is not None and not callable(on_event):
        raise ValueError("on_event 必须为可调用对象")
    if cancel_event is not None and not callable(getattr(cancel_event, "is_set", None)):
        raise ValueError("cancel_event 必须提供 is_set() 方法")
    is_cancelled = cancel_event.is_set if cancel_event is not None else lambda: False
    if is_cancelled():
        return _cancelled_result(action)

    if action == "capabilities":
        return {"ok": True, "action": action, "capabilities": detect_capabilities()}
    if action == "formats":
        caps = detect_capabilities()
        formats = []
        for profile in available_formats(caps):
            formats.append({**profile,
                            "video_encoders": available_encoders(profile["id"], "video", caps),
                            "audio_encoders": available_encoders(profile["id"], "audio", caps)})
        return {"ok": True, "action": action, "formats": formats}
    if action == "test-encoder":
        encoder, kind = request.get("encoder"), request.get("kind", "video")
        if not isinstance(encoder, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", encoder):
            raise ValueError("encoder 必须为编码器名称")
        if not isinstance(kind, str) or kind not in ("video", "audio", "subtitle"):
            raise ValueError("kind 必须为 video、audio 或 subtitle")
        caps = detect_capabilities()
        if encoder not in caps["encoders"] or caps["encoders"][encoder]["type"] != kind:
            raise ValueError("当前 FFmpeg 未提供所选类型的编码器")
        ok, detail = test_encoder(encoder, kind=kind)
        return {"ok": bool(ok), "action": action, "encoder": encoder, "kind": kind, "detail": detail}

    paths = _inputs(request)
    if action == "probe":
        media = []
        for path in paths:
            if is_cancelled():
                return _cancelled_result(action)
            media.append(probe_file(path))
        return _cancelled_result(action) if is_cancelled() else {"ok": True, "action": action, "media": media}

    concurrency = request.get("concurrency", 1)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 4:
        raise ValueError("concurrency 必须为 1–4 的整数")
    if "preset" in request and "options" in request:
        raise ValueError("preset 和 options 只能选择一个")
    options = _options(request)
    preset_id = request.get("preset")
    if "preset" in request and (not isinstance(preset_id, str) or not preset_id):
        raise ValueError("preset 必须为非空预设 ID")
    out_dir = None
    if "output_dir" in request:
        out_dir = _path(request["output_dir"], "输出目录")
        if os.path.exists(out_dir) and not os.path.isdir(out_dir):
            raise ValueError("output_dir 必须为目录，不能是文件")
    caps = detect_capabilities()
    infos = []
    for path in paths:
        if is_cancelled():
            return _cancelled_result(action)
        infos.append(probe_file(path))
    presets = []
    for path, info in zip(paths, infos):
        if is_cancelled():
            return _cancelled_result(action)
        if not info.get("video") and not info.get("audio"):
            raise ValueError(f"输入文件没有视频或音轨：{path}")
        try:
            preset = _preset_for(preset_id, info, caps) if preset_id else build_conversion(options, info, caps)
        except ValueError as exc:
            raise ValueError(f"{path}：{exc}") from None
        presets.append(preset)
    # 到这里全部媒体和选项才算有效；硬件显式选择须先通过实际短编码测试。
    tested = set()
    for preset in presets:
        if is_cancelled():
            return _cancelled_result(action)
        args = preset["args"]
        for index, arg in enumerate(args[:-1]):
            if arg not in ("-c:v", "-c:a"):
                continue
            encoder = args[index + 1]
            if encoder.endswith(("_nvenc", "_qsv", "_amf")) and encoder not in tested:
                ok, detail = test_encoder(encoder, kind="video" if arg == "-c:v" else "audio")
                if not ok:
                    raise RuntimeError(f"硬件编码器 {encoder} 不可用：{detail}")
                tested.add(encoder)
    if is_cancelled():
        return _cancelled_result(action)
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"无法创建输出目录：{exc}") from None
    tasks = [Task(path, preset, out_dir=out_dir, duration=info.get("duration") or 0,
                  task_id=f"api-{index}")
             for index, (path, preset, info) in enumerate(zip(paths, presets, infos))]
    done = threading.Event()

    def event_callback(event):
        _notify(on_event, event)
        if event.get("event") == "queue-done":
            done.set()

    queue = TaskQueue(on_event=event_callback,
                      on_log=lambda task_id, line: _notify(on_event, {"event": "log", "id": task_id, "line": line}),
                      concurrency_provider=lambda: concurrency)
    cancelled = False
    try:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            for task in tasks:
                task.cancel_event.set()
        queue.enqueue(tasks)
        while not done.wait(0.05):
            if not cancelled and cancel_event is not None and cancel_event.is_set():
                cancelled = True
                queue.cancel_all()
        # queue-done 在最后工作线程完全退出之前发出，等待其清理收尾。
    finally:
        queue.shutdown(timeout=10)
    results = [_result(task) for task in tasks]
    cancelled = cancelled or any(task.status == "cancelled" for task in tasks)
    return {"ok": all(task.status == "done" for task in tasks), "action": action,
            "results": results, "cancelled": cancelled}
