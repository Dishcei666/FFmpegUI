# -*- coding: utf-8 -*-
"""无界面 Python/JSON API 的请求校验、批处理和真实转换回归。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import api
from app.core import settings


def _caps():
    return {"command": "ffmpeg", "version": "test",
            "muxers": {"mp4": "MP4", "matroska": "MKV", "mp3": "MP3", "gif": "GIF"},
            "encoders": {"libx264": {"type": "video", "codec": "h264"},
                         "aac": {"type": "audio", "codec": "aac"},
                         "libmp3lame": {"type": "audio", "codec": "mp3"},
                         "h264_nvenc": {"type": "video", "codec": "h264"},
                         "gif": {"type": "video", "codec": "gif"}}}


def _info(path="source.mp4", audio=True):
    return {"path": str(path), "duration": 1,
            "video": {"codec": "h264", "w": 160, "h": 120, "fps": 25},
            "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2} if audio else None}


class ApiValidationRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffgui-api-validation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "源 文件.mp4"
        self.source.write_bytes(b"placeholder")

    def request(self, **extra):
        return {"action": "convert", "inputs": [str(self.source)], **extra}

    def test_strict_request_and_option_types(self):
        requests = [None, [], {}, {"action": []}, {"action": "invalid"},
                    {"action": "formats", "arbitrary": 1},
                    self.request(extra=1), self.request(options=None),
                    self.request(options={"command": "anything"}),
                    self.request(options={"quality": "23"}),
                    self.request(options={"width": 2.0}),
                    self.request(options={"channels": True}),
                    self.request(options={"fps": float("nan")}),
                    self.request(options={"video_encoder": 123}),
                    self.request(concurrency=True), self.request(concurrency=5),
                    self.request(preset="x264-hq", options={}),
                    self.request(preset=None)]
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch.object(api, "Task") as task:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    api.execute(request)
            task.assert_not_called()

    def test_paths_and_duplicate_inputs_are_rejected_before_probe(self):
        requests = [{"action": "probe", "inputs": []},
                    {"action": "probe", "inputs": "source.mp4"},
                    {"action": "probe", "inputs": [123]},
                    {"action": "probe", "inputs": ["http://host/source.mp4"]},
                    {"action": "probe", "inputs": ["pipe:0"]},
                    {"action": "probe", "inputs": ["\x00"]},
                    {"action": "probe", "inputs": [str(self.root)]},
                    self.request(inputs=[str(self.source), str(self.source)]),
                    self.request(inputs=[str(self.source), str(self.root / "missing.mp4")]),
                    self.request(output_dir=str(self.source))]
        with mock.patch.object(api, "probe_file") as probe:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    api.execute(request)
            probe.assert_not_called()

    def test_entire_batch_is_validated_before_tasks_or_directory_creation(self):
        second = self.root / "silent.mp4"
        second.write_bytes(b"placeholder")
        directory = self.root / "must-not-exist"
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", side_effect=[_info(self.source), _info(second, audio=False)]), \
                mock.patch.object(api, "Task") as task:
            with self.assertRaisesRegex(ValueError, "没有音轨"):
                api.execute(self.request(inputs=[str(self.source), str(second)],
                                         output_dir=str(directory), options={"format_id": "mp3"}))
            task.assert_not_called()
            self.assertFalse(directory.exists())

    def test_semantic_option_and_preset_errors_do_not_start_tasks(self):
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch.object(api, "Task") as task:
            for extra in ({"options": {"width": 321}}, {"preset": "unknown"},
                          {"options": {"format_id": "mp4", "video_encoder": "copy", "fps": 10}}):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    api.execute(self.request(**extra))
            task.assert_not_called()

    def test_hardware_is_tested_before_start_and_failure_is_runtime_error(self):
        directory = self.root / "hardware-out"
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch.object(api, "test_encoder", return_value=(False, "no device")) as test, \
                mock.patch.object(api, "Task") as task:
            with self.assertRaisesRegex(RuntimeError, "no device"):
                api.execute(self.request(output_dir=str(directory), options={"video_encoder": "h264_nvenc"}))
            test.assert_called_once_with("h264_nvenc", kind="video")
            task.assert_not_called()
            self.assertFalse(directory.exists())

    def test_directory_creation_failure_is_runtime_error(self):
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch.object(api.os, "makedirs", side_effect=PermissionError("denied")), \
                mock.patch.object(api, "Task") as task:
            with self.assertRaisesRegex(RuntimeError, "denied"):
                api.execute(self.request(output_dir=str(self.root / "denied")))
            task.assert_not_called()

    def test_cancel_during_batch_preflight_stops_before_next_probe(self):
        second = self.root / "second.mp4"
        second.write_bytes(b"placeholder")
        cancel = threading.Event()
        directory = self.root / "cancelled-preflight"

        def probe(path):
            cancel.set()
            return _info(path)

        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", side_effect=probe) as probe_mock, \
                mock.patch.object(api, "Task") as task:
            result = api.execute(self.request(inputs=[str(self.source), str(second)],
                                              output_dir=str(directory)), cancel_event=cancel)
            self.assertTrue(result["cancelled"])
            self.assertEqual(result["results"], [])
            probe_mock.assert_called_once_with(str(self.source))
            task.assert_not_called()
            self.assertFalse(directory.exists())

    def test_read_actions_have_json_safe_stable_schema(self):
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info(self.source)), \
                mock.patch.object(api, "test_encoder", return_value=(True, "tested")):
            formats = api.execute({"action": "formats"})
            self.assertTrue(formats["ok"])
            self.assertIn("video_encoders", formats["formats"][0])
            self.assertIn("audio_encoders", formats["formats"][0])
            self.assertEqual(api.execute({"action": "probe", "inputs": [str(self.source)]})["media"][0]["duration"], 1)
            self.assertEqual(api.execute({"action": "capabilities"})["capabilities"]["version"], "test")
            self.assertTrue(api.execute({"action": "test-encoder", "encoder": "libx264"})["ok"])
            json.dumps(formats, allow_nan=False)
            with self.assertRaises(ValueError):
                api.execute({"action": "test-encoder", "encoder": "-anything"})
            with self.assertRaises(ValueError):
                api.execute({"action": "test-encoder", "encoder": "aac", "kind": "video"})

    def test_execution_failures_return_results_instead_of_raising(self):
        def fail(task, on_progress=None, on_log=None):
            task.status = "error"
            task.error = "encoder failed"

        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch("app.core.queue.run_task", side_effect=fail):
            result = api.execute(self.request())
            self.assertFalse(result["ok"])
            self.assertFalse(result["cancelled"])
            self.assertEqual(result["results"][0]["error"], "encoder failed")
            self.assertEqual(result["results"][0]["output_path"], "")

    def test_keyboard_interrupt_still_shuts_down_queue(self):
        with mock.patch.object(api, "detect_capabilities", return_value=_caps()), \
                mock.patch.object(api, "probe_file", return_value=_info()), \
                mock.patch.object(api, "TaskQueue") as queue:
            queue.return_value.enqueue.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                api.execute(self.request())
            queue.return_value.shutdown.assert_called_once()

    def test_tool_detection_errors_stay_runtime_errors(self):
        with mock.patch.object(api, "detect_capabilities", side_effect=RuntimeError("missing ffmpeg")):
            with self.assertRaisesRegex(RuntimeError, "missing ffmpeg"):
                api.execute({"action": "formats"})


_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(_FFMPEG and _FFPROBE, "需要 ffmpeg/ffprobe")
class FFmpegApiRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ffgui-api-media-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "语音 视频.mp4"
        cls.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25:duration=0.8",
                        "-f", "lavfi", "-i", "sine=frequency=660:duration=0.8",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        "-shortest", str(cls.source)])
        cls.second = cls.root / "另一个视频.mp4"
        shutil.copyfile(cls.source, cls.second)

    @classmethod
    def run_ffmpeg(cls, args):
        proc = subprocess.run([_FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                              capture_output=True, timeout=30)
        if proc.returncode:
            raise AssertionError(proc.stderr.decode("utf-8", "replace"))
        return proc

    def setUp(self):
        self.settings = mock.patch.dict(settings._data, {"ffmpeg_path": _FFMPEG, "ffprobe_path": _FFPROBE,
                                                         "prefer_gpu": True, "hw_encoder": "not-real-hardware"})
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_unicode_batch_conversion_creates_directory_and_reports_outputs(self):
        directory = self.root / "批量 结果"
        events = []
        result = api.execute({"action": "convert", "inputs": [str(self.source), str(self.second)],
                              "output_dir": str(directory), "concurrency": 2,
                              "options": {"format_id": "mp3", "audio_encoder": "libmp3lame", "audio_bitrate": 128}},
                             on_event=events.append)
        self.assertTrue(result["ok"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(len(result["results"]), 2)
        self.assertIn("queue-done", [event["event"] for event in events])
        for item in result["results"]:
            self.assertEqual(item["status"], "done")
            self.assertGreater(item["output_size"], 0)
            self.assertEqual(item["percent"], 100)
            self.assertTrue(Path(item["output_path"]).is_file())
            self.assertEqual(Path(item["output_path"]).parent, directory)
        json.dumps(result, allow_nan=False)

    def test_callbacks_cannot_break_conversion_and_sources_are_not_overwritten(self):
        before = self.source.read_bytes()

        def observer(_event):
            raise RuntimeError("observer failed")

        result = api.execute({"action": "convert", "inputs": [str(self.source)], "preset": "x264-hq"}, on_event=observer)
        self.assertTrue(result["ok"])
        output = Path(result["results"][0]["output_path"])
        self.assertNotEqual(output, self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(output.is_file())

    def test_preset_copy_uses_first_streams_and_disables_global_hardware(self):
        result = api.execute({"action": "convert", "inputs": [str(self.source)],
                              "output_dir": str(self.root / "copy"), "preset": "copy-mp4"})
        self.assertTrue(result["ok"])
        info = api.execute({"action": "probe", "inputs": [result["results"][0]["output_path"]]})["media"][0]
        self.assertEqual(info["video"]["codec"], "h264")
        self.assertEqual(info["audio"]["codec"], "aac")

    def test_pre_cancel_does_not_launch_encoder_or_publish_output(self):
        cancel = threading.Event()
        cancel.set()
        directory = self.root / "pre-cancel"
        with mock.patch.object(api, "detect_capabilities") as caps, \
                mock.patch.object(api, "probe_file") as probe, \
                mock.patch.object(api, "Task") as task:
            result = api.execute({"action": "convert", "inputs": [str(self.source)],
                                  "output_dir": str(directory)}, cancel_event=cancel)
        caps.assert_not_called()
        probe.assert_not_called()
        task.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["results"], [])
        self.assertFalse(directory.exists())

    def test_cancel_running_ffmpeg_cleans_temporary_outputs(self):
        source = self.root / "long.mp4"
        self.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
                        "-c:v", "libx264", "-preset", "ultrafast", str(source)])
        directory = self.root / "running-cancel"
        cancel = threading.Event()

        def observer(event):
            if event["event"] == "log":
                cancel.set()

        result = api.execute({"action": "convert", "inputs": [str(source)],
                              "output_dir": str(directory), "options": {"quality": 18}},
                             on_event=observer, cancel_event=cancel)
        self.assertTrue(cancel.is_set())
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["status"], "cancelled")
        self.assertEqual(list(directory.iterdir()), [])

    def test_api_import_and_formats_work_when_qt_import_is_blocked(self):
        script = ("import importlib.abc, json, sys\n"
                  "class BlockQt(importlib.abc.MetaPathFinder):\n"
                  " def find_spec(self, fullname, path=None, target=None):\n"
                  "  if fullname.startswith(('PySide', 'PyQt')): raise ImportError('Qt forbidden')\n"
                  "sys.meta_path.insert(0, BlockQt())\n"
                  "from app.api import execute\n"
                  "result=execute({'action':'formats'})\n"
                  "assert result['ok'] and result['formats']\n"
                  "assert not any(n.startswith(('PySide','PyQt')) for n in sys.modules)\n"
                  "print(json.dumps({'ok':True,'count':len(result['formats'])}))\n")
        env = dict(os.environ, FFMPEG_PATH=_FFMPEG, FFPROBE_PATH=_FFPROBE,
                   LOCALAPPDATA=str(self.root / "headless-settings"))
        proc = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                              env=env, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertTrue(json.loads(proc.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
