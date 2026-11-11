# -*- coding: utf-8 -*-
"""媒体组合回归：python -m unittest discover -s test -p test_media_regressions.py。"""
import array
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import clipping, merging, settings, watermark, whisper
from app.core.probe import _parse


class MediaBuilderRegressions(unittest.TestCase):
    def test_scale_and_transform_share_one_video_filter(self):
        for preset in ("scale-1080", "scale-720"):
            with self.subTest(preset=preset):
                _, out, _, _ = clipping.build_segment(
                    "src.mp4", 0, 1, "precise", preset,
                    {"rotate": 90, "crop": "100:80:0:0", "speed": 1})
                self.assertEqual(out.count("-vf"), 1)
                vf = out[out.index("-vf") + 1]
                self.assertIn("crop=100:80:0:0,transpose=1,scale=", vf)
                _, out, _, _ = clipping.build_segment(
                    "src.mp4", 0, 1, "precise", preset,
                    {"speed": 1.5, "has_audio": True})
                self.assertNotIn("-vf", out)
                self.assertIn("scale=", out[out.index("-filter_complex") + 1])

    def test_merge_speed_reports_output_duration(self):
        _, out, _, _, duration = clipping.build_merge(
            "src.mp4", [(0, 2), (4, 6)], "scale-720",
            {"speed": 2, "has_audio": True})
        self.assertEqual(duration, 2)
        self.assertNotIn("-vf", out)
        self.assertIn("atempo=2.0000", out[out.index("-filter_complex") + 1])

    def test_watermark_presets_keep_business_filters(self):
        out, _, _, cleanup = watermark.build_text_watermark(
            "100% '%{text}'", 20, "#ffffff", 100, "left", "top", 4, "scale-720")
        try:
            self.assertEqual(out.count("-vf"), 1)
            self.assertIn("drawtext=", out[1])
            self.assertIn("scale=", out[1])
            self.assertIn("expansion=none", out[1])
        finally:
            cleanup()
        _, out, _, _ = watermark.build_image_watermark(
            "logo.ppm", 25, 100, "left", "top", 0, "scale-1080")
        self.assertNotIn("-vf", out)
        self.assertIn("scale2ref=", out[1])

    def test_normalize_srt_replaces_numbers_and_is_idempotent(self):
        raw = ("\ufeff0\r\n00:00:00,000 --> 00:00:00,500\r\n第一行\r\n第二行\r\n\r\n"
               "1\r\n00:00:00,500 --> 00:00:01,000\r\n123\r\n")
        normalized = whisper._normalize_srt(raw)
        self.assertEqual(normalized, (
            "1\n00:00:00,000 --> 00:00:00,500\n第一行\n第二行\n\n"
            "2\n00:00:00,500 --> 00:00:01,000\n123\n"))
        self.assertEqual(whisper._normalize_srt(normalized), normalized)
        self.assertEqual(whisper._normalize_srt(""), "")
        with self.assertRaises(ValueError):
            whisper._normalize_srt("0\nnot a timestamp\ntext")

    def test_auxiliary_probes_use_current_settings(self):
        with mock.patch.object(settings, "ffprobe", return_value="chosen-probe"), \
                mock.patch.object(clipping.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"0.0,K_\n", b"")
            self.assertEqual(clipping.probe_keyframes("x"), [0.0])
            self.assertEqual(run.call_args.args[0][0], "chosen-probe")
        with mock.patch.object(settings, "ffmpeg", return_value="chosen-ffmpeg"), \
                mock.patch.object(clipping.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"", b"crop=100:80:0:0")
            self.assertEqual(clipping.detect_crop("x"), "100:80:0:0")
            self.assertEqual(run.call_args.args[0][0], "chosen-ffmpeg")

    def test_fast_join_rejects_fps_and_channel_mismatch(self):
        info = {"video": {"codec": "h264", "w": 160, "h": 120, "fps": 20},
                "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2}}
        other = copy.deepcopy(info)
        other["video"]["fps"] = 30
        self.assertFalse(merging.check_compatible([info, other])[0])
        other = copy.deepcopy(info)
        other["audio"]["channels"] = 1
        self.assertFalse(merging.check_compatible([info, other])[0])

    def test_fast_join_accepts_equivalent_frame_rate_fractions(self):
        info = {"video": {"codec": "h264", "w": 160, "h": 120,
                          "frame_rate": "30/1", "time_base": "1/1000"}}
        other = copy.deepcopy(info)
        other["video"].update(frame_rate="60/2", time_base="2/2000")
        self.assertTrue(merging.check_compatible([info, other])[0])


class WhisperLifecycleRegressions(unittest.TestCase):
    def test_cancel_kills_recognition_process_and_removes_temp_srt(self):
        real_popen = subprocess.Popen
        children = []
        with tempfile.TemporaryDirectory(prefix="ffgui-whisper-test-") as tmp:
            model = Path(tmp, "model.bin")
            model.write_bytes(b"model")
            cancel = threading.Event()

            def spawn(_cmd, **kwargs):
                child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
                children.append(child)
                return child

            timer = threading.Timer(0.2, cancel.set)
            started = time.monotonic()
            with mock.patch.object(whisper, "model_path", return_value=str(model)), \
                    mock.patch.object(whisper.subprocess, "Popen", side_effect=spawn), \
                    mock.patch.object(tempfile, "tempdir", tmp):
                timer.start()
                try:
                    with self.assertRaisesRegex(RuntimeError, "已取消"):
                        whisper.transcribe("src", "base", "auto", str(Path(tmp, "out.srt")),
                                           cancel_event=cancel)
                finally:
                    timer.join()
            self.assertLess(time.monotonic() - started, 5)
            self.assertIsNotNone(children[0].poll())
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["model.bin"])

    def test_callback_exception_still_reaps_child(self):
        real_popen = subprocess.Popen
        children = []
        with tempfile.TemporaryDirectory(prefix="ffgui-whisper-test-") as tmp:
            model = Path(tmp, "model.bin")
            model.write_bytes(b"model")

            def spawn(_cmd, **kwargs):
                child = real_popen([sys.executable, "-u", "-c",
                                    "import time; print('out_time_us=1000'); time.sleep(30)"], **kwargs)
                children.append(child)
                return child

            def callback(_):
                raise RuntimeError("callback failed")

            with mock.patch.object(whisper, "model_path", return_value=str(model)), \
                    mock.patch.object(whisper.subprocess, "Popen", side_effect=spawn), \
                    mock.patch.object(tempfile, "tempdir", tmp):
                with self.assertRaisesRegex(RuntimeError, "callback failed"):
                    whisper.transcribe("src", "base", "auto", str(Path(tmp, "out.srt")),
                                       duration=1, on_progress=callback)
            self.assertIsNotNone(children[0].poll())
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["model.bin"])

    def test_incomplete_download_preserves_old_model_and_cleans_part(self):
        with tempfile.TemporaryDirectory(prefix="ffgui-download-test-") as tmp:
            dest = Path(tmp, "model.bin")
            dest.write_bytes(b"old model")
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.headers = {"Content-Length": "100"}
            response.read.side_effect = [b"short", b""]
            with mock.patch.object(whisper, "model_path", return_value=str(dest)), \
                    mock.patch.object(whisper.urllib.request, "urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "不完整"):
                    whisper.download_model("base")
            self.assertEqual(dest.read_bytes(), b"old model")
            self.assertFalse(Path(str(dest) + ".part").exists())


_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(_FFMPEG and _FFPROBE, "需要 PATH 中的 ffmpeg/ffprobe")
class FFmpegMediaRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ffgui-media-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "voice.mp4"
        cls.silent = cls.root / "silent.mp4"
        cls.ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=20:duration=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=1.2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(cls.source)])
        cls.ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=120x80:r=20:d=0.8",
                    "-c:v", "libx264", "-preset", "ultrafast", str(cls.silent)])
        cls.logo = cls.root / "red.ppm"
        cls.logo.write_bytes(b"P6\n10 5\n255\n" + bytes([255, 0, 0]) * 50)
        cls.subtitle = cls.root / "generated.srt"
        cls.subtitle.write_text(whisper._normalize_srt(
            "0\n00:00:00,000 --> 00:00:00,700\nSubtitle test\n"), encoding="utf-8")

    @classmethod
    def ffmpeg(cls, args):
        return subprocess.run([_FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                              capture_output=True, check=True, timeout=30)

    @classmethod
    def info(cls, path):
        proc = subprocess.run([_FFPROBE, "-v", "error", "-show_streams", "-show_format",
                               "-of", "json", str(path)],
                              capture_output=True, check=True, timeout=10)
        return _parse(json.loads(proc.stdout), str(path))

    def output(self, name, inp, out):
        path = self.root / f"{name}.mp4"
        self.ffmpeg([*inp, *out, str(path)])
        self.assertGreater(path.stat().st_size, 0)
        return path

    def pixels(self, path):
        return self.ffmpeg(["-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24",
                            "-f", "rawvideo", "pipe:1"]).stdout

    def test_rotation_and_scaling_both_take_effect(self):
        inp, out, _, _ = clipping.build_segment(
            str(self.source), 0.1, 0.9, "precise", "scale-720", {"rotate": 90})
        path = self.output("rotate-scale", [*inp, "-i", str(self.source)], out)
        v = self.info(path)["video"]
        self.assertEqual((v["w"], v["h"]), (120, 160))

    def test_speed_audio_and_scaling_work_together(self):
        inp, out, _, _ = clipping.build_segment(
            str(self.source), 0, 1.2, "precise", "scale-720",
            {"speed": 1.5, "has_audio": True})
        info = self.info(self.output("speed-scale", [*inp, "-i", str(self.source)], out))
        self.assertIsNotNone(info["audio"])
        self.assertAlmostEqual(info["duration"], 0.8, delta=0.15)

    def test_clip_concat_and_scaling_work_together(self):
        inp, out, _, _, duration = clipping.build_merge(
            str(self.source), [(0, 0.3), (0.6, 0.9)], "scale-720",
            {"speed": 1.5, "has_audio": True})
        info = self.info(self.output("clip-concat-scale", inp, out))
        self.assertIsNotNone(info["audio"])
        self.assertAlmostEqual(info["duration"], duration, delta=0.15)

    def test_text_and_subtitle_survive_scaling_and_generated_srt_parses(self):
        self.assertEqual(self.info(self.subtitle)["duration"], 0)
        out, _, _, cleanup = watermark.build_text_watermark(
            "100%", 18, "#ffffff", 100, "left", "top", 3, "scale-720")
        try:
            path = self.output("text-scale", ["-i", str(self.silent)], out)
        finally:
            cleanup()
        pixels = self.pixels(path)
        self.assertGreater(sum(v > 200 for v in pixels), 40)
        out, _, _, cleanup = watermark.build_subtitle(str(self.subtitle), 1, "scale-720")
        try:
            path = self.output("subtitle-scale", ["-i", str(self.silent)], out)
        finally:
            cleanup()
        # libass 的字号按脚本分辨率缩放，小画面仍应出现可见字幕像素。
        self.assertGreater(sum(self.pixels(path)), 1500)

    def test_image_watermark_width_uses_video_width(self):
        inp, out, _, _ = watermark.build_image_watermark(
            str(self.logo), 25, 100, "left", "top", 0, "scale-720")
        inp = [str(self.silent) if value == "{VIDEO}" else value for value in inp]
        pixels = self.pixels(self.output("image-scale", inp, out))
        red_x = [x for x in range(120)
                 if pixels[(4 * 120 + x) * 3] > 150
                 and pixels[(4 * 120 + x) * 3 + 1] < 70
                 and pixels[(4 * 120 + x) * 3 + 2] < 70]
        self.assertTrue(red_x)
        self.assertAlmostEqual(max(red_x) - min(red_x) + 1, 30, delta=2)

    def test_mixed_audio_join_preserves_sound_and_inserts_silence(self):
        for paths in ([self.source, self.silent], [self.silent, self.source]):
            with self.subTest(first=paths[0].name):
                infos = [self.info(path) for path in paths]
                inp, out, _, _, total = merging.build_join(
                    [str(path) for path in paths], infos, "scale-720")
                output = self.output(f"mixed-{paths[0].stem}", inp, out)
                info = self.info(output)
                self.assertIsNotNone(info["audio"])
                self.assertAlmostEqual(info["duration"], total, delta=0.15)
                pcm = self.ffmpeg(["-i", str(output), "-map", "0:a:0", "-ac", "1", "-ar", "8000",
                                   "-f", "f32le", "pipe:1"]).stdout
                values = array.array("f")
                values.frombytes(pcm)

                def rms(start, end):
                    samples = values[int(start * 8000):int(end * 8000)]
                    return math.sqrt(sum(v * v for v in samples) / len(samples))

                if paths[0] == self.source:
                    self.assertGreater(rms(0.2, 0.6), 0.02)
                    self.assertLess(rms(1.4, 1.8), 0.002)
                else:
                    self.assertLess(rms(0.2, 0.6), 0.002)
                    self.assertGreater(rms(1.1, 1.5), 0.02)


if __name__ == "__main__":
    unittest.main()
