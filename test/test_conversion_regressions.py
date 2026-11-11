# -*- coding: utf-8 -*-
"""高级转换：受控能力交集、参数校验与真实容器输出回归。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.conversion import (FORMAT_PROFILES, _AUDIO_ENCODERS,
                                 _VIDEO_ENCODERS, available_encoders,
                                 available_formats, build_conversion)
from app.core.probe import _parse


def sample_caps():
    return {"muxers": {p["muxer"]: "test" for p in FORMAT_PROFILES},
            "encoders": {name: {"codec": codec, "type": kind}
                         for kind, candidates in (("video", _VIDEO_ENCODERS), ("audio", _AUDIO_ENCODERS))
                         for name, _, codec in candidates}}


class ConversionBuilderRegressions(unittest.TestCase):
    def setUp(self):
        self.caps = sample_caps()
        self.info = {"video": {"codec": "h264", "w": 640, "h": 360, "fps": 30},
                     "audio": {"codec": "aac", "sample_rate": 44100, "channels": 2}}

    def build(self, **options):
        return build_conversion(options, self.info, self.caps)

    def test_format_and_encoder_lists_intersect_actual_capabilities(self):
        caps = {"muxers": {"webm": "WebM"},
                "encoders": {"libvpx-vp9": {"type": "video", "codec": "vp9"},
                             "libx264": {"type": "video", "codec": "h264"},
                             "libopus": {"type": "audio", "codec": "opus"}}}
        self.assertEqual([p["id"] for p in available_formats(caps)], ["webm"])
        self.assertEqual([e["id"] for e in available_encoders("webm", "video", caps)],
                         ["libvpx-vp9", "copy", "none"])
        self.assertEqual([e["id"] for e in available_encoders("webm", "audio", caps)],
                         ["libopus", "copy", "none"])
        self.assertEqual(available_encoders("mp3", "video", caps)[0]["id"], "none")
        self.assertEqual(available_encoders("gif", "audio", caps)[0]["id"], "none")

    def test_missing_formats_and_incompatible_encoders_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持所选输出格式"):
            build_conversion({"format_id": "mp4"}, self.info, {"muxers": {}, "encoders": {}})
        with self.assertRaisesRegex(ValueError, "编码器"):
            self.build(format_id="webm", video_encoder="libx264", audio_encoder="libopus")
        with self.assertRaisesRegex(ValueError, "编码器"):
            self.build(video_encoder="made-up-encoder")

    def test_single_stream_mapping_and_no_hardware_override(self):
        preset = self.build(width=320, fps=24, sample_rate=48000, channels=1)
        args = preset["args"]
        self.assertIn("scale=320:-2,fps=24", args)
        self.assertEqual([args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-map"],
                         ["0:v:0", "0:a:0"])
        self.assertIn("-sn", args)
        self.assertIn("-dn", args)
        self.assertFalse(preset["allow_hw_override"])
        self.assertEqual(args[-2:], ["-f", "mp4"])

    def test_copy_validates_source_codec_and_filter_conflicts(self):
        preset = self.build(video_encoder="copy", audio_encoder="copy")
        self.assertNotIn("-vf", preset["args"])
        for options in ({"video_encoder": "copy", "width": 320},
                        {"video_encoder": "copy", "fps": 24},
                        {"video_encoder": "copy", "video_bitrate": 2000},
                        {"audio_encoder": "copy", "sample_rate": 48000},
                        {"audio_encoder": "copy", "channels": 1}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "复制"):
                self.build(**options)
        with self.assertRaisesRegex(ValueError, "不能直接复制"):
            self.build(format_id="webm", video_encoder="copy", audio_encoder="none")
        self.info["audio"]["codec"] = "pcm_s16le"
        with self.assertRaisesRegex(ValueError, "不能直接复制"):
            self.build(audio_encoder="copy")

    def test_missing_streams_and_all_disabled(self):
        self.info["audio"] = None
        self.assertIn("-an", self.build()["args"])
        with self.assertRaisesRegex(ValueError, "没有音轨"):
            self.build(format_id="mp3")
        with self.assertRaisesRegex(ValueError, "全部禁用"):
            self.build(video_encoder="none", audio_encoder="none")
        self.info["video"] = None
        with self.assertRaisesRegex(ValueError, "没有视频流"):
            self.build()

    def test_ranges_are_validated_before_building(self):
        for options in ({"width": 321}, {"height": 1}, {"width": -2},
                        {"quality": 52}, {"quality": "nan"}, {"fps": float("inf")},
                        {"video_bitrate": -1}, {"audio_bitrate": 7}, {"channels": 9},
                        {"sample_rate": 123}, {"width": 320.5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(**options)

    def test_zero_audio_bitrate_uses_encoder_default_or_stream_copy(self):
        self.assertNotIn("-b:a", self.build(audio_bitrate=0)["args"])
        self.assertNotIn("-b:a", self.build(video_encoder="copy", audio_encoder="copy", audio_bitrate=0)["args"])
        self.assertNotIn("-b:a", self.build(audio_encoder="none", audio_bitrate=0)["args"])

    def test_quality_modes_use_encoder_specific_arguments(self):
        cases = [("libx264", "-crf"), ("libx265", "-crf"),
                 ("h264_nvenc", "-cq"), ("h264_qsv", "-global_quality"),
                 ("h264_amf", "-qp_i"), ("libaom-av1", "-crf"),
                 ("librav1e", "-qp"), ("libsvtav1", "-crf")]
        for encoder, flag in cases:
            with self.subTest(encoder=encoder):
                args = self.build(video_encoder=encoder)["args"]
                self.assertIn(flag, args)
                if flag != "-crf":
                    self.assertNotIn("-crf", args)
        args = self.build(video_encoder="h264_amf", video_bitrate=2500)["args"]
        self.assertIn("2500k", args)
        self.assertNotIn("-qp_i", args)
        av1_amf = self.build(video_encoder="av1_amf", quality=51)["args"]
        self.assertEqual(av1_amf[av1_amf.index("-qp_i") + 1], "255")
        qsv = self.build(video_encoder="h264_qsv")["args"]
        self.assertEqual(qsv[qsv.index("-pix_fmt") + 1], "nv12")
        prores = self.build(format_id="mov", video_encoder="prores_ks")["args"]
        self.assertIn("yuv422p10le", prores)
        self.assertIn("-q:v", prores)
        with self.assertRaisesRegex(ValueError, "码率设为 0"):
            self.build(format_id="mkv", video_encoder="ffv1", video_bitrate=1000)

    def test_codec_specific_audio_constraints(self):
        for options in ({"format_id": "mp3", "audio_encoder": "libmp3lame", "audio_bitrate": 500},
                        {"format_id": "opus", "audio_encoder": "libopus", "audio_bitrate": 600},
                        {"format_id": "opus", "audio_encoder": "libopus", "sample_rate": 44100},
                        {"format_id": "mp3", "audio_encoder": "libmp3lame", "sample_rate": 12345},
                        {"format_id": "mp4", "audio_encoder": "ac3", "channels": 8},
                        {"format_id": "mp3", "audio_encoder": "libmp3lame", "channels": 6}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(**options)
        self.info["audio"]["channels"] = 6
        args = self.build(format_id="mp3")["args"]
        self.assertEqual(args[args.index("-ac") + 1], "2")


_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(_FFMPEG and _FFPROBE, "需要 ffmpeg/ffprobe")
class FFmpegConversionRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.core.capabilities import detect_capabilities
        cls.caps = detect_capabilities(_FFMPEG)
        cls.temp = tempfile.TemporaryDirectory(prefix="ffgui-conversion-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "source.mp4"
        cls.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25:duration=0.8",
                        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=0.8",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(cls.source)])
        cls.info = cls.probe(cls.source)[0]

    @classmethod
    def run_ffmpeg(cls, args):
        proc = subprocess.run([_FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                              capture_output=True, timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr.decode("utf-8", "replace"))
        return proc

    @classmethod
    def probe(cls, path):
        proc = subprocess.run([_FFPROBE, "-v", "error", "-show_streams", "-show_format",
                               "-of", "json", str(path)], capture_output=True, check=True, timeout=10)
        data = json.loads(proc.stdout)
        return _parse(data, str(path)), data

    def convert(self, format_id, video, audio, **options):
        if format_id not in {p["id"] for p in available_formats(self.caps)}:
            self.skipTest(f"缺少 {format_id} 封装器")
        for name, kind in ((video, "video"), (audio, "audio")):
            if name not in ("none", "copy") and name not in self.caps["encoders"]:
                self.skipTest(f"缺少 {name} 编码器")
        preset = build_conversion({"format_id": format_id, "video_encoder": video,
                                   "audio_encoder": audio, **options}, self.info, self.caps)
        output = self.root / f"out-{format_id}-{video}.{preset['ext']}"
        self.run_ffmpeg(["-i", str(self.source), *preset["args"], str(output)])
        self.assertGreater(output.stat().st_size, 0)
        info, data = self.probe(output)
        if format_id not in ("apng", "webp"):
            self.assertGreaterEqual(info["duration"], 0.65)
            self.assertLessEqual(info["duration"], 1.05)
        return output, info, data

    def test_six_video_containers(self):
        cases = [("mp4", "libx264", "aac", "h264", "aac"),
                 ("mkv", "ffv1", "flac", "ffv1", "flac"),
                 ("mov", "prores_ks", "pcm_s16le", "prores", "pcm_s16le"),
                 ("avi", "mpeg4", "libmp3lame", "mpeg4", "mp3"),
                 ("ts", "mpeg2video", "mp2", "mpeg2video", "mp2"),
                 ("flv", "flv", "libmp3lame", "flv1", "mp3")]
        for fmt, venc, aenc, vcodec, acodec in cases:
            with self.subTest(format=fmt):
                _, info, _ = self.convert(fmt, venc, aenc, audio_bitrate=128)
                self.assertEqual(info["video"]["codec"], vcodec)
                self.assertEqual(info["audio"]["codec"], acodec)

    def test_webm_vp9_opus(self):
        _, info, _ = self.convert("webm", "libvpx-vp9", "libopus", audio_bitrate=96)
        self.assertEqual(info["video"]["codec"], "vp9")
        self.assertEqual(info["audio"]["codec"], "opus")

    def test_other_software_video_encoder_families(self):
        for fmt, encoder, codec in (("mp4", "libx265", "hevc"),
                                    ("mkv", "libsvtav1", "av1"),
                                    ("mkv", "libaom-av1", "av1"),
                                    ("mkv", "librav1e", "av1"),
                                    ("webm", "libvpx", "vp8")):
            with self.subTest(encoder=encoder):
                _, info, _ = self.convert(fmt, encoder, "none", quality=28)
                self.assertEqual(info["video"]["codec"], codec)

    def test_audio_formats_and_resampling(self):
        for fmt, enc, codec in [("mp3", "libmp3lame", "mp3"), ("flac", "flac", "flac"),
                                ("wav", "pcm_s24le", "pcm_s24le"), ("m4a", "aac", "aac"),
                                ("aac", "aac", "aac"), ("ogg", "libvorbis", "vorbis"),
                                ("opus", "libopus", "opus"), ("aiff", "pcm_s16be", "pcm_s16be")]:
            with self.subTest(format=fmt):
                _, info, _ = self.convert(fmt, "none", enc, sample_rate=48000, channels=1, audio_bitrate=128)
                self.assertIsNone(info["video"])
                self.assertEqual(info["audio"]["codec"], codec)
                self.assertEqual(info["audio"]["sample_rate"], 48000)
                self.assertEqual(info["audio"]["channels"], 1)

    def test_copy_preserves_packets(self):
        output, info, _ = self.convert("mkv", "copy", "copy")
        self.assertEqual(info["video"]["codec"], "h264")
        self.assertEqual(info["audio"]["codec"], "aac")
        for stream in ("v:0", "a:0"):
            before = self.run_ffmpeg(["-i", str(self.source), "-map", f"0:{stream}", "-c", "copy",
                                      "-f", "hash", "-hash", "sha256", "pipe:1"]).stdout
            after = self.run_ffmpeg(["-i", str(output), "-map", f"0:{stream}", "-c", "copy",
                                     "-f", "hash", "-hash", "sha256", "pipe:1"]).stdout
            self.assertEqual(before, after)

    def test_resize_frame_rate_and_missing_audio(self):
        _, info, _ = self.convert("mp4", "libx264", "aac", width=128, fps=10, channels=1)
        self.assertEqual((info["video"]["w"], info["video"]["h"]), (128, 96))
        self.assertAlmostEqual(info["video"]["fps"], 10)
        silent = self.root / "silent.mp4"
        self.run_ffmpeg(["-i", str(self.source), "-c:v", "copy", "-an", str(silent)])
        preset = build_conversion({"format_id": "mp4", "video_encoder": "libx264", "audio_encoder": "aac"},
                                  self.probe(silent)[0], self.caps)
        out = self.root / "silent-out.mp4"
        self.run_ffmpeg(["-i", str(silent), *preset["args"], str(out)])
        self.assertIsNone(self.probe(out)[0]["audio"])

    def test_mpg_and_animated_formats(self):
        self.convert("mpg", "mpeg2video", "mp2", audio_bitrate=128)
        for fmt, enc in (("gif", "gif"), ("apng", "apng"), ("webp", "libwebp_anim")):
            with self.subTest(format=fmt):
                output, _, _ = self.convert(fmt, enc, "none", width=80, fps=10)
                content = output.read_bytes()
                if fmt == "apng":
                    self.assertIn(b"acTL", content)
                    self.assertGreater(content.count(b"fcTL"), 1)
                elif fmt == "webp":
                    self.assertIn(b"ANIM", content)
                    self.assertGreater(content.count(b"ANMF"), 1)


if __name__ == "__main__":
    unittest.main()
