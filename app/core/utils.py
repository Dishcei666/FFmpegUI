# -*- coding: utf-8 -*-
"""展示用的格式化工具与滤镜路径转义。"""
import os


def escape_filter_path(path):
    """把 Windows 路径转成 ffmpeg 滤镜参数里可用的形式。

    滤镜参数有两层解析（图级 + 选项级）：单引号防图级切分，
    冒号前加反斜杠防选项级切分。路径中含单引号的场景由调用方
    用临时文件规避（本项目所有内嵌路径均为受控/临时路径）。
    """
    p = os.path.abspath(path).replace("\\", "/")
    return "'" + p.replace(":", r"\:") + "'"


def fmt_duration(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_bitrate(bps):
    bps = int(bps or 0)
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    if bps:
        return f"{bps / 1000:.0f} kbps"
    return ""


def fmt_fps(fps):
    fps = float(fps or 0)
    if not fps:
        return ""
    text = f"{fps:.2f}".rstrip("0").rstrip(".")
    return text


def fmt_took(ms):
    sec = (ms or 0) / 1000
    if sec < 60:
        return f"{sec:.1f}s"
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"
