# -*- coding: utf-8 -*-
"""输出预设定义。

每个预设描述"输入文件 -> 期望输出"的转换方式：
- ext  : 输出文件扩展名（决定封装格式）
- args : ffmpeg 输出侧参数（入口处会自动拼接 -i 输入 与 输出文件名）
"""

PRESETS = [
    {
        "id": "copy-mp4",
        "name": "MP4 无损快转",
        "desc": "直接复制流不重编码，速度最快",
        "ext": "mp4",
        "args": ["-map", "0:v?", "-map", "0:a?",
                 "-c", "copy", "-movflags", "+faststart"],
    },
    {
        "id": "x264-hq",
        "name": "MP4 高质量",
        "desc": "H.264 CRF 18，接近原画质",
        "ext": "mp4",
        "args": ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    },
    {
        "id": "x264-compress",
        "name": "MP4 高压缩",
        "desc": "H.264 CRF 26，体积更小",
        "ext": "mp4",
        "args": ["-c:v", "libx264", "-crf", "26", "-preset", "medium",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
    },
    {
        "id": "scale-1080",
        "name": "缩放至 1080p",
        "desc": "大视频缩至 1080p（不会放大）",
        "ext": "mp4",
        "args": ["-vf", "scale=-2:'min(1080,ih)'",
                 "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    },
    {
        "id": "scale-720",
        "name": "缩放至 720p",
        "desc": "大视频缩至 720p（不会放大）",
        "ext": "mp4",
        "args": ["-vf", "scale=-2:'min(720,ih)'",
                 "-c:v", "libx264", "-crf", "21", "-preset", "medium",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    },
    {
        "id": "audio-mp3",
        "name": "提取音频 MP3",
        "desc": "丢弃画面，导出高音质 MP3",
        "ext": "mp3",
        "args": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    },
    {
        "id": "gif",
        "name": "GIF 动图",
        "desc": "12 帧 480 宽，适合短片段",
        "ext": "gif",
        "args": ["-filter_complex",
                 "[0:v]fps=12,scale=480:-2:flags=lanczos,"
                 "split[a][b];[a]palettegen[p];[b][p]paletteuse[out]",
                 "-map", "[out]", "-loop", "0"],
    },
]


def get_preset(preset_id):
    """按 id 查找预设，找不到返回 None。"""
    return next((p for p in PRESETS if p["id"] == preset_id), None)
