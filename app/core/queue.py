# -*- coding: utf-8 -*-
"""串行任务队列：后台线程依次执行任务，通过回调上报事件。

事件协议（on_event 收到的 dict）：
  {"event": "task", id, status, percent, speed, output_path, error, took_ms}
  {"event": "queue-done"}
"""
import threading

from .runner import cancel_task, run_task


class TaskQueue:
    def __init__(self, on_event=None, on_log=None, concurrency_provider=None):
        self._tasks = []
        self._lock = threading.Lock()
        self._workers = []
        self._done_emitted = True
        self._on_event = on_event
        self._on_log = on_log
        self._cp = concurrency_provider or (lambda: 1)

    def enqueue(self, tasks):
        with self._lock:
            self._tasks.extend(tasks)
            self._done_emitted = False
        for t in tasks:
            self._emit(t)
        self._ensure_workers()

    def cancel(self, task_id):
        with self._lock:
            task = next((t for t in self._tasks if t.id == task_id), None)
        if task is None:
            return
        if task.status == "queued":
            task.status = "cancelled"
            self._emit(task)
        elif task.status in ("starting", "running"):
            cancel_task(task)                 # 状态在 run_task 收尾时更新

    def cancel_all(self):
        with self._lock:
            ids = [t.id for t in self._tasks]
        for task_id in ids:
            self.cancel(task_id)

    # ------------------------------------------------------------------
    def _desired_workers(self):
        try:
            return max(1, min(4, int(self._cp())))
        except (TypeError, ValueError):
            return 1

    def _ensure_workers(self):
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            while len(self._workers) < self._desired_workers():
                worker = threading.Thread(target=self._run, daemon=True)
                worker.start()
                self._workers.append(worker)

    def _run(self):
        while True:
            with self._lock:
                task = next((t for t in self._tasks
                             if t.status == "queued"), None)
                if task is not None:
                    task.status = "starting"   # 先认领，避免并发重复执行
            if task is None:
                break
            task.cancel_event = threading.Event()
            task.status = "running"
            task_id = task.id

            def on_log(line, _id=task_id):
                if self._on_log:
                    self._on_log(_id, line)

            run_task(task, on_progress=lambda t: self._emit(t), on_log=on_log)
        self._maybe_done()

    def _maybe_done(self):
        with self._lock:
            active = any(t.status in ("queued", "starting", "running")
                         for t in self._tasks)
            if active or self._done_emitted:
                return
            self._done_emitted = True
        if self._on_event:
            self._on_event({"event": "queue-done"})

    def _emit(self, task):
        if not self._on_event:
            return
        self._on_event({
            "event": "task",
            "id": task.id,
            "status": task.status,
            "percent": round(task.percent, 1),
            "speed": task.speed,
            "output_path": task.output_path,
            "error": task.error,
            "took_ms": task.took_ms,
        })
