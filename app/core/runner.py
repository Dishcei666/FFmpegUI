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
import uuid
import logging

from . import settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_LOG = logging.getLogger(__name__)


def _final_args(task):
    """按设置应用硬件编码替换（含 libx264 的参数才替换）。"""
    args = list(task.preset["args"])
    if (task.preset.get("allow_hw_override", True)
            and settings.get("prefer_gpu") and settings.get("hw_encoder")):
        from . import gpu
        args = gpu.apply_hw_args(args, settings.get("hw_encoder"))
    return args


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
        self.cancel_event = threading.Event()     # 入队前即可取消，启动时不重置
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
    temp = os.path.join(out_dir, f"{base}.ffgui-tmp.{uuid.uuid4().hex}.{ext}")
    return final, temp


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def run_task(task, on_progress=None, on_log=None):
    """阻塞执行一个任务，所有退出路径均清理资源并上报终态。"""
    started = time.monotonic()
    err_tail = []
    readers = []

    def drain(stream, sink):
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "ignore").strip()
                if line:
                    sink(line)
        except (OSError, ValueError):
            pass                              # 退出清理期间管道可能已关闭

    def on_err_line(line):
        err_tail.append(line)
        if len(err_tail) > 60:
            err_tail.pop(0)
        if on_log:
            _call(on_log, line)

    try:
        if _cancelled(task):
            task.status = "cancelled"
            return task
        input_part = (task.input_args if task.custom_input else
                      [*task.input_args, "-i", task.input_path])
        args = [settings.ffmpeg(), "-hide_banner", "-nostdin", "-y",
                "-progress", "pipe:1", "-nostats",
                *input_part, *_final_args(task), task.temp_path]
        task.status = "running"
        _emit(task, on_progress, force=True)
        if _cancelled(task):
            task.status = "cancelled"
            return task
        try:
            task._proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW)
        except FileNotFoundError:
            task.status = "error"
            task.error = "未找到 ffmpeg，请确认已安装并加入 PATH 或在设置中指定路径"
            return task
        # 取消可能发生在 Popen 内部、进程对象赋值之前。
        if _cancelled(task):
            _terminate(task._proc)
        readers = [
            threading.Thread(target=drain, args=(task._proc.stdout,
                lambda line: _on_progress_line(task, line, on_progress)), daemon=True),
            threading.Thread(target=drain, args=(task._proc.stderr, on_err_line), daemon=True),
        ]
        for reader in readers:
            reader.start()
        code = task._proc.wait()
        for reader in readers:
            reader.join(timeout=2)
        if _cancelled(task):
            task.status = "cancelled"
        elif code == 0:
            try:
                task.output_path = _publish_output(task)
                task.output_size = os.path.getsize(task.output_path)
                task.status = "done"
                task.percent = 100.0
            except OSError as exc:
                task.status = "error"
                task.error = f"保存输出文件失败：{exc}"
        else:
            task.status = "error"
            task.error = "\n".join(err_tail[-6:]) or f"ffmpeg 异常退出（代码 {code}）"
    except Exception as exc:
        task.status = "cancelled" if _cancelled(task) else "error"
        task.error = "" if task.status == "cancelled" else f"任务执行失败：{exc}"
    finally:
        try:
            _cleanup_process(task._proc, readers)
        finally:
            task._proc = None
            if task.status != "done":
                _safe_remove(task.temp_path)
            task.took_ms = int((time.monotonic() - started) * 1000)
            _emit(task, on_progress, force=True)
    return task


def _cleanup_process(proc, readers):
    if proc is None:
        return
    try:
        try:
            running = proc.poll() is None
        except OSError:
            running = True
        if running:
            _terminate(proc)
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    _LOG.exception("回收 FFmpeg 进程失败")
    finally:
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    _LOG.exception("关闭 FFmpeg 管道失败")


def _publish_output(task):
    """原子发布完整输出，不覆盖同时完成的任务或外部新建文件。"""
    while True:
        final = _unique_final(task.out_dir, task.input_path, task.preset["ext"],
                              base_name=task.base_name)
        try:
            if os.name == "nt":
                os.rename(task.temp_path, final)  # Windows rename 不覆盖已有文件
            else:
                os.link(task.temp_path, final)    # POSIX rename 会覆盖，改用排他链接
                _safe_remove(task.temp_path)
            return final
        except FileExistsError:
            continue


def _cancelled(task):
    return task.cancel_event is not None and task.cancel_event.is_set()


def _terminate(proc):
    try:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=_NO_WINDOW, timeout=5)
        if proc.poll() is None:
            proc.kill()
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def cancel_task(task):
    """请求取消，启动之前的请求也会被 run_task 保留。"""
    if task.cancel_event is not None:
        task.cancel_event.set()
    proc = task._proc
    if proc is not None:
        _terminate(proc)


def _call(callback, value):
    try:
        callback(value)
    except Exception:
        # 窗口关闭或订阅者异常不能阻断管道读取，否则 FFmpeg 可能死锁。
        _LOG.exception("任务回调失败")


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
    now = time.monotonic()
    if force or now - task._last_emit >= 0.2:
        task._last_emit = now
        _call(cb, task)
