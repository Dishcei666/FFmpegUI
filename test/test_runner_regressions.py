# -*- coding: utf-8 -*-
"""任务取消、错误终态、并发输出与 worker 生命周期回归。"""
import io
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

from app.core.queue import TaskQueue
from app.core.runner import Task, run_task, cancel_task, _publish_output

PRESET = {"name": "test", "ext": "mp4", "args": ["-c", "copy"]}


class RunnerRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def task(self, name="input.mkv", **kwargs):
        return Task(str(self.root / name), PRESET, str(self.root), **kwargs)

    def test_same_basename_tasks_never_share_or_delete_temporary_files(self):
        first = self.task("a/same.mkv")
        Path(first.temp_path).write_bytes(b"running output")
        second = self.task("b/same.mkv")
        self.assertNotEqual(first.temp_path, second.temp_path)
        self.assertEqual(Path(first.temp_path).read_bytes(), b"running output")

    def test_cancel_before_run_never_spawns_and_emits_terminal(self):
        task = self.task()
        cancel_task(task)
        states = []
        with patch("app.core.runner.subprocess.Popen") as spawn:
            run_task(task, on_progress=lambda t: states.append(t.status))
        spawn.assert_not_called()
        self.assertEqual(states, ["cancelled"])
        self.assertFalse(Path(task.temp_path).exists())

    def test_cancel_from_running_notification_does_not_spawn(self):
        task = self.task()
        states = []

        def progress(t):
            states.append(t.status)
            if t.status == "running":
                cancel_task(t)

        with patch("app.core.runner.subprocess.Popen") as spawn:
            run_task(task, on_progress=progress)
        spawn.assert_not_called()
        self.assertEqual(states, ["running", "cancelled"])

    def test_startup_failures_always_emit_error(self):
        for error in (FileNotFoundError("missing"), PermissionError("denied")):
            with self.subTest(error=type(error).__name__):
                task = self.task()
                states = []
                with patch("app.core.runner.subprocess.Popen", side_effect=error):
                    run_task(task, on_progress=lambda t: states.append(t.status))
                self.assertEqual(states, ["running", "error"])
                self.assertTrue(task.error)
                self.assertIsNone(task._proc)

    def test_cancel_during_spawn_terminates_process(self):
        task = self.task()

        class Process:
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            returncode = None
            pid = 123

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        proc = Process()

        def spawn(*args, **kwargs):
            cancel_task(task)              # 模拟 Popen 尚未返回时收到取消
            return proc

        with patch("app.core.runner.subprocess.Popen", side_effect=spawn), \
                patch("app.core.runner._terminate", side_effect=lambda p: p.kill()):
            run_task(task)
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(proc.returncode, -9)
        self.assertTrue(proc.stdout.closed)
        self.assertIsNone(task._proc)

    def test_cleanup_failure_still_emits_terminal_and_removes_partial_output(self):
        for failure in ("wait", "close"):
            with self.subTest(failure=failure):
                task = self.task()
                Path(task.temp_path).write_bytes(b"partial")
                states = []

                class BadStream(io.BytesIO):
                    def close(self):
                        super().close()
                        if failure == "close":
                            raise OSError("close failed")

                class Process:
                    stdout = BadStream()
                    stderr = io.BytesIO()
                    returncode = 1 if failure == "close" else None
                    waited = False
                    pid = 123

                    def poll(self):
                        return self.returncode

                    def wait(self, timeout=None):
                        if failure == "wait" and not self.waited:
                            self.waited = True
                            raise OSError("wait failed")
                        return self.returncode

                    def kill(self):
                        self.returncode = -9

                proc = Process()
                with patch("app.core.runner.subprocess.Popen", return_value=proc), \
                        patch("app.core.runner._terminate", side_effect=lambda p: p.kill()), \
                        patch("app.core.runner._LOG.exception"):
                    run_task(task, on_progress=lambda t: states.append(t.status))
                self.assertEqual(states[-1], "error")
                self.assertIsNone(task._proc)
                self.assertFalse(Path(task.temp_path).exists())
                self.assertTrue(proc.stderr.closed)

    def test_concurrent_publish_preserves_every_output_and_existing_file(self):
        (self.root / "same.mp4").write_bytes(b"existing")
        tasks = [self.task(f"{i}/same.mkv") for i in range(8)]
        for i, task in enumerate(tasks):
            Path(task.temp_path).write_bytes(str(i).encode())
        barrier = threading.Barrier(len(tasks))
        outputs, errors = [], []

        def publish(task):
            try:
                barrier.wait(timeout=5)
                outputs.append(_publish_output(task))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(t,)) for t in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(outputs)), len(tasks))
        self.assertEqual({Path(p).read_bytes() for p in outputs},
                         {str(i).encode() for i in range(len(tasks))})
        self.assertEqual((self.root / "same.mp4").read_bytes(), b"existing")
        self.assertFalse(any(Path(t.temp_path).exists() for t in tasks))


class QueueRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def task(self, name="source.mkv"):
        return Task(os.path.join(self.temp.name, name), PRESET, self.temp.name)

    def wait_for_workers(self, queue):
        with queue._lock:
            workers = list(queue._workers)
        for worker in workers:
            worker.join(timeout=5)
        self.assertFalse(any(worker.is_alive() for worker in workers))

    def test_retry_with_same_id_can_be_cancelled(self):
        done = threading.Event()
        started = threading.Event()
        events = []

        def event(ev):
            events.append(ev)
            if ev["event"] == "queue-done":
                done.set()

        first, second = self.task(), self.task()

        def execute(task, on_progress, on_log):
            if task is first:
                task.status = "error"
            else:
                task.status = "running"
                started.set()
                if not task.cancel_event.wait(5):
                    raise RuntimeError("cancel request lost")
                task.status = "cancelled"
            on_progress(task)

        queue = TaskQueue(on_event=event)
        with patch("app.core.queue.run_task", side_effect=execute):
            queue.enqueue([first])
            self.assertTrue(done.wait(5))
            done.clear()
            queue.enqueue([second])
            self.assertTrue(started.wait(5))
            queue.cancel(second.id)
            self.assertTrue(done.wait(5))
            self.wait_for_workers(queue)
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(len([e for e in events if e["event"] == "queue-done"]), 2)
        self.assertEqual(queue._tasks, [])

    def test_worker_exception_does_not_block_following_task(self):
        done = threading.Event()
        first, second = self.task("a.mkv"), self.task("b.mkv")

        def execute(task, on_progress, on_log):
            if task is first:
                raise PermissionError("cannot launch")
            task.status = "done"
            on_progress(task)

        queue = TaskQueue(on_event=lambda ev: done.set()
                          if ev["event"] == "queue-done" else None)
        with patch("app.core.queue.run_task", side_effect=execute):
            queue.enqueue([first, second])
            self.assertTrue(done.wait(5))
            self.wait_for_workers(queue)
        self.assertEqual(first.status, "error")
        self.assertEqual(second.status, "done")
        self.assertEqual(queue._tasks, [])

    def test_enqueue_during_worker_completion_is_not_stranded(self):
        retiring, release, finished = threading.Event(), threading.Event(), threading.Event()
        first, second = self.task("a.mkv"), self.task("b.mkv")
        queue = TaskQueue()
        real_maybe_done = queue._maybe_done
        block_once = [True]

        def maybe_done():
            if block_once[0]:
                block_once[0] = False
                retiring.set()
                release.wait(5)
            real_maybe_done()

        def execute(task, on_progress, on_log):
            task.status = "done"
            on_progress(task)
            if task is second:
                finished.set()

        with patch.object(queue, "_maybe_done", side_effect=maybe_done), \
                patch("app.core.queue.run_task", side_effect=execute):
            queue.enqueue([first])
            self.assertTrue(retiring.wait(5))
            queue.enqueue([second])
            release.set()
            self.assertTrue(finished.wait(5))
            self.wait_for_workers(queue)
        self.assertEqual(second.status, "done")

    def test_subscriber_exception_does_not_stop_queue(self):
        ran = threading.Event()
        task = self.task()

        def broken_callback(_):
            raise RuntimeError("subscriber failed")

        def execute(task, on_progress, on_log):
            task.status = "done"
            on_progress(task)
            ran.set()

        queue = TaskQueue(on_event=broken_callback)
        with patch("app.core.queue.run_task", side_effect=execute), \
                patch("app.core.queue._LOG.exception") as logged:
            queue.enqueue([task])
            self.assertTrue(ran.wait(5))
            self.wait_for_workers(queue)
        self.assertEqual(task.status, "done")
        self.assertEqual(queue._tasks, [])
        self.assertTrue(logged.called)

    def test_cancel_all_cancels_queued_without_starting_them(self):
        done = threading.Event()
        queue = TaskQueue(on_event=lambda ev: done.set()
                          if ev["event"] == "queue-done" else None)
        tasks = [self.task("a.mkv"), self.task("b.mkv")]
        with patch.object(queue, "_ensure_workers"):
            queue.enqueue(tasks)
            queue.cancel_all()
        self.assertTrue(done.is_set())
        self.assertEqual([t.status for t in tasks], ["cancelled", "cancelled"])
        self.assertTrue(all(t.cancel_event.is_set() for t in tasks))
        self.assertEqual(queue._tasks, [])


@unittest.skipUnless(shutil.which("ffmpeg"), "需要本机 FFmpeg")
class RealRunnerRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, directory, color):
        target = self.root / directory / "same.mp4"
        target.parent.mkdir()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=20:d=0.5",
                        "-c:v", "libx264", "-preset", "ultrafast", str(target)],
                       check=True, capture_output=True, timeout=15)
        return str(target)

    def test_real_same_name_concurrent_conversions_preserve_both_contents(self):
        red, blue = self.source("a", "red"), self.source("b", "blue")
        tasks = [Task(src, PRESET, str(self.root)) for src in (red, blue)]
        finished = threading.Event()
        queue = TaskQueue(concurrency_provider=lambda: 2,
                          on_event=lambda ev: finished.set()
                          if ev["event"] == "queue-done" else None)
        self.addCleanup(queue.shutdown)
        queue.enqueue(tasks)
        self.assertTrue(finished.wait(15))
        self.assertEqual([t.status for t in tasks], ["done", "done"])
        self.assertNotEqual(tasks[0].output_path, tasks[1].output_path)
        pixels = []
        for task in tasks:
            result = subprocess.run(["ffmpeg", "-v", "error", "-i", task.output_path,
                                     "-vf", "scale=1:1", "-frames:v", "1",
                                     "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                                    check=True, capture_output=True, timeout=10)
            pixels.append(result.stdout)
        self.assertGreater(pixels[0][0], pixels[0][2] + 100)
        self.assertGreater(pixels[1][2], pixels[1][0] + 100)
        self.assertEqual(list(self.root.glob("*.ffgui-tmp.*")), [])

    def test_real_running_cancel_removes_partial_output_and_stops_workers(self):
        progress = threading.Event()
        task = Task("live", {"name": "live", "ext": "mp4", "args": [
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]},
            str(self.root), duration=30, custom_input=True,
            input_args=["-re", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=20"])
        queue = TaskQueue(on_event=lambda ev: progress.set()
                          if ev.get("percent", 0) > 0 else None)
        self.addCleanup(queue.shutdown)
        queue.enqueue([task])
        self.assertTrue(progress.wait(8), task.error)
        self.assertTrue(queue.shutdown(timeout=5))
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(task._proc)
        self.assertFalse(Path(task.temp_path).exists())
        self.assertFalse(Path(task.output_path).exists())
        with self.assertRaises(RuntimeError):
            queue.enqueue([Task("next", PRESET, str(self.root))])

    def test_subscriber_exceptions_do_not_block_real_ffmpeg(self):
        src = self.source("a", "red")
        task = Task(src, PRESET, str(self.root))

        def broken_callback(_):
            raise RuntimeError("subscriber failed")

        with patch("app.core.runner._LOG.exception") as logged:
            run_task(task, on_progress=broken_callback, on_log=broken_callback)
        self.assertEqual(task.status, "done", task.error)
        self.assertTrue(logged.called)



if __name__ == "__main__":
    unittest.main()
