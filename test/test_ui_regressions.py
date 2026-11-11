# -*- coding: utf-8 -*-
"""离屏 UI 回归：python -m unittest discover -s test -p test_ui_regressions.py。"""
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from app.core import gpu, settings
    from app.ui.main_window import MainWindow
    from app.ui.settings_page import SettingsPage
    from app.ui.widgets import resolve_ffplay, set_page_out_dir
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "需要 PySide6 执行真实 Qt 回归")
class UiRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ffgui-ui-test-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(settings, "_data", dict(settings._data)).start()
        patch.object(settings, "_settings_path",
                     return_value=str(Path(self.tmp.name, "settings.json"))).start()
        patch.object(settings, "save").start()
        settings.set("out_dir", "")
        settings.set("prefer_gpu", False)
        with patch.object(MainWindow, "_detect_ffmpeg"), \
                patch.object(SettingsPage, "_check"):
            self.win = MainWindow()
        patch.object(self.win.queue, "shutdown", return_value=True, create=True).start()
        self.addCleanup(self.win.close)

    @staticmethod
    def info(duration=8.0):
        return {"duration": duration, "container": "mp4", "size": 2_000_000_000,
                "video": {"w": 640, "h": 360, "codec": "h264"},
                "audio": {"codec": "aac"}}

    def test_default_directory_updates_existing_pages_and_preserves_manual_choice(self):
        default = str(Path(self.tmp.name, "default"))
        custom = str(Path(self.tmp.name, "custom"))
        Path(default).mkdir()
        Path(custom).mkdir()
        set_page_out_dir(self.win.clip_page, custom)
        self.win.settings_page._save_outdir(default)
        followers = (self.win, self.win.audio_page, self.win.merge_page,
                     self.win.watermark_page)
        self.assertTrue(all(page._out_dir == default for page in followers))
        self.assertEqual(self.win.clip_page._out_dir, custom)
        self.win.settings_page._save_outdir("")
        self.assertTrue(all(page._out_dir is None for page in followers))
        self.assertIn("与源文件相同", self.win.watermark_page.dir_chip.text())
        self.assertEqual(self.win.clip_page._out_dir, custom)

    def test_clip_ignores_old_probe_keyframes_and_crop_results(self):
        page = self.win.clip_page
        with patch("app.ui.clip_page.threading.Thread"):
            page.load_file("old.mp4")
            old = page._load_request
            page.load_file("current.mp4")
            current = page._load_request
        page._on_file_probed(old, "old.mp4", False, None, "obsolete failure")
        self.assertEqual(page._src, "current.mp4")
        page._on_file_probed(current, "current.mp4", True, self.info(), "")
        page._on_file_probed(old, "old.mp4", True, self.info(99), "")
        self.assertEqual(page._info["duration"], 8)
        with patch.object(page.timeline, "set_keyframes") as update:
            page._on_keyframes(old, "old.mp4", [0, 9])
            update.assert_not_called()
            page._on_keyframes(current, "current.mp4", [0, 2])
            update.assert_called_once_with([0, 2])
        page.edit_crop.clear()
        page._on_crop_done(old, "old.mp4", "100:100:0:0")
        self.assertEqual(page.edit_crop.text(), "")

    def test_watermark_ignores_old_probe_after_switching_files(self):
        page = self.win.watermark_page
        with patch("app.ui.watermark_page.threading.Thread"):
            page.load_file("old.mp4")
            old = page._load_request
            page.load_file("current.mp4")
            current = page._load_request
        page._on_file_probed(current, "current.mp4", True, self.info(), "")
        page._on_file_probed(old, "old.mp4", False, None, "obsolete failure")
        self.assertEqual(page._src, "current.mp4")
        self.assertEqual(page._info["duration"], 8)

    def test_merge_lists_audio_only_container_as_error_and_does_not_start(self):
        page = self.win.merge_page
        with patch("app.ui.merge_page.threading.Thread"):
            page.add_paths(["video.mp4", "audio-only.mp4"])
        page._on_probe_done("video.mp4", self.info(), "")
        audio_only = self.info()
        audio_only["video"] = None
        page._on_probe_done("audio-only.mp4", audio_only, "")
        self.assertEqual(len(page._items), 2)
        self.assertIn("没有视频流", page._items[1]["error"])
        self.assertIn("没有视频流", page.rows_layout.itemAt(1).widget().meta.text())
        self.assertFalse(page.btn_start.isEnabled())
        self.win.queue.enqueue = Mock()
        page.start()
        self.win.queue.enqueue.assert_not_called()

    def test_active_clip_or_watermark_job_cannot_replace_source(self):
        for page in (self.win.clip_page, self.win.watermark_page):
            page._src = "active.mp4"
            page._pending.add("active-job")
            page.load_file("replacement.mp4")
            self.assertEqual(page._src, "active.mp4")
            self.assertEqual(page._pending, {"active-job"})
            page._pending.clear()
        page = self.win.watermark_page
        page._ai_busy = True
        page.load_file("replacement.mp4")
        self.assertEqual(page._src, "active.mp4")
        page._ai_busy = False

    def test_segment_and_single_merge_progress_use_output_duration(self):
        page = self.win.clip_page
        page._src = str(Path(self.tmp.name, "video.mp4"))
        page._info = self.info()
        page._out_dir = self.tmp.name
        page._insert_segment(0, 4)
        page.spin_speed.setValue(2.0)
        self.win.queue.enqueue = Mock()
        page._export_segments()
        task = self.win.queue.enqueue.call_args.args[0][0]
        self.assertEqual(task.duration, 2.0)
        page._pending.clear()
        page._export_single_merge((0, 4))
        task = self.win.queue.enqueue.call_args.args[0][0]
        self.assertEqual(task.duration, 2.0)
        page._pending.clear()

    def test_ffplay_resolves_beside_configured_ffmpeg(self):
        ffmpeg = Path(self.tmp.name, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffplay = Path(self.tmp.name, "ffplay.exe" if os.name == "nt" else "ffplay")
        ffmpeg.touch()
        ffplay.touch()
        with patch.dict(os.environ, {"FFPLAY_PATH": ""}), \
                patch.object(settings, "ffmpeg", return_value=str(ffmpeg)):
            self.assertEqual(resolve_ffplay(), str(ffplay))
        with patch.dict(os.environ, {"FFPLAY_PATH": "custom-player"}):
            self.assertEqual(resolve_ffplay(), "custom-player")

    def test_nonzero_ffmpeg_version_exit_is_not_ready(self):
        with patch("app.ui.main_window.subprocess.run", return_value=
                   subprocess.CompletedProcess(["ffmpeg"], 1, b"", b"failed")), \
                patch("app.ui.main_window.threading.Thread") as worker:
            worker.return_value.start.side_effect = \
                lambda: worker.call_args.kwargs["target"]()
            self.win._detect_ffmpeg()
        self.assertIn("未检测到", self.win.foot_status.text())

    def test_gpu_detection_preserves_each_result_icon(self):
        page = self.win.settings_page
        page._on_gpu_each("h264_nvenc", True)
        page._on_gpu_each("h264_qsv", False)
        text = page.gpu_status.text()
        self.assertIn(f'✓</span> {gpu.LABELS["h264_nvenc"]}', text)
        self.assertIn(f'✗</span> {gpu.LABELS["h264_qsv"]}', text)

    def test_failed_gpu_redetection_removes_obsolete_encoder(self):
        page = self.win.settings_page
        settings.set("hw_encoder", "h264_nvenc")
        settings.set("prefer_gpu", True)
        page._on_gpu_done({"h264_nvenc": False})
        self.assertEqual(settings.get("hw_encoder"), "")
        self.assertFalse(settings.get("prefer_gpu"))
        with patch.object(page, "_check"):
            page._load_values()
        self.assertFalse(page.cb_gpu.isEnabled())

    def test_ai_cancel_passes_event_and_keeps_source_locked_until_worker_finishes(self):
        page = self.win.watermark_page
        page._src = str(Path(self.tmp.name, "video.mp4"))
        page._info = self.info()
        called = threading.Event()

        def transcribe(*args, cancel_event=None, **kwargs):
            called.set()
            if cancel_event.wait(2):
                raise RuntimeError("已取消识别")
            raise AssertionError("未收到取消事件")

        with patch("app.ui.watermark_page.is_downloaded", return_value=True), \
                patch("app.ui.watermark_page.transcribe", side_effect=transcribe):
            page._generate_subtitles()
            self.assertTrue(called.wait(1))
            self.assertTrue(page._ai_busy)
            page._cancel()
            deadline = time.monotonic() + 3
            while page._ai_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertFalse(page._ai_busy)
        self.assertIn("已取消", page.ai_status.text())

    def test_shutdown_cancels_model_download_and_joins_worker(self):
        page = self.win.watermark_page
        called = threading.Event()

        def download(*args, cancel_event=None, **kwargs):
            called.set()
            if cancel_event.wait(2):
                raise RuntimeError("已取消下载")
            raise AssertionError("未收到关闭取消事件")

        with patch("app.ui.watermark_page.download_model", side_effect=download):
            page._download_model()
            self.assertTrue(called.wait(1))
            page.shutdown()
        self.assertTrue(page._dl_cancel.is_set())
        self.assertFalse(page._dl_thread.is_alive())

    def test_close_stops_queue_before_deleting_filter_resources(self):
        order = []
        page = self.win.watermark_page
        page._cleanup = lambda: order.append("cleanup")
        page.shutdown(queue_stopped=False)
        self.assertEqual(order, [])
        self.win.queue.shutdown.side_effect = lambda **kwargs: \
            order.append("queue-stopped") or True
        self.win.close()
        self.assertEqual(order, ["queue-stopped", "cleanup"])
        self.assertIsNone(page._cleanup)


if __name__ == "__main__":
    unittest.main()
