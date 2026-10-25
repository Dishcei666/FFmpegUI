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
import threading

_DEFAULTS = {
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "out_dir": "",
    "concurrency": 1,
    "prefer_gpu": False,
    "hw_encoder": "",
}

_lock = threading.Lock()
_data = dict(_DEFAULTS)


def _settings_path():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "FFmpegUI", "settings.json")


def load():
    global _data
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            stored = json.load(f)
        data = dict(_DEFAULTS)
        data.update({k: stored[k] for k in _DEFAULTS if k in stored})
        _data = data
    except (OSError, ValueError):
        _data = dict(_DEFAULTS)
    return _data


def save():
    with _lock:
        os.makedirs(os.path.dirname(_settings_path()), exist_ok=True)
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)


def get(key):
    return _data.get(key, _DEFAULTS.get(key))


def set(key, value):                            # noqa: A001 - 与模块语义一致
    _data[key] = value
    save()


def reset():
    global _data
    _data = dict(_DEFAULTS)
    save()


def ffmpeg():
    return _data["ffmpeg_path"] or os.environ.get("FFMPEG_PATH", "ffmpeg")


def ffprobe():
    return _data["ffprobe_path"] or os.environ.get("FFPROBE_PATH", "ffprobe")


load()
