# -*- coding: utf-8 -*-
"""无 Qt 的 CLI 回归：真实子进程、UTF-8 JSON 协议及 FFmpeg 输出。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
_NO_QT_BOOTSTRAP = """
import importlib.abc
import runpy
import sys
class RejectQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('PySide6', 'shiboken6'):
            raise RuntimeError('CLI attempted to import Qt: ' + fullname)
sys.meta_path.insert(0, RejectQt())
sys.argv = ['app.cli', *sys.argv[1:]]
try:
    runpy.run_module('app.cli', run_name='__main__')
finally:
    assert not any(name.split('.')[0] in ('PySide6', 'shiboken6') for name in sys.modules), \
        'CLI loaded Qt modules'
"""


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffgui-cli-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "isolated-config"
        self.env = dict(os.environ, LOCALAPPDATA=str(self.config),
                        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        for key in ("FFMPEG_PATH", "FFPROBE_PATH", "FFPLAY_PATH", "FFGUI_PAGE",
                    "FFGUI_SCREENSHOT", "FFGUI_AUTOQUIT_MS", "FFGUI_CONVERT_MODE"):
            self.env.pop(key, None)

    def cli(self, *args, stdin=None, no_qt=False, env=None):
        if isinstance(stdin, str):
            stdin = stdin.encode("utf-8")
        command = [sys.executable]
        command += ["-c", _NO_QT_BOOTSTRAP] if no_qt else ["-m", "app.cli"]
        result = subprocess.run([*command, *args], cwd=ROOT, env=env or self.env,
                                input=stdin, capture_output=True, timeout=45)
        self.assertNotIn(b"Traceback (most recent call last)", result.stdout)
        self.assertNotIn(b"Traceback (most recent call last)", result.stderr)
        return result

    def json_result(self, result, code=0):
        self.assertEqual(result.returncode, code,
                         f"stdout={result.stdout!r}\nstderr={result.stderr!r}")
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            self.fail(f"stdout must be exactly one UTF-8 JSON document: {result.stdout!r}: {exc}")
        self.assertIsInstance(payload, dict)
        return payload

    def request(self, value, **kwargs):
        return self.cli("run", stdin=json.dumps(value, ensure_ascii=False), **kwargs)


class CLIProtocolRegressions(CLITestCase):
    def test_help_and_convert_help_work_without_qt(self):
        for args in (("--help",), ("convert", "--help"), ("run", "--help")):
            with self.subTest(args=args):
                result = self.cli(*args, no_qt=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                self.assertIn(b"usage:", result.stdout.lower())
                self.assertEqual(result.stderr, b"")
        self.assertFalse(self.config.exists(), "CLI help must not write GUI settings")

    def test_unknown_commands_missing_arguments_and_bad_types_are_json_errors(self):
        for args in ((), ("unknown-command",), ("formats", "--unknown"),
                     ("convert",), ("probe",), ("test-encoder",),
                     ("convert", "input.mp4", "--quality", "not-an-int"),
                     ("test-encoder", "libx264", "--kind", "data")):
            with self.subTest(args=args):
                self.json_result(self.cli(*args), 2)

    def test_malformed_utf8_and_json_are_json_errors(self):
        for content in (b"{", b"not json", b"\xff", b"",
                        b'{"action":"formats","action":"probe"}',
                        b'{"action":"convert","inputs":["source.mp4"],"options":{"quality":NaN}}'):
            with self.subTest(content=content):
                self.json_result(self.cli("run", stdin=content), 2)

    def test_json_schema_rejects_wrong_root_input_and_options_types(self):
        existing = self.root / "existing-file.dat"
        existing.write_bytes(b"placeholder")
        bad = [None, [], 1, "request", {},
               {"action": "convert", "inputs": []},
               {"action": "convert", "inputs": "source.mp4"},
               {"action": "convert", "inputs": [1]},
               {"action": "convert", "inputs": [None]},
               {"action": "convert", "inputs": [""]},
               {"action": "convert", "inputs": ["   "]},
               {"action": "convert", "inputs": ["bad\x00path"]},
               {"action": "convert", "inputs": [str(existing)], "options": []},
               {"action": "convert", "inputs": [str(existing)], "options": {"width": "320"}},
               {"action": "convert", "inputs": [str(existing)], "options": {"fps": True}},
               {"action": "convert", "inputs": [str(existing)], "options": {"unknown": 1}},
               {"action": "convert", "inputs": [str(existing)], "concurrency": True},
               {"action": "convert", "inputs": [str(existing)], "concurrency": 0},
               {"action": "convert", "inputs": [str(existing)], "concurrency": 1.5},
               {"action": "convert", "inputs": [str(existing)], "output_dir": 123},
               {"action": "convert", "inputs": [str(existing)], "preset": False},
               {"action": "convert", "inputs": [str(existing)], "unknown": "value"},
               {"action": "not-an-action", "inputs": ["source.mp4"]}]
        for value in bad:
            with self.subTest(value=value):
                self.json_result(self.request(value), 2)

    def test_request_file_errors_are_json_errors(self):
        missing = self.root / "missing-request.json"
        self.json_result(self.cli("run", "--request", str(missing)), 2)
        invalid = self.root / "无效请求.json"
        invalid.write_text("{", encoding="utf-8")
        self.json_result(self.cli("run", "--request", str(invalid)), 2)

    def test_missing_tool_returns_exit_one_and_json(self):
        env = dict(self.env, FFMPEG_PATH=str(self.root / "missing-ffmpeg.exe"))
        payload = self.json_result(self.cli("capabilities", env=env), 1)
        self.assertIn("error", payload)
        self.assertFalse(self.config.exists(), "CLI must not create GUI settings")

    def test_cancelled_result_and_keyboard_interrupt_use_exit_130(self):
        for body in ("return {'ok':False,'action':'formats','cancelled':True}",
                     "raise KeyboardInterrupt"):
            with self.subTest(body=body):
                script = ("from app import cli\n"
                          "def execute(*args, **kwargs):\n    " + body + "\n"
                          "cli.execute = execute\n"
                          "raise SystemExit(cli.main(['formats']))\n")
                result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                        env=self.env, capture_output=True, timeout=15)
                payload = self.json_result(result, 130)
                self.assertTrue(payload["cancelled"])


@unittest.skipUnless(FFMPEG and FFPROBE, "需要 PATH 中的 ffmpeg/ffprobe")
class CLIRealFFmpegRegressions(CLITestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample_temp = tempfile.TemporaryDirectory(prefix="ffgui-cli-source-")
        cls.addClassCleanup(cls.sample_temp.cleanup)
        cls.source = Path(cls.sample_temp.name, "示例 视频 'AI'.mp4")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25:duration=0.6",
                        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.6",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(cls.source)],
                       check=True, capture_output=True, timeout=20)

    def media(self, path):
        result = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(path)], capture_output=True,
                                check=True, timeout=10)
        return json.loads(result.stdout)

    def test_formats_capabilities_probe_and_encoder_return_json_without_qt(self):
        for args, expected in ((("formats",), "mp4"),
                               (("capabilities",), "libx264"),
                               (("probe", str(self.source)), "h264"),
                               (("test-encoder", "libx264", "--kind", "video"), "libx264")):
            with self.subTest(args=args):
                payload = self.json_result(self.cli(*args, no_qt=True))
                self.assertIn(expected, json.dumps(payload, ensure_ascii=False))
        self.assertFalse(self.config.exists(), "Read-only CLI queries must not write settings")

    def test_explicit_json_flag_preserves_single_object_protocol(self):
        payload = self.json_result(self.cli("formats", "--json"))
        self.assertIn("mp4", json.dumps(payload))

    def test_convert_creates_unicode_output_and_stderr_progress_is_ndjson(self):
        output_dir = self.root / "输出 媒体"
        result = self.cli("convert", str(self.source), "--format", "webm",
                          "--video-encoder", "libvpx-vp9", "--audio-encoder", "libopus",
                          "--audio-bitrate", "96", "--width", "80", "--fps", "15",
                          "--output-dir", str(output_dir), "--progress", "--json", no_qt=True)
        payload = self.json_result(result)
        self.assertTrue(payload.get("ok"), payload)
        outputs = list(output_dir.glob("*.webm"))
        self.assertEqual(len(outputs), 1)
        data = self.media(outputs[0])
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        self.assertEqual((video["codec_name"], video["width"]), ("vp9", 80))
        self.assertEqual(audio["codec_name"], "opus")
        progress = [json.loads(line) for line in result.stderr.decode("utf-8").splitlines() if line]
        self.assertTrue(progress, "--progress must emit stderr NDJSON")
        self.assertTrue(all(isinstance(event, dict) for event in progress))
        self.assertFalse(list(output_dir.glob("*.ffgui-tmp.*")))

    def test_convert_preset_accepts_multiple_inputs_and_concurrency(self):
        second = self.root / "第二个 源.mp4"
        shutil.copyfile(self.source, second)
        output_dir = self.root / "batch"
        payload = self.json_result(self.cli(
            "convert", str(self.source), str(second), "--preset", "audio-mp3",
            "--output-dir", str(output_dir), "--concurrency", "2"))
        self.assertTrue(payload.get("ok"), payload)
        outputs = list(output_dir.glob("*.mp3"))
        self.assertEqual(len(outputs), 2)
        for output in outputs:
            streams = self.media(output)["streams"]
            self.assertTrue(any(s["codec_name"] == "mp3" for s in streams))
            self.assertFalse(any(s["codec_type"] == "video" for s in streams))

    def test_stdin_json_request_and_unicode_request_file(self):
        for use_file in (False, True):
            with self.subTest(use_file=use_file):
                output_dir = self.root / ("文件输出" if use_file else "标准输入输出")
                request = {"action": "convert", "inputs": [str(self.source)],
                           "output_dir": str(output_dir), "concurrency": 1,
                           "options": {"format_id": "m4a", "audio_encoder": "aac"}}
                if use_file:
                    path = self.root / "请求 文件.json"
                    path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
                    result = self.cli("run", "--request", str(path))
                else:
                    result = self.request(request)
                payload = self.json_result(result)
                self.assertTrue(payload.get("ok"), payload)
                outputs = list(output_dir.glob("*.m4a"))
                self.assertEqual(len(outputs), 1)
                self.assertEqual(self.media(outputs[0])["streams"][0]["codec_name"], "aac")

    def test_runtime_failure_and_option_errors_have_distinct_exit_codes(self):
        invalid_media = self.root / "损坏的媒体.mp4"
        invalid_media.write_bytes(b"not a media file")
        self.json_result(self.cli("probe", str(invalid_media)), 1)
        self.json_result(self.cli("probe", str(self.root / "不存在.mp4")), 2)
        self.json_result(self.cli("test-encoder", "not_a_real_encoder"), 2)
        for extra in (("--format", "not-a-format"),
                      ("--preset", "not-a-preset"),
                      ("--format", "mp4", "--preset", "audio-mp3"),
                      ("--width", "81"), ("--concurrency", "0"),
                      ("--video-encoder", "copy", "--width", "80")):
            with self.subTest(extra=extra):
                self.json_result(self.cli("convert", str(self.source), *extra,
                                          "--output-dir", str(self.root / "invalid")), 2)
        self.json_result(self.cli("convert", str(self.source), str(self.source),
                                  "--output-dir", str(self.root / "duplicate")), 2)
        self.assertFalse((self.root / "duplicate").exists())


if __name__ == "__main__":
    unittest.main()
