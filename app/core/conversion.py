# -*- coding: utf-8 -*-
"""高级转换的受控格式/编码器目录与参数构建；不依赖 Qt，不执行子进程。"""
import math


def _profile(id_, label, ext, muxer, kind, video=(), audio=()):
    return {"id": id_, "label": label, "ext": ext, "muxer": muxer,
            "kind": kind, "video_codecs": tuple(video), "audio_codecs": tuple(audio)}


FORMAT_PROFILES = [
    _profile("mp4", "MP4", "mp4", "mp4", "video",
             ("h264", "hevc", "av1", "mpeg4"), ("aac", "mp3", "alac", "ac3")),
    _profile("mkv", "Matroska (MKV)", "mkv", "matroska", "video",
             ("h264", "hevc", "av1", "vp8", "vp9", "prores", "ffv1", "mpeg4", "mpeg2video", "mjpeg"),
             ("aac", "mp3", "flac", "opus", "vorbis", "pcm_s16le", "pcm_s24le", "ac3", "alac")),
    _profile("mov", "QuickTime (MOV)", "mov", "mov", "video",
             ("h264", "hevc", "prores", "mpeg4", "mjpeg"),
             ("aac", "alac", "pcm_s16le", "pcm_s24le")),
    _profile("webm", "WebM", "webm", "webm", "video",
             ("vp8", "vp9", "av1"), ("opus", "vorbis")),
    _profile("avi", "AVI", "avi", "avi", "video",
             ("mpeg4", "mjpeg", "h264", "ffv1"), ("mp3", "pcm_s16le", "pcm_s24le", "ac3")),
    _profile("ts", "MPEG Transport Stream (TS)", "ts", "mpegts", "video",
             ("h264", "hevc", "mpeg2video"), ("aac", "mp2", "ac3")),
    _profile("flv", "Flash Video (FLV)", "flv", "flv", "video",
             ("h264", "flv1"), ("aac", "mp3")),
    _profile("mpg", "MPEG Program Stream (MPG)", "mpg", "mpeg", "video",
             ("mpeg2video", "mpeg1video"), ("mp2", "ac3", "mp3")),
    _profile("mp3", "MP3 音频", "mp3", "mp3", "audio", audio=("mp3",)),
    _profile("m4a", "M4A 音频", "m4a", "ipod", "audio", audio=("aac", "alac")),
    _profile("aac", "AAC (ADTS) 音频", "aac", "adts", "audio", audio=("aac",)),
    _profile("flac", "FLAC 无损音频", "flac", "flac", "audio", audio=("flac",)),
    _profile("wav", "WAV 音频", "wav", "wav", "audio",
             audio=("pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le")),
    _profile("ogg", "Ogg 音频", "ogg", "ogg", "audio", audio=("vorbis", "flac")),
    _profile("opus", "Opus 音频", "opus", "opus", "audio", audio=("opus",)),
    _profile("aiff", "AIFF 音频", "aiff", "aiff", "audio", audio=("pcm_s16be", "pcm_s24be")),
    _profile("gif", "GIF 动图", "gif", "gif", "image", ("gif",)),
    _profile("apng", "APNG 动图", "apng", "apng", "image", ("apng",)),
    _profile("webp", "WebP 动图", "webp", "webp", "image", ("webp",)),
]

# 只开放已知参数语义的编码器；本机的编译能力再做一次交集。
_VIDEO_ENCODERS = [
    ("libx264", "H.264 · x264", "h264"),
    ("h264_nvenc", "H.264 · NVIDIA NVENC", "h264"),
    ("h264_qsv", "H.264 · Intel QSV", "h264"),
    ("h264_amf", "H.264 · AMD AMF", "h264"),
    ("libx265", "H.265 / HEVC · x265", "hevc"),
    ("hevc_nvenc", "HEVC · NVIDIA NVENC", "hevc"),
    ("hevc_qsv", "HEVC · Intel QSV", "hevc"),
    ("hevc_amf", "HEVC · AMD AMF", "hevc"),
    ("libsvtav1", "AV1 · SVT-AV1", "av1"),
    ("libaom-av1", "AV1 · libaom", "av1"),
    ("librav1e", "AV1 · rav1e", "av1"),
    ("av1_nvenc", "AV1 · NVIDIA NVENC", "av1"),
    ("av1_qsv", "AV1 · Intel QSV", "av1"),
    ("av1_amf", "AV1 · AMD AMF", "av1"),
    ("libvpx-vp9", "VP9 · libvpx", "vp9"),
    ("vp9_qsv", "VP9 · Intel QSV", "vp9"),
    ("libvpx", "VP8 · libvpx", "vp8"),
    ("prores_ks", "Apple ProRes · prores_ks", "prores"),
    ("prores_aw", "Apple ProRes · prores_aw", "prores"),
    ("prores", "Apple ProRes", "prores"),
    ("ffv1", "FFV1 无损", "ffv1"),
    ("mpeg4", "MPEG-4 Part 2", "mpeg4"),
    ("mpeg2video", "MPEG-2 Video", "mpeg2video"),
    ("mpeg1video", "MPEG-1 Video", "mpeg1video"),
    ("mjpeg", "Motion JPEG", "mjpeg"),
    ("flv", "FLV / Sorenson Spark", "flv1"),
    ("gif", "GIF 调色板", "gif"),
    ("apng", "APNG 无损", "apng"),
    ("libwebp_anim", "WebP 动图", "webp"),
    ("libwebp", "WebP", "webp"),
]
_AUDIO_ENCODERS = [
    ("aac", "AAC", "aac"), ("libfdk_aac", "AAC · Fraunhofer", "aac"),
    ("libmp3lame", "MP3 · LAME", "mp3"), ("libshine", "MP3 · Shine", "mp3"),
    ("libopus", "Opus", "opus"), ("libvorbis", "Vorbis", "vorbis"),
    ("flac", "FLAC 无损", "flac"), ("alac", "Apple Lossless", "alac"),
    ("ac3", "Dolby AC-3", "ac3"), ("mp2", "MPEG Audio Layer II", "mp2"),
    ("pcm_s16le", "PCM 16-bit LE", "pcm_s16le"),
    ("pcm_s24le", "PCM 24-bit LE", "pcm_s24le"),
    ("pcm_s32le", "PCM 32-bit LE", "pcm_s32le"),
    ("pcm_f32le", "PCM 32-bit 浮点", "pcm_f32le"),
    ("pcm_s16be", "PCM 16-bit BE", "pcm_s16be"),
    ("pcm_s24be", "PCM 24-bit BE", "pcm_s24be"),
]
_HARDWARE_SUFFIXES = ("_nvenc", "_qsv", "_amf")
_LOSSLESS_AUDIO = {"flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
                   "pcm_f32le", "pcm_s16be", "pcm_s24be"}


def _get_profile(profile_id):
    profile = next((p for p in FORMAT_PROFILES if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError("请选择支持的输出格式")
    return profile


def available_formats(capabilities):
    """返回本机 FFmpeg 可封装的受控格式。"""
    muxers = (capabilities or {}).get("muxers") or {}
    return [dict(p) for p in FORMAT_PROFILES if p["muxer"] in muxers]


def available_encoders(profile_id, kind, capabilities):
    """返回容器允许且本机已编译的候选，硬件仍需独立实测驱动。"""
    profile = _get_profile(profile_id)
    if kind not in ("video", "audio"):
        raise ValueError("编码器类型必须为 video 或 audio")
    codecs = profile[f"{kind}_codecs"]
    result = []
    encoders = (capabilities or {}).get("encoders") or {}
    for name, label, codec in _VIDEO_ENCODERS if kind == "video" else _AUDIO_ENCODERS:
        metadata = encoders.get(name)
        if codec in codecs and metadata and metadata.get("type") == kind:
            result.append({"id": name, "label": label, "codec": codec,
                           "hardware": name.endswith(_HARDWARE_SUFFIXES)})
    if codecs:
        result.append({"id": "copy", "label": "复制原始流（不重编码）", "codec": "copy", "hardware": False})
    result.append({"id": "none", "label": "禁用视频" if kind == "video" else "禁用音频",
                   "codec": "none", "hardware": False})
    return result


def _number(options, key, default, label, minimum, maximum, integer=True):
    value = options.get(key, default)
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if (not math.isfinite(number) or number < minimum or number > maximum
                or (integer and number != int(number))):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label}必须在 {minimum}–{maximum} 范围内"
                         + ("，且为整数" if integer else "")) from None
    return int(number) if integer else number


def _select_encoder(options, key, kind, profile, capabilities):
    candidates = available_encoders(profile["id"], kind, capabilities)
    default = next((c["id"] for c in candidates if c["id"] not in ("copy", "none")
                    and not c["hardware"]), "none")
    name = options.get(key) or default
    selected = next((c for c in candidates if c["id"] == name), None)
    if selected is None:
        label = "视频" if kind == "video" else "音频"
        raise ValueError(f"所选{label}编码器不受该容器支持，或当前 FFmpeg 未提供该编码器")
    return selected


def _video_args(encoder, quality, bitrate):
    name, codec = encoder["id"], encoder["codec"]
    args = ["-c:v", name]
    if name == "copy":
        return args
    if codec in ("prores", "ffv1", "gif", "apng", "webp") and bitrate:
        raise ValueError("该视频编码器使用质量或无损模式，请将视频码率设为 0")
    if name.endswith("_nvenc"):
        args += ["-preset", "p5"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
    elif name.endswith("_qsv"):
        args += ["-preset", "medium"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-global_quality", str(quality)]
    elif name.endswith("_amf"):
        qp = round(quality * 255 / 51) if codec == "av1" else quality
        args += ["-quality", "quality"]
        args += (["-rc", "vbr_peak", "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate * 2}k"]
                 if bitrate else ["-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp)])
    elif name in ("libx264", "libx265"):
        args += ["-preset", "medium"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-crf", str(quality)]
    elif name == "libsvtav1":
        args += ["-preset", "8"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-crf", str(quality)]
    elif name == "libaom-av1":
        args += ["-cpu-used", "6"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-crf", str(quality), "-b:v", "0"]
    elif name == "librav1e":
        args += ["-speed", "6"]
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-qp", str(round(quality * 255 / 51))]
    elif codec in ("vp8", "vp9"):
        args += ["-deadline", "good", "-cpu-used", "4"]
        args += (["-b:v", f"{bitrate}k"] if bitrate else
                 ["-crf", str(max(4, quality) if codec == "vp8" else quality),
                  "-b:v", "1000k" if codec == "vp8" else "0"])
    elif codec == "prores":
        args += ["-profile:v", "3", "-q:v", str(max(1, round(quality * 31 / 51) + 1))]
    elif codec == "ffv1":
        args += ["-level", "3", "-coder", "1", "-context", "1", "-g", "1"]
    elif codec == "webp":
        args += ["-quality", str(100 - round(quality * 100 / 51)), "-loop", "0"]
    elif codec not in ("gif", "apng"):
        args += ["-b:v", f"{bitrate}k"] if bitrate else ["-q:v", str(max(2, round(quality * 29 / 51) + 2))]
    if codec == "prores":
        args += ["-pix_fmt", "yuv422p10le"]
    elif codec == "mjpeg":
        args += ["-pix_fmt", "yuvj420p"]
    elif codec == "apng":
        args += ["-pix_fmt", "rgba", "-plays", "0"]
    elif codec not in ("gif", "webp", "ffv1"):
        args += ["-pix_fmt", "nv12" if name.endswith("_qsv") else "yuv420p"]
    return args


def build_conversion(options, info, capabilities):
    """校验高级选项，返回可直接交给 runner.Task 的预设。

    仅输出首个视频/音频流；不保留字幕、附件及额外音轨。
    """
    options, info = options or {}, info or {}
    profile = _get_profile(options.get("format_id", "mp4"))
    if profile["muxer"] not in ((capabilities or {}).get("muxers") or {}):
        raise ValueError("当前 FFmpeg 不支持所选输出格式，请重新检测或选择其他格式")
    video = _select_encoder(options, "video_encoder", "video", profile, capabilities)
    audio = _select_encoder(options, "audio_encoder", "audio", profile, capabilities)
    quality = _number(options, "quality", 23, "视频质量", 0, 51)
    vb = _number(options, "video_bitrate", 0, "视频码率（kbps）", 0, 1000000)
    ab = _number(options, "audio_bitrate", 192, "音频码率（kbps）", 0, 1536)
    width = _number(options, "width", 0, "输出宽度", 0, 16384)
    height = _number(options, "height", 0, "输出高度", 0, 16384)
    fps = _number(options, "fps", 0, "帧率", 0, 240, integer=False)
    rate = _number(options, "sample_rate", 0, "采样率", 0, 384000)
    channels = _number(options, "channels", 0, "声道数", 0, 8)
    if any(value and (value < 2 or value % 2) for value in (width, height)):
        raise ValueError("输出宽高必须为大于等于 2 的偶数，0 表示保持原尺寸或按比例计算")
    if 0 < fps < 0.1:
        raise ValueError("帧率必须为 0（保持原样）或 0.1–240")
    if rate and rate < 8000:
        raise ValueError("采样率必须为 0（保持原样）或 8000–384000 Hz")
    if 0 < ab < 8:
        raise ValueError("音频码率必须为 0（编码器默认值）或 8–1536 kbps")

    source_video, source_audio = info.get("video"), info.get("audio")
    if profile["kind"] == "audio" and not source_audio:
        raise ValueError("源文件没有音轨，无法输出音频格式")
    if video["id"] != "none" and not source_video:
        raise ValueError("源文件没有视频流，请禁用视频或选择音频输出格式")
    # 视频文件没有音轨很常见；保留画面转换，自动略过音频选项。
    if not source_audio and profile["kind"] != "audio":
        audio = {"id": "none", "codec": "none"}
    if video["id"] == "none" and audio["id"] == "none":
        raise ValueError("视频和音频不能全部禁用")

    for selected, stream, kind in ((video, source_video, "video"), (audio, source_audio, "audio")):
        if selected["id"] == "copy":
            source_codec = stream.get("codec") if stream else None
            if source_codec not in profile[f"{kind}_codecs"]:
                label = "视频" if kind == "video" else "音频"
                raise ValueError(f"源{label}编码 {source_codec or '未知'} 不能直接复制到该容器，请选择重编码")
    if video["id"] == "copy" and (width or height or fps or vb):
        raise ValueError("复制视频流时不能调整分辨率、帧率或码率，请选择视频编码器")
    if audio["id"] == "copy" and (rate or channels):
        raise ValueError("复制音频流时不能重采样或调整声道，请选择音频编码器")

    filters = []
    if video["id"] not in ("none", "copy"):
        if width or height:
            filters.append(f"scale={width or -2}:{height or -2}")
        elif source_video and ((source_video.get("w") or 0) % 2 or (source_video.get("h") or 0) % 2):
            filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
        if fps:
            filters.append(f"fps={fps:g}")
        elif video["codec"] in ("mpeg1video", "mpeg2video"):
            # MPEG-1/2 仅支持有限帧率，默认规范到 25 fps。
            filters.append("fps=25")
        elif video["codec"] == "gif":
            filters.append("fps=12")

    args = ["-sn", "-dn"]
    if video["id"] == "none":
        args += ["-vn"]
    elif video["codec"] == "gif":
        chain = ",".join(filters)
        graph = f"[0:v:0]{chain + ',' if chain else ''}split[gp][gv];[gp]palettegen[pal];[gv][pal]paletteuse[vout]"
        args += ["-filter_complex", graph, "-map", "[vout]", "-loop", "0"]
        args += _video_args(video, quality, vb)
    else:
        args += ["-map", "0:v:0"]
        if filters:
            args += ["-vf", ",".join(filters)]
        args += _video_args(video, quality, vb)

    if audio["id"] == "none":
        args += ["-an"]
    else:
        args += ["-map", "0:a:0", "-c:a", audio["id"]]
        if audio["id"] != "copy":
            if audio["codec"] not in _LOSSLESS_AUDIO:
                if audio["codec"] == "opus" and ab > 512:
                    raise ValueError("Opus 音频码率不能超过 512 kbps")
                if audio["codec"] == "mp3" and ab > 320:
                    raise ValueError("MP3 音频码率不能超过 320 kbps")
                if ab:
                    args += ["-b:a", f"{ab}k"]
            effective_rate = rate
            if audio["codec"] == "opus":
                if rate and rate not in (8000, 12000, 16000, 24000, 48000):
                    raise ValueError("Opus 采样率请选择 8000、12000、16000、24000 或 48000 Hz")
                effective_rate = rate or 48000
            allowed_rates = {
                "aac": (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000),
                "mp3": (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000),
                "mp2": (16000, 22050, 24000, 32000, 44100, 48000),
                "ac3": (32000, 44100, 48000),
            }.get(audio["codec"])
            if rate and allowed_rates and rate not in allowed_rates:
                raise ValueError(f"所选音频编码器不支持 {rate} Hz，请选择常用采样率或保持原样")
            if effective_rate:
                args += ["-ar", str(effective_rate)]
            if channels:
                if audio["codec"] in ("mp3", "mp2") and channels > 2:
                    raise ValueError("MP3 / MP2 最多支持双声道")
                if audio["codec"] == "ac3" and channels > 6:
                    raise ValueError("AC-3 最多支持 5.1 声道，请选择 6 声道或更少")
                args += ["-ac", str(channels)]
            elif audio["codec"] in ("mp3", "mp2") and (source_audio.get("channels") or 0) > 2:
                args += ["-ac", "2"]

    if profile["id"] in ("mp4", "mov", "m4a"):
        args += ["-movflags", "+faststart"]
        if video["codec"] == "hevc":
            args += ["-tag:v", "hvc1"]
    args += ["-f", profile["muxer"]]
    return {"id": "custom", "name": f"自定义 · {profile['label']}",
            "ext": profile["ext"], "args": args, "allow_hw_override": False}
