# -*- coding: utf-8 -*-
"""设置、硬件参数和探测错误路径回归测试；仅使用标准库。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core import gpu, probe, settings
from app.core.presets import get_preset


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config" / "settings.json"
        self.original = dict(settings._data)
        self.path_patch = patch.object(settings, "_settings_path",
                                       return_value=str(self.path))
        self.path_patch.start()
        settings._data = dict(settings._DEFAULTS)

    def tearDown(self):
        with settings._lock:
            settings._data = self.original
        self.path_patch.stop()
        self.tmp.cleanup()

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_non_object_json_uses_defaults(self):
        for value in (None, [], ["concurrency"], 2, True, "bad"):
            with self.subTest(value=value):
                self.write(value)
                self.assertEqual(settings.load(), settings._DEFAULTS)

    def test_invalid_fields_fall_back_individually(self):
        invalid = {
            "ffmpeg_path": ["ffmpeg"], "ffprobe_path": "bad\x00path",
            "out_dir": False, "concurrency": True, "prefer_gpu": "false",
            "hw_encoder": "unknown", "extra": "ignored",
        }
        self.write(invalid)
        self.assertEqual(settings.load(), settings._DEFAULTS)
        invalid.update(out_dir="有效目录", concurrency=4)
        self.write(invalid)
        result = settings.load()
        self.assertEqual(result["out_dir"], "有效目录")
        self.assertEqual(result["concurrency"], 4)
        self.assertNotIn("extra", result)

    def test_concurrency_requires_integer_in_range(self):
        for value in (0, 5, -1, "2", 2.0, None, True):
            with self.subTest(value=value):
                self.write({"concurrency": value})
                self.assertEqual(settings.load()["concurrency"], 1)

    def test_malformed_json_and_encoding_use_defaults(self):
        self.path.parent.mkdir(parents=True)
        for content in (b'{"concurrency":', b"\xff\xfe"):
            self.path.write_bytes(content)
            self.assertEqual(settings.load(), settings._DEFAULTS)

    def test_utf8_bom_is_supported_and_load_returns_copy(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"concurrency": 3}', encoding="utf-8-sig")
        loaded = settings.load()
        self.assertEqual(loaded["concurrency"], 3)
        loaded["concurrency"] = 99
        self.assertEqual(settings.get("concurrency"), 3)

    def test_save_round_trip_is_atomic_and_leaves_no_temp_file(self):
        settings.set("out_dir", "中文输出目录")
        settings.set("concurrency", 3)
        self.assertEqual(settings.load()["out_dir"], "中文输出目录")
        self.assertEqual(settings.get("concurrency"), 3)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_failure_preserves_disk_and_memory(self):
        settings.set("concurrency", 2)
        original_file = self.path.read_bytes()
        with patch.object(settings.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                settings.set("concurrency", 4)
        self.assertEqual(self.path.read_bytes(), original_file)
        self.assertEqual(settings.get("concurrency"), 2)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_write_failure_preserves_original_file(self):
        settings.set("out_dir", "original")
        original_file = self.path.read_bytes()
        with patch.object(settings.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                settings.reset()
        self.assertEqual(self.path.read_bytes(), original_file)
        self.assertEqual(settings.get("out_dir"), "original")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_setter_never_changes_config(self):
        with self.assertRaises(ValueError):
            settings.set("concurrency", "bad")
        with self.assertRaises(KeyError):
            settings.set("unknown", 1)
        self.assertEqual(settings.get("concurrency"), 1)
        self.assertFalse(self.path.exists())

    def test_concurrent_updates_do_not_lose_other_keys(self):
        updates = {"out_dir": "output", "ffmpeg_path": "ffmpeg-custom",
                   "ffprobe_path": "ffprobe-custom", "concurrency": 4}
        errors = []

        def update(key, value):
            try:
                settings.set(key, value)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=update, args=item)
                   for item in updates.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual({key: settings.load()[key] for key in updates}, updates)

    def test_empty_environment_variables_use_default_commands(self):
        with patch.dict(os.environ, {"FFMPEG_PATH": "", "FFPROBE_PATH": ""}):
            self.assertEqual(settings.ffmpeg(), "ffmpeg")
            self.assertEqual(settings.ffprobe(), "ffprobe")


class HardwareArgsTests(unittest.TestCase):
    def test_amf_preserves_distinct_quantization_levels(self):
        for preset_id, expected in (("x264-hq", "18"), ("x264-compress", "26")):
            with self.subTest(preset=preset_id):
                original = get_preset(preset_id)["args"]
                args = gpu.apply_hw_args(original, "h264_amf")
                self.assertEqual(args[args.index("-rc") + 1], "cqp")
                for flag in ("-qp_i", "-qp_p", "-qp_b"):
                    self.assertEqual(args[args.index(flag) + 1], expected)
                self.assertNotIn("-crf", args)
                self.assertNotIn("-preset", args)
                self.assertIn("-crf", original)

    def test_unknown_encoder_and_stream_copy_are_unchanged(self):
        x264 = get_preset("x264-hq")["args"]
        self.assertEqual(gpu.apply_hw_args(x264, "unknown"), x264)
        copy = get_preset("copy-mp4")["args"]
        self.assertEqual(gpu.apply_hw_args(copy, "h264_amf"), copy)


class ProbeTests(unittest.TestCase):
    def run_probe(self, stdout=b"{}", stderr=b"", returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with patch.object(probe.subprocess, "run", return_value=result) as run:
            info = probe.probe_file("-sample.mp4")
        return info, run.call_args.args[0]

    def test_error_log_and_input_option_are_enabled(self):
        _, cmd = self.run_probe()
        self.assertEqual(cmd[cmd.index("-v") + 1], "error")
        self.assertEqual(cmd[-2:], ["-i", "-sample.mp4"])

    def test_execution_errors_are_runtime_errors(self):
        for error in (FileNotFoundError(), PermissionError("access denied"),
                      ValueError("bad path"), subprocess.TimeoutExpired("ffprobe", 30)):
            with self.subTest(error=type(error).__name__):
                with patch.object(probe.subprocess, "run", side_effect=error):
                    with self.assertRaises(RuntimeError):
                        probe.probe_file("sample.mp4")

    def test_ffprobe_error_message_is_preserved(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid data found"):
            self.run_probe(stderr=b"Invalid data found", returncode=1)

    def test_malformed_output_is_runtime_error(self):
        for output in (b"not json", b"\xff", b"null", b"[]",
                       b'{"streams": [null]}', b'{"format": "bad"}'):
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    self.run_probe(stdout=output)

    def test_stream_metadata_ignores_cover_art_and_preserves_compatibility_fields(self):
        data = {"format": {"duration": "2.5"}, "streams": [
            {"codec_type": "video", "codec_name": "mjpeg",
             "disposition": {"attached_pic": 1}},
            {"codec_type": "video", "codec_name": "h264", "width": 640,
             "height": 360, "avg_frame_rate": "0/0", "r_frame_rate": "30000/1001",
             "pix_fmt": "yuv420p", "profile": "High", "level": 31,
             "time_base": "1/30000"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
             "channels": 2, "channel_layout": "stereo", "sample_fmt": "fltp",
             "time_base": "1/48000"},
        ]}
        info, _ = self.run_probe(stdout=json.dumps(data).encode())
        self.assertEqual(info["video"]["codec"], "h264")
        self.assertEqual(info["video"]["fps"], 29.97)
        self.assertEqual(info["video"]["frame_rate"], "30000/1001")
        self.assertEqual(info["video"]["pix_fmt"], "yuv420p")
        self.assertEqual(info["audio"]["channel_layout"], "stereo")
        self.assertEqual(info["audio"]["time_base"], "1/48000")

    def test_non_finite_values_do_not_poison_progress(self):
        info = probe._parse({"format": {"duration": "NaN", "size": "inf"},
                             "streams": []}, "sample")
        self.assertEqual(info["duration"], 0)
        self.assertEqual(info["size"], 0)
        self.assertEqual(probe._fps("inf"), 0)
        self.assertEqual(probe._fps("-30"), 0)


if __name__ == "__main__":
    unittest.main()
