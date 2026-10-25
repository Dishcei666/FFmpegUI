# -*- coding: utf-8 -*-
"""视频拼接核心：兼容性检查 + concat 快速合并 + 重编码统一合并。"""
import os
import tempfile

from .presets import get_preset


def check_compatible(infos):
    """判断能否用 concat 流复制快速合并。返回 (ok, reason)。

    infos: probe_file 输出列表（顺序与文件一致）。
    """
    if len(infos) < 2:
        return False, "至少需要 2 个文件"
    if any(not i.get("video") for i in infos):
        return False, "存在不含视频流的文件"
    first = infos[0]
    codecs = {i["video"]["codec"] for i in infos}
    if len(codecs) > 1:
        return False, f"编码不一致（{'/'.join(sorted(codecs))}）"
    sizes = {(i["video"]["w"], i["video"]["h"]) for i in infos}
    if len(sizes) > 1:
        return False, "分辨率不一致（" + "、".join(f"{w}×{h}" for w, h in
                                                sorted(sizes, reverse=True)) + "）"
    audios = [i.get("audio") for i in infos]
    if any(audios) and not all(audios):
        return False, "部分文件有音轨、部分没有"
    if all(audios):
        acodecs = {a["codec"] for a in audios}
        rates = {a["sample_rate"] for a in audios}
        if len(acodecs) > 1:
            return False, f"音频编码不一致（{'/'.join(sorted(acodecs))}）"
        if len(rates) > 1:
            return False, "音频采样率不一致"
    return True, "编码参数一致，可快速合并（不重编码）"


def write_concat_list(paths):
    """生成 concat 清单临时文件，返回其路径（用完由调用方删除）。"""
    lines = []
    for p in paths:
        esc = p.replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{esc}'")
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="ffgui-merge-",
                                     text=False)
    os.close(fd)
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return list_path


def build_fast_join(paths, out_ext=None):
    """concat 流复制合并。返回 (input_args, out_args, ext, label, 清单文件路径)。"""
    list_path = write_concat_list(paths)
    ext = out_ext or os.path.splitext(paths[0])[1][1:].lower() or "mp4"
    inp = ["-f", "concat", "-safe", "0", "-i", list_path]
    out = ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    return inp, out, ext, "快速合并", list_path


def build_join(paths, infos, encode_preset_id):
    """重编码合并：统一缩放到首个文件的分辨率/帧率再 concat。

    返回 (input_args, out_args, ext, label, total_duration)。input_args 已含全部 -i。
    """
    preset = get_preset(encode_preset_id) or get_preset("x264-hq")
    v0 = infos[0]["video"]
    w, h = max(v0["w"], 2), max(v0["h"], 2)
    fps = v0.get("fps") or 0
    normalize = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    if fps > 0:
        normalize += f",fps={fps:.3f}"

    audios = [i.get("audio") for i in infos]
    use_audio = all(audios)                 # 音轨不齐时丢弃音频，保证能拼
    n = len(paths)

    inp = []
    graph_parts = []
    seg_labels = []
    for i, p in enumerate(paths):
        inp += ["-i", p]
        graph_parts.append(f"[{i}:v]{normalize}[v{i}]")
        seg_labels.append(f"[v{i}]")
        if use_audio:
            graph_parts.append(
                f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            seg_labels.append(f"[a{i}]")

    # concat 滤镜的输入按"每段 视频+音频"交错排列；链与链之间用 ; 分隔
    joined = ";".join(graph_parts)
    if use_audio:
        graph = (joined + ";" + "".join(seg_labels) +
                 f"concat=n={n}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        graph = (joined + ";" + "".join(seg_labels) +
                 f"concat=n={n}:v=1[v]")
        maps = ["-map", "[v]"]

    out = ["-filter_complex", graph, *maps, *preset["args"]]
    total = sum((i.get("duration") or 0) for i in infos)
    return inp, out, "mp4", preset["name"], total
