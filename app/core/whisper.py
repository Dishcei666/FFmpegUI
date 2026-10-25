# -*- coding: utf-8 -*-
"""whisper AI 字幕：模型管理（下载/定位）+ 视频语音识别生成 SRT。

依赖本机 ffmpeg 的 whisper 滤镜（gyan.dev full build 已启用），
模型来自 whisper.cpp 的 ggml 格式，经 hf-mirror 镜像下载。
"""
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_URL = "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/{file}"

MODELS = {
    "base": {"file": "ggml-base.bin", "label": "base · 148MB · 速度快"},
    "small": {"file": "ggml-small.bin", "label": "small · 445MB · 更准（推荐中文）"},
}


def models_dir():
    path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "FFmpegUI", "models")
    os.makedirs(path, exist_ok=True)
    return path


def model_path(name):
    return os.path.join(models_dir(), MODELS[name]["file"])


def is_downloaded(name):
    """模型存在且大小超过预期 98% 才算完整（防半截文件）。"""
    path = model_path(name)
    if not os.path.exists(path):
        return False
    try:
        import urllib.request  # noqa: F401 - 只为说明依赖，大小阈值见下
        size = os.path.getsize(path)
        expect = 140_000_000 if name == "base" else 440_000_000
        return size >= expect
    except OSError:
        return False


def download_model(name, on_progress=None):
    """下载模型（阻塞，建议在线程中调用）。on_progress(已下载字节, 总字节)。"""
    url = _URL.format(file=MODELS[name]["file"])
    dest = model_path(name)
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "FFmpegUI"})
    with urllib.request.urlopen(req, timeout=60) as resp, \
            open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        last_cb = 0.0
        while True:
            chunk = resp.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            import time
            now = time.time()
            if on_progress and (now - last_cb >= 0.2 or downloaded == total):
                last_cb = now
                on_progress(downloaded, total)
    if os.path.exists(dest):
        os.remove(dest)
    os.replace(part, dest)
    return dest


def transcribe(media_path, model_name, language, srt_out, duration=0.0,
               on_progress=None, on_log=None):
    """识别 media_path 的语音并写出 SRT 到 srt_out。阻塞调用。

    返回 srt_out；失败抛 RuntimeError。
    """
    model = model_path(model_name)
    if not os.path.exists(model):
        raise RuntimeError(f"模型未下载：{MODELS[model_name]['label']}")
    lang = language or "auto"

    # destination 用 ASCII 临时路径规避滤镜串转义，完成后挪到目标
    fd, tmp_srt = tempfile.mkstemp(suffix=".srt", prefix="ffgui-whisper-")
    os.close(fd)
    os.remove(tmp_srt)

    from .utils import escape_filter_path
    af = (f"whisper=model={escape_filter_path(model)}"
          f":destination={escape_filter_path(tmp_srt)}"
          f":format=srt:language={lang}")
    cmd = [settings.ffmpeg(), "-hide_banner", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats",
           "-i", media_path, "-af", af, "-f", "null", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError("未找到 ffmpeg")

    err_tail = []

    def drain_err():
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "ignore").strip()
            if line:
                err_tail.append(line)
                if len(err_tail) > 60:
                    err_tail.pop(0)
                if on_log:
                    on_log(line)

    import threading
    t_err = threading.Thread(target=drain_err, daemon=True)
    t_err.start()

    buf = ""
    while True:
        chunk = proc.stdout.read(128)
        if not chunk:
            break
        buf += chunk.decode("utf-8", "ignore")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if line.startswith("out_time_us=") and duration > 0 and on_progress:
                try:
                    sec = int(line.split("=", 1)[1]) / 1e6
                    on_progress(min(99.5, sec / duration * 100.0))
                except ValueError:
                    pass
    proc.wait()
    t_err.join(timeout=2)

    if proc.returncode != 0 or not os.path.exists(tmp_srt):
        tail = "\n".join(err_tail[-6:])
        raise RuntimeError(tail or f"识别失败（退出码 {proc.returncode}）")

    content = _normalize_srt(open(tmp_srt, encoding="utf-8").read())
    try:
        os.remove(tmp_srt)
    except OSError:
        pass
    with open(srt_out, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(content)
    if on_progress:
        on_progress(100.0)
    return srt_out


def _normalize_srt(text):
    """识别输出从 0 开始编号，重排为标准的 1 开始。"""
    blocks = re.split(r"\n\s*\n", text.strip())
    out = []
    index = 0
    for block in blocks:
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) >= 2:
            index += 1
            out.append(f"{index}\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n" if out else ""
