# -*- coding: utf-8 -*-
"""ffprobe 媒体信息探测。

纯标准库实现（不依赖 Qt），便于在界面之外独立测试。
"""
import json
import os
import subprocess

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def probe_file(path):
    """探测一个媒体文件，返回信息 dict；失败抛出 RuntimeError。

    阻塞调用（通常 < 100ms），批量探测请在后台线程进行。
    """
    cmd = [settings.ffprobe(), "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30,
                              creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError("未找到 ffprobe，请确认已安装 FFmpeg 并加入 PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe 探测超时")

    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "ffprobe 无法解析该文件")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "ignore"))
    except ValueError:
        raise RuntimeError("ffprobe 输出解析失败")
    return _parse(data, path)


def _parse(data, path):
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for cand in (fmt.get("duration"), (video or {}).get("duration"),
                 (audio or {}).get("duration")):
        try:
            if cand:
                duration = max(duration, float(cand))
        except (TypeError, ValueError):
            pass

    def _int(v):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "path": path,
        "name": os.path.basename(path),
        "container": (fmt.get("format_long_name") or fmt.get("format_name")
                      or "").split(",")[0],
        "size": _int(fmt.get("size")),
        "duration": duration,
        "bitrate": _int(fmt.get("bit_rate")),
        "video": {
            "codec": video.get("codec_name", "?"),
            "w": _int(video.get("width")),
            "h": _int(video.get("height")),
            "fps": _fps(video.get("avg_frame_rate")),
        } if video else None,
        "audio": {
            "codec": audio.get("codec_name", "?"),
            "sample_rate": _int(audio.get("sample_rate")),
            "channels": _int(audio.get("channels")),
        } if audio else None,
    }


def _fps(rate):
    """把 avg_frame_rate 的 "30000/1001" 形式换算为浮点帧率。"""
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return round(float(num) / float(den), 3) if float(den) else 0.0
        return float(rate)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
