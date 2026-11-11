# -*- coding: utf-8 -*-
"""共享并发任务队列，工作线程通过回调上报任务状态与整批完成事件。"""
import logging
import threading
import time

from .runner import cancel_task, run_task

_LOG = logging.getLogger(__name__)
_TERMINAL = {"done", "error", "cancelled"}
_ACTIVE = {"queued", "starting", "running"}


class TaskQueue:
    def __init__(self, on_event=None, on_log=None, concurrency_provider=None):
        self._tasks = []                     # 仅保留活动任务，重试不会命中历史任务
        self._lock = threading.RLock()
        self._event_lock = threading.RLock()
        self._workers = []
        self._done_emitted = True
        self._closed = False
        self._on_event = on_event
        self._on_log = on_log
        self._cp = concurrency_provider or (lambda: 1)

    def enqueue(self, tasks):
        tasks = list(tasks)
        if not tasks:
            return
        # 入队和完成通知串行化，避免上一批的 done 晚于下一批 queued。
        with self._event_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("任务队列已关闭")
                if any(t.status != "queued" or t in self._tasks for t in tasks):
                    raise ValueError("只能入队新的 queued 任务")
                if len({id(t) for t in tasks}) != len(tasks):
                    raise ValueError("同一个任务对象不能重复入队")
                self._tasks.extend(tasks)
                self._done_emitted = False
            for task in tasks:
                self._emit(task)
            self._ensure_workers()

    def cancel(self, task_id):
        with self._lock:
            tasks = [t for t in self._tasks if t.id == task_id and t.status in _ACTIVE]
        self._cancel_tasks(tasks)

    def cancel_all(self):
        with self._lock:
            tasks = list(self._tasks)
        self._cancel_tasks(tasks)

    def shutdown(self, timeout=5.0):
        """停止接收任务，取消并有界等待；停止后调用方可清理输入临时资源。"""
        deadline = time.monotonic() + max(0, timeout)
        with self._event_lock:
            with self._lock:
                self._closed = True
        self.cancel_all()
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=max(0, deadline - time.monotonic()))
        return not any(worker.is_alive() for worker in workers)

    def _cancel_tasks(self, tasks):
        queued, running = [], []
        with self._lock:
            for task in tasks:
                if task.status not in _ACTIVE:
                    continue
                task.cancel_event.set()
                if task.status == "queued":
                    task.status = "cancelled"
                    self._tasks.remove(task)
                    queued.append(task)
                else:
                    running.append(task)
        for task in queued:
            self._emit(task)
        for task in running:
            # taskkill 可能等待驱动响应，不在 Qt 主线程串行等待进程终止。
            threading.Thread(target=cancel_task, args=(task,), daemon=True).start()
        self._maybe_done()

    def _desired_workers(self):
        try:
            return max(1, min(4, int(self._cp())))
        except (TypeError, ValueError):
            return 1

    def _ensure_workers(self):
        with self._lock:
            if self._closed or not any(t.status == "queued" for t in self._tasks):
                return
            while len(self._workers) < self._desired_workers():
                worker = threading.Thread(target=self._run, daemon=True)
                # 注册与启动均在同一把锁内，退出线程也在锁内注销。
                self._workers.append(worker)
                try:
                    worker.start()
                except Exception:
                    self._workers.remove(worker)
                    raise

    def _run(self):
        worker = threading.current_thread()
        try:
            while True:
                with self._lock:
                    task = next((t for t in self._tasks if t.status == "queued"), None)
                    if task is None or len(self._workers) > self._desired_workers():
                        # 先注销再释放锁，入队者不会把即将退出的线程算作可用。
                        self._workers.remove(worker)
                        break
                    task.status = "starting"

                def on_log(line, task_id=task.id):
                    if self._on_log:
                        self._on_log(task_id, line)

                try:
                    run_task(task, on_progress=self._emit, on_log=on_log)
                    if task.status not in _TERMINAL:
                        raise RuntimeError("任务未返回完成状态")
                except Exception as exc:
                    task.status = "cancelled" if task.cancel_event.is_set() else "error"
                    task.error = "" if task.status == "cancelled" else f"任务执行失败：{exc}"
                    self._emit(task)
                finally:
                    with self._lock:
                        if task in self._tasks:
                            self._tasks.remove(task)
                self._maybe_done()
        finally:
            with self._lock:
                if worker in self._workers:
                    self._workers.remove(worker)
            self._ensure_workers()
            self._maybe_done()

    def _maybe_done(self):
        with self._event_lock:
            with self._lock:
                if self._tasks or self._done_emitted:
                    return
                self._done_emitted = True
            self._notify({"event": "queue-done"})

    def _notify(self, event):
        if self._on_event:
            with self._event_lock:
                try:
                    self._on_event(event)
                except Exception:
                    _LOG.exception("队列事件回调失败")

    def _emit(self, task):
        self._notify({
            "event": "task",
            "id": task.id,
            "status": task.status,
            "percent": round(task.percent, 1),
            "speed": task.speed,
            "output_path": task.output_path,
            "error": task.error,
            "took_ms": task.took_ms,
        })
