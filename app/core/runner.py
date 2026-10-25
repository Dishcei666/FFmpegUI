# -*- coding: utf-8 -*-
"""ffmpeg 任务构建与执行。

设计要点：
- 输出先写入临时文件，成功后改名，避免中途中断留下损坏文件；
- 用 "-progress pipe:1 -nostats" 从 stdout 按行读取进度（key=value）；
- stderr 只保留末尾若干行用于报错，同时逐行回调给日志面板；
- 取消通过 task.cancel_event + 杀进程实现（Windows 用 taskkill 杀整棵进程树）。
"""
import os
import subprocess
import threading
import time

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Task:
    """一个转换任务（核心层与 UI 层共用的数据结构）。

    - base_name:    输出文件主名（默认取输入文件名）；剪辑导出用它区分 片段01/02…
    - task_id:      任务标识（默认输入路径）；同一源文件的多个片段任务需各给唯一 id
    - input_args:   放在 -i 之前的输入侧参数（剪辑用 -ss/-t 实现快速输入定位）
    - custom_input: input_args 已包含完整的（可多个）"-i 源"，runner 不再自动拼 -i
    """

    def __init__(self, input_path, preset, out_dir=None, duration=0.0,
                 base_name=None, task_id=None, input_args=None,
                 custom_input=False):
        self.id = task_id or input_path
        self.input_path = input_path
        self.preset = preset
        self.out_dir = out_dir or os.path.dirname(input_path) or "."
        self.duration = float(duration or 0)
        self.base_name = base_name
        self.input_args = list(input_args) if input_args else []
        self.custom_input = custom_input
        self.output_path, self.temp_path = _plan_output(
            self.out_dir, input_path, preset["ext"],
            base_name=base_name)

        self.status = "queued"                    # queued/running/done/error/cancelled
        self.percent = 0.0
        self.speed = ""
        self.error = ""
        self.output_size = 0
        self.took_ms = 0
        self.cancel_event = None                  # 由队列注入
        self._proc = None
        self._last_emit = 0.0


def _unique_final(out_dir, input_path, ext, base_name=None):
    """生成不与现有文件、也不与输入文件冲突的最终输出路径。"""
    base = base_name or os.path.splitext(os.path.basename(input_path))[0]
    final = os.path.join(out_dir, f"{base}.{ext}")
    i = 0
    while os.path.exists(final) or os.path.abspath(final) == os.path.abspath(input_path):
        i += 1
        final = os.path.join(out_dir, f"{base} ({i}).{ext}")
    return final


def _plan_output(out_dir, input_path, ext, base_name=None):
    base = base_name or os.path.splitext(os.path.basename(input_path))[0]
    final = _unique_final(out_dir, input_path, ext, base_name=base)
    temp = os.path.join(out_dir, f"{base}.ffgui-tmp.{ext}")
    if os.path.exists(temp):
        try:
            os.remove(temp)
        except OSError:
            pass
    return final, temp


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def run_task(task, on_progress=None, on_log=None):
    """阻塞执行一个任务（应在后台线程调用），返回更新后的 task。"""
    if task.custom_input:
        input_part = task.input_args
    else:
        input_part = [*task.input_args, "-i", task.input_path]
    args = [FFMPEG, "-hide_banner", "-nostdin", "-y",
            "-progress", "pipe:1", "-nostats",
            *input_part, *task.preset["args"], task.temp_path]
    task.status = "running"
    task._started_at = time.time()
    _emit(task, on_progress, force=True)

    try:
        task._proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW)
    except FileNotFoundError:
        task.status = "error"
        task.error = "未找到 ffmpeg，请确认已安装并加入 PATH"
        return task

    err_tail = []

    def drain(stream, sink):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "ignore").strip()
            if line:
                sink(line)

    def on_err_line(line):
        err_tail.append(line)
        if len(err_tail) > 60:
            err_tail.pop(0)
        if on_log:
            on_log(line)

    t_out = threading.Thread(target=drain,
                             args=(task._proc.stdout,
                                   lambda l: _on_progress_line(task, l, on_progress)),
                             daemon=True)
    t_err = threading.Thread(target=drain,
                             args=(task._proc.stderr, on_err_line),
                             daemon=True)
    t_out.start()
    t_err.start()
    code = task._proc.wait()
    t_out.join(timeout=2)
    t_err.join(timeout=2)

    if task.cancel_event is not None and task.cancel_event.is_set():
        _safe_remove(task.temp_path)
        task.status = "cancelled"
    elif code == 0:
        try:
            final = task.output_path
            if os.path.exists(final):
                final = _unique_final(task.out_dir, task.input_path,
                                      task.preset["ext"],
                                      base_name=task.base_name)
            os.replace(task.temp_path, final)
            task.output_path = final
            task.output_size = os.path.getsize(final)
            task.status = "done"
            task.percent = 100.0
        except OSError as exc:
            _safe_remove(task.temp_path)
            task.status = "error"
            task.error = f"保存输出文件失败：{exc}"
    else:
        _safe_remove(task.temp_path)
        task.status = "error"
        task.error = "\n".join(err_tail[-6:]) or f"ffmpeg 异常退出（代码 {code}）"

    task.took_ms = int((time.time() - task._started_at) * 1000)
    _emit(task, on_progress, force=True)
    return task


def cancel_task(task):
    """请求取消：排队中的任务由队列直接置为 cancelled，运行中的杀进程。"""
    if task.cancel_event:
        task.cancel_event.set()
    proc = task._proc
    if proc is not None and proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=_NO_WINDOW)
            else:
                proc.kill()
        except OSError:
            pass


def _on_progress_line(task, line, cb):
    if line.startswith("out_time_us="):
        try:
            _set_elapsed(task, int(line.split("=", 1)[1]) / 1e6)
        except ValueError:
            pass
    elif line.startswith("out_time_ms="):        # 兼容：该值历史上也是微秒
        try:
            _set_elapsed(task, int(line.split("=", 1)[1]) / 1e6)
        except ValueError:
            pass
    elif line.startswith("out_time="):
        _set_elapsed(task, _parse_hms(line.split("=", 1)[1]))
    elif line.startswith("speed="):
        speed = line.split("=", 1)[1]
        task.speed = "" if speed == "N/A" else speed
    _emit(task, cb)


def _set_elapsed(task, sec):
    if task.duration > 0:
        task.percent = max(0.0, min(99.9, sec / task.duration * 100.0))


def _parse_hms(text):
    try:
        parts = text.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        pass
    return 0.0


def _emit(task, cb, force=False):
    if not cb:
        return
    now = time.time()
    if force or now - task._last_emit >= 0.2:
        task._last_emit = now
        cb(task)
