# -*- coding: utf-8 -*-
"""音频工具核心：格式预设 + 音量归一化/降噪处理链。"""

AUDIO_PRESETS = [
    {"id": "mp3-320", "name": "MP3 320k", "desc": "最通用的有损格式，最高码率",
     "ext": "mp3", "args": ["-map", "0:a:0", "-c:a", "libmp3lame",
                            "-b:a", "320k"]},
    {"id": "mp3-vbr", "name": "MP3 VBR 高音质", "desc": "动态码率，体积与质量平衡",
     "ext": "mp3", "args": ["-map", "0:a:0", "-c:a", "libmp3lame",
                            "-q:a", "2"]},
    {"id": "aac-256", "name": "AAC (M4A) 256k", "desc": "苹果生态友好，同码率优于 MP3",
     "ext": "m4a", "args": ["-map", "0:a:0", "-c:a", "aac",
                            "-b:a", "256k"]},
    {"id": "flac", "name": "FLAC 无损", "desc": "无损压缩，存档首选",
     "ext": "flac", "args": ["-map", "0:a:0", "-c:a", "flac"]},
    {"id": "wav", "name": "WAV 无压缩", "desc": "16bit PCM，编辑/后期使用",
     "ext": "wav", "args": ["-map", "0:a:0", "-c:a", "pcm_s16le"]},
    {"id": "ogg", "name": "OGG Vorbis 192k", "desc": "开源格式，游戏/网页常用",
     "ext": "ogg", "args": ["-map", "0:a:0", "-c:a", "libvorbis",
                            "-q:a", "6"]},
    {"id": "opus", "name": "Opus 128k", "desc": "同码率音质最佳，语音/播客首选",
     "ext": "opus", "args": ["-map", "0:a:0", "-c:a", "libopus",
                             "-b:a", "128k"]},
]


def get_audio_preset(preset_id):
    return next((p for p in AUDIO_PRESETS if p["id"] == preset_id), None)


def build_audio(preset_id, normalize=False, denoise=False):
    """返回 (out_args, ext, label)。归一化/降噪通过 -af 滤镜链实现。"""
    preset = get_audio_preset(preset_id) or AUDIO_PRESETS[0]
    af = []
    if denoise:
        af.append("afftdn=nf=-25")
    if normalize:
        # EBU R128 单遍动态归一化，-16 LUFS 为播客/视频常用响度
        af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    out = list(preset["args"])
    if af:
        out += ["-af", ",".join(af)]
    tags = [t for t, on in (("归一化", normalize), ("降噪", denoise)) if on]
    label = preset["name"] + ("·" + "+".join(tags) if tags else "")
    return out, preset["ext"], label
