# -*- coding: utf-8 -*-
"""查询当前 FFmpeg 的封装器和编码器，独立于 Qt。

detect_capabilities/test_encoder 均为阻塞调用，界面应在后台线程中调用。
编码器出现在列表中只表示此 FFmpeg 编译时包含它；test_encoder 才会
实际尝试一次短编码，其成功也不保证所有输入参数和分辨率均受支持。
"""
from collections import OrderedDict
from copy import deepcopy
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_TIMEOUT = 20
_CACHE_LIMIT = 8
_cache = OrderedDict()
_cache_lock = threading.Lock()
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_ENCODER_LINE = re.compile(
    r"^\s*([VAS])[A-Z.]{5,7}\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)\s+(.+?)\s*$")
_CODEC = re.compile(r"\(codec\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)\)")
_KINDS = {"V": "video", "A": "audio", "S": "subtitle"}


def _command_info(command):
    try:
        requested = settings.ffmpeg() if command is None else os.fspath(command)
    except TypeError as exc:
        raise RuntimeError("FFmpeg 可执行文件路径无效") from exc
    if not isinstance(requested, str) or not requested.strip() or "\x00" in requested:
        raise RuntimeError("FFmpeg 可执行文件路径无效")
    try:
        resolved = shutil.which(requested)
        if resolved is None:
            raise RuntimeError(f"未找到 FFmpeg 可执行文件：{requested}")
        resolved = os.path.realpath(os.path.abspath(resolved))
        stat = os.stat(resolved)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"无法访问 FFmpeg 可执行文件：{exc}") from exc
    key = (os.path.normcase(resolved), stat.st_mtime_ns, stat.st_size)
    return requested, resolved, key


def _decode(output):
    if isinstance(output, str):
        return output
    return (output or b"").decode("utf-8", "replace")


def _run(command, args, operation):
    try:
        proc = subprocess.run(
            [command, "-hide_banner", "-nostdin", *args],
            capture_output=True, timeout=_TIMEOUT, creationflags=_NO_WINDOW)
    except FileNotFoundError as exc:
        raise RuntimeError(f"未找到 FFmpeg 可执行文件：{command}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg {operation}超时（{_TIMEOUT} 秒）") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"无法启动 FFmpeg 进行{operation}：{exc}") from exc
    stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
    if proc.returncode != 0:
        lines = (stderr or stdout).strip().splitlines()
        if len(lines) > 8:
            lines = [*lines[:4], "…", *lines[-4:]]
        # 硬件缺驱动等根因通常在开头，末尾仅剩通用的“编码器启动失败”。
        detail = "\n".join(lines)[:2000]
        message = f"FFmpeg {operation}失败（退出代码 {proc.returncode}）"
        raise RuntimeError(f"{message}：{detail}" if detail else message)
    return "\n".join(part for part in (stdout, stderr) if part)


def _parse_muxers(output):
    muxers = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        flags, names, description = parts
        if "E" not in flags or any(flag not in "DE.d" for flag in flags):
            continue
        for name in names.split(","):
            if _NAME.fullmatch(name):
                muxers[name] = description.strip()
    return muxers


def _parse_encoders(output):
    encoders = {}
    for line in output.splitlines():
        match = _ENCODER_LINE.match(line)
        if not match:
            continue
        kind, name, description = match.groups()
        codec = _CODEC.search(description)
        encoders[name] = {
            "type": _KINDS[kind],
            "codec": codec.group(1) if codec else name,
            "description": description,
        }
    return encoders


def detect_capabilities(command=None):
    """返回当前 FFmpeg 能力字典；失败抛出带具体原因的 RuntimeError。

    command 保留调用方传入的命令；resolved_command 为实际执行的绝对路径。
    缓存按解析路径、修改时间和大小区分，换工具后不会沿用旧工具的结果。
    返回副本，调用方可以安全地过滤列表。
    """
    requested, resolved, key = _command_info(command)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            result = deepcopy(cached)
            result["command"] = requested
            return result

    version_output = _run(resolved, ["-version"], "版本检测")
    version = next((line.strip() for line in version_output.splitlines()
                    if line.startswith("ffmpeg version ")), "")
    if not version:
        raise RuntimeError("无法识别 FFmpeg 版本，请检查所选可执行文件")
    muxers = _parse_muxers(_run(resolved, ["-muxers"], "封装器检测"))
    encoders = _parse_encoders(_run(resolved, ["-encoders"], "编码器检测"))
    if not muxers or not encoders:
        raise RuntimeError("FFmpeg 未返回可识别的封装器或编码器列表")
    result = {"command": requested, "resolved_command": resolved,
              "version": version, "muxers": muxers, "encoders": encoders}
    with _cache_lock:
        _cache[key] = deepcopy(result)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return result


def invalidate_cache(command=None):
    """清空缓存，或仅清理指定命令解析到的可执行文件。"""
    if command is None:
        with _cache_lock:
            _cache.clear()
        return
    try:
        _, _, key = _command_info(command)
    except RuntimeError:
        return
    with _cache_lock:
        for cached_key in list(_cache):
            if cached_key[0] == key[0]:
                del _cache[cached_key]


def test_encoder(encoder, kind="video", command=None):
    """只测试选中的编码器，返回 (成功, 说明)，不会尝试其他编码器。

    测试用 0.2 秒的合成视频/音频或临时字幕，通过 null 封装器丢弃输出。
    硬件编码器仍须具有当前驱动和设备；不主动初始化特定设备或下载组件。
    """
    if not isinstance(encoder, str) or not _NAME.fullmatch(encoder):
        return False, "编码器名称无效"
    if kind not in ("video", "audio", "subtitle"):
        return False, f"不支持的编码器类型：{kind}"
    try:
        _, resolved, _ = _command_info(command)
        if kind == "video":
            args = ["-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=320x240:r=30:d=0.2", "-an",
                    "-c:v", encoder, "-frames:v", "6", "-f", "null", "-"]
            _run(resolved, args, "视频编码测试")
        elif kind == "audio":
            args = ["-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo:d=0.2", "-vn",
                    "-c:a", encoder, "-t", "0.2", "-f", "null", "-"]
            _run(resolved, args, "音频编码测试")
        else:
            with tempfile.TemporaryDirectory(prefix="ffgui-encoder-test-") as temp:
                source = Path(temp, "sample.srt")
                source.write_text("1\n00:00:00,000 --> 00:00:00,200\nTest\n",
                                  encoding="utf-8")
                args = ["-v", "error", "-i", str(source), "-map", "0:s:0",
                        "-c:s", encoder, "-f", "null", "-"]
                _run(resolved, args, "字幕编码测试")
    except (RuntimeError, OSError) as exc:
        return False, str(exc)
    return True, f"{encoder} 短编码测试通过"
