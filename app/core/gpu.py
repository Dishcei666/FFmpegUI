# -*- coding: utf-8 -*-
"""硬件编码（GPU 加速）：可用性检测 + 参数替换。

检测方式：对每个候选编码器跑一次 0.2 秒的 tiny 编码——
只看 -encoders 列表会误报（有驱动才真正可用）。
"""
import os
import subprocess

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# 检测优先级：NVIDIA > Intel > AMD
CANDIDATES = ["h264_nvenc", "h264_qsv", "h264_amf"]

LABELS = {
    "h264_nvenc": "NVIDIA NVENC",
    "h264_qsv": "Intel 核显 QSV",
    "h264_amf": "AMD AMF",
}


def _test_encoder(encoder):
    cmd = [settings.ffmpeg(), "-hide_banner",
           "-f", "lavfi", "-i", "nullsrc=s=128x72:d=0.2",
           "-c:v", encoder, "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20,
                              creationflags=_NO_WINDOW)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_all(on_each=None):
    """逐个检测候选编码器，返回 {encoder: bool}。阻塞，建议在线程中调用。

    on_each(encoder, ok) 在每个结果出来时回调（用于界面渐进展示）。
    """
    results = {}
    for enc in CANDIDATES:
        ok = _test_encoder(enc)
        results[enc] = ok
        if on_each:
            on_each(enc, ok)
    return results


def pick_default(results):
    """按优先级选第一个可用的编码器，没有则返回空串。"""
    for enc in CANDIDATES:
        if results.get(enc):
            return enc
    return ""


def apply_hw_args(args, encoder):
    """把 libx264 参数替换为硬件编码参数；不含 libx264 原样返回。"""
    out = list(args)
    replaced = False
    for i in range(len(out) - 1):
        if out[i] == "-c:v" and out[i + 1] == "libx264":
            out[i + 1] = encoder
            replaced = True
            break
    if not replaced:
        return list(args)

    def drop_flag(flag):
        for j in range(len(out) - 1):
            if out[j] == flag:
                del out[j:j + 2]
                break

    def rename_flag(old, new):
        for j in range(len(out) - 1):
            if out[j] == old:
                out[j] = new
                break

    if encoder == "h264_nvenc":
        rename_flag("-crf", "-cq")          # nvenc 用 -cq 表达质量
        drop_flag("-preset")                # 新版 nvenc 只认 p1-p7
        out += ["-preset", "p5"]
    elif encoder == "h264_qsv":
        rename_flag("-crf", "-global_quality")
    elif encoder == "h264_amf":
        drop_flag("-crf")
        drop_flag("-preset")                # AMF 预设名不同，用质量档
        out += ["-quality", "quality"]
    return out
