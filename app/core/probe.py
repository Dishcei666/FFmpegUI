# -*- coding: utf-8 -*-
"""ffprobe 媒体信息探测。

纯标准库实现（不依赖 Qt），便于在界面之外独立测试。
"""
import json
import math
import os
import subprocess

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def probe_file(path):
    """探测一个媒体文件，返回信息 dict；失败抛出 RuntimeError。

    阻塞调用（通常 < 100ms），批量探测请在后台线程进行。
    """
    path = os.fspath(path)
    cmd = [settings.ffprobe(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", "-i", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30,
                              creationflags=_NO_WINDOW)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 ffprobe，请确认已安装 FFmpeg 并检查可执行文件路径") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe 探测超时（30 秒）") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"无法启动 ffprobe：{exc}") from exc

    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "ffprobe 无法解析该文件")
    try:
        data = json.loads(proc.stdout.decode("utf-8-sig"))
    except ValueError as exc:
        raise RuntimeError("ffprobe 输出解析失败") from exc
    return _parse(data, path)


def _parse(data, path):
    if not isinstance(data, dict):
        raise RuntimeError("ffprobe 输出结构无效")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    if (not isinstance(fmt, dict) or not isinstance(streams, list)
            or any(not isinstance(s, dict) for s in streams)):
        raise RuntimeError("ffprobe 输出结构无效")
    # 专辑封面属于 attached_pic，不作为实际视频轨用于剪辑和拼接。
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for cand in (fmt.get("duration"), (video or {}).get("duration"),
                 (audio or {}).get("duration")):
        try:
            if cand:
                seconds = float(cand)
                if math.isfinite(seconds):
                    duration = max(duration, seconds)
        except (TypeError, ValueError, OverflowError):
            pass

    def _int(v):
        try:
            return int(v or 0)
        except (TypeError, ValueError, OverflowError):
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
            "fps": (_fps(video.get("avg_frame_rate"))
                    or _fps(video.get("r_frame_rate"))),
            "frame_rate": (video.get("avg_frame_rate")
                           if _fps(video.get("avg_frame_rate"))
                           else video.get("r_frame_rate", "")),
            "pix_fmt": video.get("pix_fmt", ""),
            "profile": video.get("profile", ""),
            "level": video.get("level"),
            "time_base": video.get("time_base", ""),
        } if video else None,
        "audio": {
            "codec": audio.get("codec_name", "?"),
            "sample_rate": _int(audio.get("sample_rate")),
            "channels": _int(audio.get("channels")),
            "channel_layout": audio.get("channel_layout", ""),
            "sample_fmt": audio.get("sample_fmt", ""),
            "time_base": audio.get("time_base", ""),
        } if audio else None,
    }


def _fps(rate):
    """把 avg_frame_rate 的 "30000/1001" 形式换算为浮点帧率。"""
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            result = float(num) / float(den) if float(den) else 0.0
        else:
            result = float(rate)
        return round(result, 3) if math.isfinite(result) and result > 0 else 0.0
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return 0.0
