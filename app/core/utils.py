# -*- coding: utf-8 -*-
"""展示用的格式化工具。"""


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
