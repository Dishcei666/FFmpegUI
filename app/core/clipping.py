# -*- coding: utf-8 -*-
"""剪辑模块：命令构建（片段导出 / 合并导出）与辅助探测（关键帧、黑边）。

纯函数 + 子进程，不依赖 Qt。返回的 args 配合 runner.Task 的
input_args / preset["args"] / preset["ext"] 使用。
"""
import os
import re
import subprocess

from .presets import get_preset

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH", "ffprobe")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# 精确剪切模式下可选的编码预设（取自 presets.py）
ENCODE_PRESET_IDS = ["x264-hq", "x264-compress", "scale-1080", "scale-720"]

ROTATE_VF = {90: "transpose=1", 180: "transpose=1,transpose=1",
             270: "transpose=2", "mirror": "hflip"}


def _f(t):
    return f"{float(t):.3f}"


def build_vf(transform):
    """把裁切/旋转/变速拼成视频滤镜链（不含音频处理）。"""
    parts = []
    if transform.get("crop"):
        parts.append(f"crop={transform['crop']}")
    rotate = transform.get("rotate")
    if rotate:
        parts.append(ROTATE_VF[rotate] if isinstance(rotate, str)
                     else ROTATE_VF.get(int(rotate), ""))
    speed = float(transform.get("speed") or 1.0)
    if abs(speed - 1.0) > 1e-6:
        parts.append(f"setpts={1.0 / speed:.5f}*PTS")
    return ",".join(p for p in parts if p)


def _has_speed(transform):
    return abs(float(transform.get("speed") or 1.0) - 1.0) > 1e-6


def build_segment(src, start, end, mode, encode_preset_id, transform):
    """构建单个片段任务。返回 (input_args, out_args, ext, label)。

    - 无损快切：输入定位 + 流复制，切点对齐关键帧，速度极快；
    - 精确剪切：输入定位 + 重编码，逐帧准确，可叠加裁切/旋转/变速。
    """
    transform = transform or {}
    duration = max(0.05, end - start)
    if mode == "lossless":
        ext = os.path.splitext(src)[1][1:].lower() or "mp4"
        inp = ["-ss", _f(start), "-t", _f(duration)]
        out = ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
        return inp, out, ext, "无损快切"

    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    inp = ["-ss", _f(start), "-t", _f(duration)]
    vf = build_vf(transform)
    speed = float(transform.get("speed") or 1.0)

    if _has_speed(transform) and transform.get("has_audio"):
        # 变速 + 有音轨：音视频分别处理后重新映射
        tempo = min(max(speed, 0.5), 2.0)
        fc = (f"[0:v]{vf}[v];[0:a]atempo={tempo:.4f}[a]"
              if vf else f"[0:v]setpts={1.0 / speed:.5f}*PTS[v];[0:a]atempo={tempo:.4f}[a]")
        out = ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
               *preset["args"]]
    else:
        out = (["-vf", vf] if vf else []) + list(preset["args"])
    return inp, out, "mp4", preset["name"]


def build_merge(src, segments, encode_preset_id, transform):
    """构建"多段合并为一个文件"任务（单条 ffmpeg 命令，多输入 + concat）。

    返回 (input_args, out_args, ext, label, total_duration)，input_args 已含全部 -i。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    inp = []
    total = 0.0
    for start, end in segments:
        duration = max(0.05, end - start)
        total += duration
        inp += ["-ss", _f(start), "-t", _f(duration), "-i", src]

    n = len(segments)
    has_audio = bool(transform.get("has_audio"))
    vf = build_vf(transform)
    speed = float(transform.get("speed") or 1.0)

    v_labels = "".join(f"[{i}:v]" for i in range(n))
    if has_audio:
        labels = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        graph = f"{labels}concat=n={n}:v=1:a=1[cv][ca]"
        v_map, a_map = "[cv]", "[ca]"
    else:
        graph = f"{v_labels}concat=n={n}:v=1[cv]"
        v_map, a_map = "[cv]", None

    if vf:
        graph += f";[{v_map[1:-1]}]{vf}[vout]"
        v_map = "[vout]"
    if has_audio and _has_speed(transform):
        tempo = min(max(speed, 0.5), 2.0)
        graph += f";[ca]atempo={tempo:.4f}[aout]"
        a_map = "[aout]"

    out = ["-filter_complex", graph, "-map", v_map]
    if a_map:
        out += ["-map", a_map]
    out += list(preset["args"])
    return inp, out, "mp4", preset["name"], total


def probe_keyframes(path, timeout=30):
    """返回关键帧时间点列表（秒）。文件大或失败时抛异常/超时，UI 应降级。"""
    cmd = [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError("ffprobe 关键帧读取失败")
    times = []
    for line in proc.stdout.decode("utf-8", "ignore").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                t = float(parts[0])
                if t >= 0:
                    times.append(t)
            except ValueError:
                pass
    return times


def detect_crop(path, sample_seconds=12):
    """用 cropdetect 检测黑边，返回 'w:h:x:y' 字符串；检测不到返回 None。"""
    cmd = [FFMPEG, "-hide_banner", "-t", str(sample_seconds), "-i", path,
           "-vf", "cropdetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120,
                          creationflags=_NO_WINDOW)
    text = proc.stderr.decode("utf-8", "ignore")
    found = re.findall(r"crop=(\d+:\d+:\d+:\d+)", text)
    return found[-1] if found else None
