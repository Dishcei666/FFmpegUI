# -*- coding: utf-8 -*-
"""高级转换与主窗口批量任务、任意扩展名导入的集成回归。"""
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from app.core import settings
    from app.core.runner import Task, _final_args
    from app.ui.main_window import MainWindow
    from app.ui.settings_page import SettingsPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class ConversionWindowRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(settings, "_data", dict(settings._DEFAULTS)).start()
        patch.object(settings, "_settings_path", return_value=str(
            Path(self.temp.name, "settings.json"))).start()
        with patch.object(MainWindow, "_detect_ffmpeg"), \
                patch.object(SettingsPage, "_check"):
            self.win = MainWindow()
        self.addCleanup(self.win.close)
        self.win.queue.enqueue = Mock()

    def add_file(self, name="unusual.media", audio=True, video=True):
        path = str(Path(self.temp.name, name))
        Path(path).touch()
        with patch("app.ui.main_window.threading.Thread"):
            self.win.add_paths([path])
        info = {"duration": 1, "size": 10,
                "video": {"codec": "h264", "w": 160, "h": 120, "fps": 20}
                         if video else None,
                "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2}
                         if audio else None}
        self.win._on_probe_done(path, info, "")
        return path

    def advanced(self):
        self.win.advanced_panel.refresh_capabilities = Mock()
        self.win.advanced_panel.is_ready = Mock(return_value=True)
        self.win.convert_mode.setCurrentIndex(1)

    def test_unusual_and_extensionless_inputs_are_probed(self):
        for name in ("source.mxf", "recording.unknown", "noextension"):
            with self.subTest(name=name):
                path = self.add_file(name)
                self.assertEqual(self.win._files[path]["status"], "ready")
        self.win.add_paths([self.temp.name, str(Path(self.temp.name, "missing"))])
        self.assertEqual(len(self.win._files), 3)

    def test_no_audio_or_video_is_rejected_after_probe(self):
        path = self.add_file(audio=False, video=False)
        self.assertEqual(self.win._files[path]["status"], "error")
        self.assertIsNone(self.win._files[path]["info"])
        self.assertFalse(self.win.start_btn.isEnabled())

    def test_presets_still_work_without_capability_detection(self):
        self.add_file()
        with patch.object(self.win.advanced_panel, "refresh_capabilities") as detect:
            self.win.start()
        tasks = self.win.queue.enqueue.call_args.args[0]
        self.assertEqual(tasks[0].preset["id"], "copy-mp4")
        detect.assert_not_called()

    def test_entire_batch_is_validated_before_any_task_is_queued(self):
        first, second = self.add_file("a.mp4"), self.add_file("b.wav", video=False)
        self.advanced()
        self.win.advanced_panel.build_preset = Mock(side_effect=[
            {"name": "MKV", "ext": "mkv", "args": ["-c", "copy"]},
            ValueError("输入不含视频")])
        self.win.start()
        self.win.queue.enqueue.assert_not_called()
        self.assertFalse(self.win._busy)
        self.assertEqual([self.win._files[p]["status"] for p in (first, second)],
                         ["ready", "ready"])

    def test_advanced_uses_built_preset_and_locks_controls(self):
        path = self.add_file()
        self.advanced()
        preset = {"id": "custom", "name": "MKV / FFV1", "ext": "mkv",
                  "args": ["-c:v", "ffv1"], "allow_hw_override": False}
        self.win.advanced_panel.build_preset = Mock(return_value=preset)
        self.win.start()
        task = self.win.queue.enqueue.call_args.args[0][0]
        self.assertEqual(task.preset, preset)
        self.assertEqual(task.input_path, path)
        self.assertFalse(self.win.convert_mode.isEnabled())
        self.assertFalse(any(b.isEnabled() for b in self.win.preset_group.buttons()))

    def test_advanced_requires_current_capabilities(self):
        self.add_file()
        self.advanced()
        self.win.advanced_panel.is_ready.return_value = False
        self.win._refresh()
        self.assertFalse(self.win.start_btn.isEnabled())
        self.win.start()
        self.win.queue.enqueue.assert_not_called()

    def test_starting_tasks_cannot_be_removed_or_cleared(self):
        path = self.add_file()
        item = self.win._files[path]
        item["status"] = "starting"
        item["row"].set_state("starting")
        self.assertFalse(item["row"].btn_del.isVisible())
        self.win._remove_file(path)
        self.win._clear_finished()
        self.assertIn(path, self.win._files)

        audio_path = str(Path(self.temp.name, "audio.wav"))
        Path(audio_path).touch()
        with patch("app.ui.audio_page.threading.Thread"):
            self.win.audio_page.add_paths([audio_path])
        self.win.audio_page._on_probe_done(audio_path, item["info"], "")
        audio_item = self.win.audio_page._files[audio_path]
        audio_item["status"] = "starting"
        audio_item["row"].set_state("starting")
        self.win.audio_page._remove_file(audio_path)
        self.assertIn(audio_path, self.win.audio_page._files)

        clip = self.win.clip_page
        clip._src = path
        clip._info = item["info"]
        clip._insert_segment(0, 0.5)
        row = clip._segments[0]["row"]
        row.set_state("starting")
        self.assertFalse(row.btn_del.isVisible())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg/ffprobe")
    def test_real_advanced_conversion_runs_from_probe_through_queue_to_ui(self):
        from app.core.probe import probe_file
        source = str(Path(self.temp.name, "source.unusual"))
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x120:r=20:d=0.4", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=0.4", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", "-f", "matroska", source],
                       check=True, capture_output=True, timeout=15)
        self.win.add_paths([source])
        self.win.convert_mode.setCurrentIndex(1)

        def wait_until(predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("后台操作未在期限内完成")

        panel = self.win.advanced_panel
        wait_until(lambda: panel.is_ready() and self.win._files[source]["status"] == "ready")
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("webm"))
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("libvpx-vp9"))
        panel.audio_combo.setCurrentIndex(panel.audio_combo.findData("libopus"))
        self.assertTrue(panel.is_ready())
        del self.win.queue.enqueue           # 使用真实队列与真实 FFmpeg
        self.win.start()
        wait_until(lambda: not self.win._busy)
        self.assertEqual(self.win._files[source]["status"], "done")
        output = self.win._files[source]["row"]._output
        info = probe_file(output)
        self.assertEqual(Path(output).suffix, ".webm")
        self.assertEqual(info["video"]["codec"], "vp9")
        self.assertEqual(info["audio"]["codec"], "opus")
        self.assertTrue(self.win.convert_mode.isEnabled())

    def test_explicit_software_encoder_ignores_global_gpu_preference(self):
        settings.set("prefer_gpu", True)
        settings.set("hw_encoder", "h264_qsv")
        task = Task(str(Path(self.temp.name, "source.mp4")),
                    {"name": "custom", "ext": "mkv", "allow_hw_override": False,
                     "args": ["-c:v", "libx264", "-crf", "23"]}, self.temp.name)
        self.assertIn("libx264", _final_args(task))
        task.preset.pop("allow_hw_override")
        self.assertIn("h264_qsv", _final_args(task))


if __name__ == "__main__":
    unittest.main()
