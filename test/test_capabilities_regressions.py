# -*- coding: utf-8 -*-
"""FFmpeg 能力列表、缓存与所选编码器探测的回归测试。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.core import capabilities


MUXERS = """File formats:
 D. = Demuxing supported
 .E = Muxing supported
 --
  E mp4,mov         QuickTime / MOV
 DE matroska,webm   Matroska / WebM
  D input_only      Demuxer only
  E null            Null video output
"""
ENCODERS = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 ------
 V....D libx264      libx264 H.264 / AVC (codec h264)
 V..... h264_nvenc   NVIDIA NVENC H.264 encoder (codec h264)
 V....D rawvideo     raw video
 A....D aac          AAC (Advanced Audio Coding)
 A....D libopus      libopus Opus (codec opus)
 S..... srt          SubRip subtitle (codec subrip)
"""


class CapabilityParsingTests(unittest.TestCase):
    def test_muxer_aliases_are_expanded_and_headers_are_excluded(self):
        self.assertEqual(capabilities._parse_muxers(MUXERS), {
            "mp4": "QuickTime / MOV", "mov": "QuickTime / MOV",
            "matroska": "Matroska / WebM", "webm": "Matroska / WebM",
            "null": "Null video output"})
        modern = " D.. = Demuxing supported\n .E. = Muxing supported\n  E  mp4 MP4 format\n"
        self.assertEqual(capabilities._parse_muxers(modern), {"mp4": "MP4 format"})

    def test_encoder_types_and_canonical_codecs_are_parsed(self):
        result = capabilities._parse_encoders(ENCODERS)
        self.assertEqual(len(result), 6)
        self.assertEqual(result["libx264"]["type"], "video")
        self.assertEqual(result["h264_nvenc"]["codec"], "h264")
        self.assertEqual(result["rawvideo"]["codec"], "rawvideo")
        self.assertEqual(result["libopus"]["type"], "audio")
        self.assertEqual(result["libopus"]["codec"], "opus")
        self.assertEqual(result["srt"]["type"], "subtitle")
        self.assertEqual(result["srt"]["codec"], "subrip")
        self.assertNotIn("=", result)


class CapabilityDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.exe = Path(self.temp.name, "ffmpeg.exe")
        self.exe.write_bytes(b"placeholder")
        capabilities.invalidate_cache()
        self.addCleanup(capabilities.invalidate_cache)

    def result(self, cmd, **kwargs):
        if "-version" in cmd:
            stdout = "ffmpeg version test-build\nbuilt with test\n"
        elif "-muxers" in cmd:
            stdout = MUXERS
        elif "-encoders" in cmd:
            stdout = ENCODERS
        else:
            stdout = ""
        return subprocess.CompletedProcess(cmd, 0, stdout.encode(), b"")

    def detect(self, requested="ffmpeg"):
        with patch.object(capabilities.shutil, "which", return_value=str(self.exe)):
            return capabilities.detect_capabilities(requested)

    def test_lists_are_queried_without_testing_hardware(self):
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            result = self.detect()
        self.assertEqual(result["command"], "ffmpeg")
        self.assertEqual(result["resolved_command"], str(self.exe.resolve()))
        self.assertEqual(result["version"], "ffmpeg version test-build")
        self.assertEqual(len(run.call_args_list), 3)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         ["-version", "-muxers", "-encoders"])
        self.assertTrue(all(call.kwargs["timeout"] > 0 for call in run.call_args_list))

    def test_cached_results_are_isolated_and_keep_requested_alias(self):
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            result = self.detect()
            result["muxers"].clear()
            result["encoders"]["libx264"]["codec"] = "changed"
            cached = self.detect(str(self.exe))
            self.assertEqual(run.call_count, 3)
        self.assertEqual(cached["command"], str(self.exe))
        self.assertIn("mp4", cached["muxers"])
        self.assertEqual(cached["encoders"]["libx264"]["codec"], "h264")

    def test_changed_executable_invalidates_cache(self):
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            self.detect()
            stat = self.exe.stat()
            os.utime(self.exe, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            self.detect()
            self.assertEqual(run.call_count, 6)

    def test_different_resolved_path_does_not_reuse_cache(self):
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            self.detect()
            second = self.exe.with_name("different.exe")
            second.write_bytes(b"other")
            with patch.object(capabilities.shutil, "which", return_value=str(second)):
                capabilities.detect_capabilities("ffmpeg")
            self.assertEqual(run.call_count, 6)

    def test_invalidate_selected_command_forces_refresh(self):
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            self.detect()
            with patch.object(capabilities.shutil, "which", return_value=str(self.exe)):
                capabilities.invalidate_cache("ffmpeg")
            self.detect()
            self.assertEqual(run.call_count, 6)

    def test_default_command_uses_current_settings(self):
        with patch.object(capabilities.settings, "ffmpeg", return_value="chosen-ffmpeg"), \
                patch.object(capabilities.shutil, "which", return_value=str(self.exe)) as which, \
                patch.object(capabilities.subprocess, "run", side_effect=self.result):
            result = capabilities.detect_capabilities()
        which.assert_called_once_with("chosen-ffmpeg")
        self.assertEqual(result["command"], "chosen-ffmpeg")

    def test_missing_command_and_invalid_paths_raise_clear_errors(self):
        with patch.object(capabilities.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "未找到"):
                capabilities.detect_capabilities("missing")
        for command in ("", "  ", "bad\x00path", 42, b"ffmpeg"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(RuntimeError, "路径无效"):
                    capabilities.detect_capabilities(command)

    def test_timeout_permission_and_nonzero_exit_are_reported(self):
        failures = [(subprocess.TimeoutExpired("ffmpeg", 20), "超时"),
                    (PermissionError("access denied"), "无法启动")]
        for failure, message in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(capabilities.subprocess, "run", side_effect=failure):
                    with self.assertRaisesRegex(RuntimeError, message):
                        self.detect()
        result = subprocess.CompletedProcess([], 3, b"", b"specific failure")
        with patch.object(capabilities.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "退出代码 3.*specific failure"):
                self.detect()

    def test_malformed_output_is_not_cached(self):
        invalid = subprocess.CompletedProcess([], 0, b"not ffmpeg", b"")
        with patch.object(capabilities.subprocess, "run", return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, "版本"):
                self.detect()
        with patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            self.assertIn("mp4", self.detect()["muxers"])
            self.assertEqual(run.call_count, 3)

    def test_unrecognizable_lists_are_rejected(self):
        def empty_lists(cmd, **kwargs):
            if "-version" in cmd:
                return self.result(cmd, **kwargs)
            return subprocess.CompletedProcess(cmd, 0, b"header only", b"")
        with patch.object(capabilities.subprocess, "run", side_effect=empty_lists):
            with self.assertRaisesRegex(RuntimeError, "列表"):
                self.detect()

    def test_selected_encoder_is_the_only_one_tested(self):
        with patch.object(capabilities.shutil, "which", return_value=str(self.exe)), \
                patch.object(capabilities.subprocess, "run", side_effect=self.result) as run:
            ok, detail = capabilities.test_encoder("h264_nvenc")
        self.assertTrue(ok, detail)
        self.assertEqual(run.call_count, 1)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertNotIn("-encoders", cmd)
        self.assertEqual(cmd[-3:], ["-f", "null", "-"])

    def test_audio_and_subtitle_tests_have_appropriate_inputs_and_cleanup(self):
        subtitle_paths = []

        def execute(cmd, **kwargs):
            if "-c:s" in cmd:
                path = Path(cmd[cmd.index("-i") + 1])
                self.assertTrue(path.exists())
                self.assertIn("Test", path.read_text(encoding="utf-8"))
                subtitle_paths.append(path)
            if "-c:a" in cmd:
                self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
                self.assertIn("anullsrc", cmd[cmd.index("-i") + 1])
            return self.result(cmd, **kwargs)

        with patch.object(capabilities.shutil, "which", return_value=str(self.exe)), \
                patch.object(capabilities.subprocess, "run", side_effect=execute):
            self.assertTrue(capabilities.test_encoder("aac", "audio")[0])
            self.assertTrue(capabilities.test_encoder("srt", "subtitle")[0])
        self.assertTrue(subtitle_paths)
        self.assertFalse(subtitle_paths[0].parent.exists())

    def test_encoder_failure_returns_detail_and_does_not_raise(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"device driver unavailable")
        with patch.object(capabilities.shutil, "which", return_value=str(self.exe)), \
                patch.object(capabilities.subprocess, "run", return_value=failed):
            ok, detail = capabilities.test_encoder("h264_amf")
        self.assertFalse(ok)
        self.assertIn("device driver unavailable", detail)

    def test_long_encoder_failure_keeps_initial_driver_error(self):
        stderr = b"driver DLL missing\n" + b"generic encoder failure\n" * 12 + b"Nothing written\n"
        failed = subprocess.CompletedProcess([], 1, b"", stderr)
        with patch.object(capabilities.shutil, "which", return_value=str(self.exe)), \
                patch.object(capabilities.subprocess, "run", return_value=failed):
            ok, detail = capabilities.test_encoder("h264_amf")
        self.assertFalse(ok)
        self.assertIn("driver DLL missing", detail)
        self.assertIn("Nothing written", detail)

    def test_invalid_encoder_or_kind_does_not_spawn(self):
        with patch.object(capabilities.subprocess, "run") as run:
            for encoder, kind in (("-y", "video"), ("x;bad", "video"), ("aac", "data")):
                self.assertFalse(capabilities.test_encoder(encoder, kind)[0])
        run.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg"), "需要本机 FFmpeg")
class RealCapabilityTests(unittest.TestCase):
    def test_real_lists_and_software_encoder_probes(self):
        result = capabilities.detect_capabilities("ffmpeg")
        self.assertIn("mp4", result["muxers"])
        self.assertEqual(result["encoders"]["libx264"]["codec"], "h264")
        for encoder, kind in (("libx264", "video"), ("aac", "audio"), ("srt", "subtitle")):
            if encoder not in result["encoders"]:
                continue
            with self.subTest(encoder=encoder):
                ok, detail = capabilities.test_encoder(encoder, kind, "ffmpeg")
                self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
