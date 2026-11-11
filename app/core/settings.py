# -*- coding: utf-8 -*-
"""应用设置：JSON 持久化到 %LOCALAPPDATA%\\FFmpegUI\\settings.json。

各键含义：
- ffmpeg_path / ffprobe_path : 为空表示用 PATH 中的默认命令
- out_dir   : 默认输出目录（空 = 与源文件相同）
- concurrency : 任务并发数 1-4
- prefer_gpu / hw_encoder : 优先硬件编码及选定的编码器
"""
import json
import os
import tempfile
import threading

_DEFAULTS = {
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "out_dir": "",
    "concurrency": 1,
    "prefer_gpu": False,
    "hw_encoder": "",
}

_ENCODERS = ("", "h264_nvenc", "h264_qsv", "h264_amf")
_lock = threading.RLock()
_data = dict(_DEFAULTS)


def _settings_path():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "FFmpegUI", "settings.json")


def _valid_value(key, value):
    if key == "concurrency":
        return type(value) is int and 1 <= value <= 4
    if key == "prefer_gpu":
        return type(value) is bool
    if key == "hw_encoder":
        return isinstance(value, str) and value in _ENCODERS
    return isinstance(value, str) and "\x00" not in value


def _validated(stored):
    """仅接受已知键及正确类型；单个损坏字段不会影响其他有效设置。"""
    data = dict(_DEFAULTS)
    if isinstance(stored, dict):
        for key in _DEFAULTS:
            if key in stored and _valid_value(key, stored[key]):
                data[key] = stored[key]
    return data


def load():
    global _data
    with _lock:
        try:
            with open(_settings_path(), encoding="utf-8-sig") as f:
                data = _validated(json.load(f))
        except (OSError, ValueError):
            data = dict(_DEFAULTS)
        _data = data
        return dict(_data)


def _write(data):
    """调用方持锁；同目录写完整后原子替换，失败时保留原配置。"""
    path = _settings_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=directory, prefix=".settings-", suffix=".tmp") as f:
            temp_path = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def save():
    global _data
    with _lock:
        data = _validated(_data)
        _write(data)
        _data = data


def get(key):
    with _lock:
        return _data.get(key, _DEFAULTS.get(key))


def set(key, value):                            # noqa: A001 - 与模块语义一致
    global _data
    if key not in _DEFAULTS:
        raise KeyError(key)
    if not _valid_value(key, value):
        raise ValueError(f"无效的设置值：{key}")
    with _lock:
        data = dict(_data)
        data[key] = value
        _write(data)
        _data = data


def reset():
    global _data
    with _lock:
        data = dict(_DEFAULTS)
        _write(data)
        _data = data


def ffmpeg():
    return get("ffmpeg_path") or os.environ.get("FFMPEG_PATH") or "ffmpeg"


def ffprobe():
    return get("ffprobe_path") or os.environ.get("FFPROBE_PATH") or "ffprobe"


load()
