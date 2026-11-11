# -*- coding: utf-8 -*-
"""高级转换面板的真实 Qt 回归；无需硬件、下载或安装依赖。"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    from app.core import settings
    from app.ui.advanced_conversion import AdvancedConversionPanel


def capabilities(command="ffmpeg"):
    return {
        "command": command, "version": "ffmpeg version test-build",
        "muxers": {"mp4": "MP4", "matroska": "Matroska", "mp3": "MP3",
                   "webm": "WebM", "gif": "GIF", "webp": "WebP"},
        "encoders": {
            "libx264": {"type": "video", "codec": "h264", "description": "H264"},
            "h264_nvenc": {"type": "video", "codec": "h264", "description": "NVENC"},
            "ffv1": {"type": "video", "codec": "ffv1", "description": "FFV1"},
            "aac": {"type": "audio", "codec": "aac", "description": "AAC"},
            "libmp3lame": {"type": "audio", "codec": "mp3", "description": "MP3"},
            "gif": {"type": "video", "codec": "gif", "description": "GIF"},
            "prores_ks": {"type": "video", "codec": "prores", "description": "ProRes"},
            "pcm_s16le": {"type": "audio", "codec": "pcm_s16le", "description": "PCM"},
            "libwebp_anim": {"type": "video", "codec": "webp", "description": "WebP"},
            "libsvtav1": {"type": "video", "codec": "av1", "description": "AV1"},
            "libvpx-vp9": {"type": "video", "codec": "vp9", "description": "VP9"},
            "libopus": {"type": "audio", "codec": "opus", "description": "Opus"},
        },
    }


@unittest.skipUnless(HAVE_QT, "需要 PySide6 执行真实 Qt 回归")
class AdvancedUiRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ffgui-advanced-ui-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(settings, "_data", dict(settings._data)).start()
        patch.object(settings, "_settings_path",
                     return_value=str(Path(self.tmp.name, "settings.json"))).start()
        patch.object(settings, "ffmpeg", return_value="ffmpeg").start()
        self.panel = AdvancedConversionPanel()
        self.addCleanup(self.panel.shutdown)

    def ready(self):
        with patch("app.ui.advanced_conversion.detect_capabilities",
                   return_value=capabilities()):
            self.panel.refresh_capabilities()
            self.wait_for(lambda: not self.panel._detecting)
        self.assertTrue(self.panel.is_ready(), self.panel.status.text())

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "后台操作未完成")

    @staticmethod
    def info():
        return {"video": {"codec": "h264", "w": 640, "h": 360},
                "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2},
                "duration": 8.0}

    def test_constructor_is_idle_until_explicit_detection(self):
        self.assertFalse(self.panel._detecting)
        self.assertFalse(self.panel.is_ready())
        self.assertEqual(self.panel.format_combo.count(), 0)

    def test_background_detection_selects_mp4_x264_aac_and_builds_preset(self):
        self.ready()
        self.assertEqual(self.panel.format_combo.currentData(), "mp4")
        self.assertEqual(self.panel.video_combo.currentData(), "libx264")
        self.assertEqual(self.panel.audio_combo.currentData(), "aac")
        preset = self.panel.build_preset(self.info())
        self.assertEqual(preset["ext"], "mp4")
        self.assertFalse(preset["allow_hw_override"])
        self.assertIn("libx264", preset["args"])

    def test_failed_detection_allows_retry(self):
        with patch("app.ui.advanced_conversion.detect_capabilities",
                   side_effect=RuntimeError("程序路径不可用")):
            self.panel.refresh_capabilities()
            self.wait_for(lambda: not self.panel._detecting)
        self.assertFalse(self.panel.is_ready())
        self.assertIn("路径不可用", self.panel.status.text())
        self.assertTrue(self.panel.refresh_btn.isEnabled())
        self.ready()

    def test_stale_detection_and_changed_program_cannot_be_used(self):
        with patch("app.ui.advanced_conversion.threading.Thread"):
            self.panel.refresh_capabilities()
            old_request = self.panel._request
            self.panel.refresh_capabilities(force=True)
            request = self.panel._request
        self.panel._on_detected(old_request, "ffmpeg", capabilities(), "")
        self.assertIsNone(self.panel._caps)
        self.panel._on_detected(request, "ffmpeg", capabilities(), "")
        self.assertTrue(self.panel.is_ready())
        with patch.object(settings, "ffmpeg", return_value="other-ffmpeg"):
            self.assertFalse(self.panel.is_ready())
            with self.assertRaises(ValueError):
                self.panel.build_preset(self.info())

    def test_copy_disables_transforms_and_does_not_leak_old_values(self):
        self.ready()
        panel = self.panel
        panel.width_spin.setValue(1280)
        panel.fps_spin.setValue(60)
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("copy"))
        panel.audio_combo.setCurrentIndex(panel.audio_combo.findData("copy"))
        for control in (*panel._video_controls, *panel._audio_controls):
            self.assertFalse(control.isEnabled())
        with patch("app.ui.advanced_conversion.build_conversion", return_value={}) as build:
            panel.build_preset(self.info())
        options = build.call_args.args[0]
        self.assertEqual(options["width"], 0)
        self.assertEqual(options["fps"], 0)
        self.assertEqual(options["sample_rate"], 0)
        self.assertEqual(options["channels"], 0)
        self.assertTrue(self.panel.build_preset(self.info()))

    def test_lossless_and_prores_controls_match_encoder_parameter_semantics(self):
        self.ready()
        panel = self.panel
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("mkv"))
        panel.video_bitrate_spin.setValue(2000)
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("ffv1"))
        self.assertFalse(panel.quality_spin.isEnabled())
        self.assertFalse(panel.video_bitrate_spin.isEnabled())
        self.assertEqual(panel.video_bitrate_spin.value(), 2000)
        self.assertNotIn("-b:v", panel.build_preset(self.info())["args"])
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("prores_ks"))
        self.assertTrue(panel.quality_spin.isEnabled())
        self.assertFalse(panel.video_bitrate_spin.isEnabled())
        self.assertIn("ProRes", panel.quality_spin.toolTip())
        self.assertNotIn("-b:v", panel.build_preset(self.info())["args"])
        panel.audio_combo.setCurrentIndex(panel.audio_combo.findData("pcm_s16le"))
        self.assertFalse(panel.audio_bitrate_spin.isEnabled())
        self.assertTrue(panel.sample_rate_combo.isEnabled())
        self.assertNotIn("-b:a", panel.build_preset(self.info())["args"])
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("webp"))
        self.assertFalse(panel.video_bitrate_spin.isEnabled())
        self.assertEqual(panel.video_bitrate_spin.value(), 2000)
        self.assertNotIn("-b:v", panel.build_preset(self.info())["args"])

    def test_webm_fallback_prefers_vp9_opus_over_slow_av1_default(self):
        self.ready()
        panel = self.panel
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("webm"))
        self.assertEqual(panel.video_combo.currentData(), "libvpx-vp9")
        self.assertEqual(panel.audio_combo.currentData(), "libopus")
        self.assertTrue(panel.is_ready())

    def test_audio_only_and_animation_profiles_build_without_hidden_tracks(self):
        self.ready()
        panel = self.panel
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("mp3"))
        self.assertEqual(panel.video_combo.currentData(), "none")
        self.assertTrue(panel.is_ready())
        self.assertIn("-vn", panel.build_preset(self.info())["args"])
        panel.format_combo.setCurrentIndex(panel.format_combo.findData("gif"))
        self.assertEqual(panel.audio_combo.currentData(), "none")
        self.assertFalse(panel.quality_spin.isEnabled())
        self.assertIn("-an", panel.build_preset(self.info())["args"])

    def test_hardware_listing_requires_successful_real_test(self):
        self.ready()
        panel = self.panel
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("h264_nvenc"))
        self.assertFalse(panel.is_ready())
        self.assertTrue(panel.test_btn.isEnabled())
        with patch("app.ui.advanced_conversion.test_encoder",
                   return_value=(False, "没有可用设备")):
            panel._test_hardware()
            self.wait_for(lambda: not panel._testing)
        self.assertFalse(panel.is_ready())
        self.assertIn("没有可用设备", panel.status.text())
        with patch("app.ui.advanced_conversion.test_encoder",
                   return_value=(True, "试编码成功")) as encode:
            panel._test_hardware()
            self.wait_for(lambda: not panel._testing)
        self.assertTrue(panel.is_ready())
        encode.assert_called_once_with("h264_nvenc", kind="video", command="ffmpeg")

    def test_program_change_during_hardware_test_unlocks_retry(self):
        self.ready()
        panel = self.panel
        panel.video_combo.setCurrentIndex(panel.video_combo.findData("h264_nvenc"))
        with patch("app.ui.advanced_conversion.threading.Thread"):
            panel._test_hardware()
        with patch.object(settings, "ffmpeg", return_value="new-ffmpeg"):
            panel._on_tested(panel._test_request, "ffmpeg", (("video", "h264_nvenc"),),
                             [("video", "h264_nvenc", True, "ok")])
            self.assertFalse(panel._testing)
            self.assertFalse(panel.is_ready())
            self.assertTrue(panel.refresh_btn.isEnabled())

    def test_busy_locks_all_parameters_and_shutdown_ignores_pending_results(self):
        self.ready()
        panel = self.panel
        panel.set_busy(True)
        self.assertFalse(panel.format_combo.isEnabled())
        self.assertFalse(panel.video_combo.isEnabled())
        self.assertFalse(panel.refresh_btn.isEnabled())
        panel.set_busy(False)
        self.assertTrue(panel.format_combo.isEnabled())
        previous_caps = panel._caps
        panel.shutdown()
        panel._on_detected(panel._request, "ffmpeg", capabilities("other"), "")
        self.assertIs(panel._caps, previous_caps)
        self.assertFalse(panel.is_ready())


if __name__ == "__main__":
    unittest.main()
