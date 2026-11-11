# -*- coding: utf-8 -*-
"""把预设的视频滤镜并入业务滤镜链，避免多个 -vf 或 simple/complex 冲突。"""


def join_filters(*filters):
    return ",".join(value for value in filters if value)


def split_video_filters(args):
    """返回 (不含视频滤镜的输出参数, 视频滤镜链)，不修改原参数。"""
    output, filters = [], []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-vf", "-filter:v", "-filter:v:0"):
            if index + 1 >= len(args):
                raise ValueError(f"{arg} 缺少滤镜参数")
            filters.append(args[index + 1])
            index += 2
        else:
            output.append(arg)
            index += 1
    return output, join_filters(*filters)
