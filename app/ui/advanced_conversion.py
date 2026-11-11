# -*- coding: utf-8 -*-
"""按当前 FFmpeg 能力提供高级转换参数，检测与试编码均在后台执行。"""
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from app.core import settings
from app.core.capabilities import (detect_capabilities, invalidate_cache,
                                    test_encoder)
from app.core.conversion import (available_encoders, available_formats,
                                  build_conversion)


class _Bridge(QObject):
    detected = Signal(int, str, object, str)
    tested = Signal(int, str, object, object)


class AdvancedConversionPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._caps = None
        self._command = None
        self._request = 0
        self._test_request = 0
        self._detecting = False
        self._testing = False
        self._busy = False
        self._closed = False
        self._tested = set()
        self._test_error = ""
        self._encoders = {"video": [], "audio": []}
        self._bridge = _Bridge(self)
        self._bridge.detected.connect(self._on_detected)
        self._bridge.tested.connect(self._on_tested)
        self._build_ui()
        self._update_controls()

    def _build_ui(self):
        self.setMaximumHeight(250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        head = QHBoxLayout()
        self.status = QLabel("切入高级模式后检测本机 FFmpeg 支持的格式和编码器")
        self.status.setObjectName("FileMeta")
        self.status.setWordWrap(True)
        head.addWidget(self.status, 1)
        self.refresh_btn = QPushButton("重新检测")
        self.refresh_btn.setObjectName("GhostBtn")
        self.refresh_btn.clicked.connect(lambda: self.refresh_capabilities(force=True))
        head.addWidget(self.refresh_btn)
        self.test_btn = QPushButton("测试硬件编码器")
        self.test_btn.setObjectName("GhostBtn")
        self.test_btn.clicked.connect(self._test_hardware)
        head.addWidget(self.test_btn)
        layout.addLayout(head)
        note = QLabel("输出首条视频 / 音轨；字幕与其他轨道不保留。可选格式按本机能力筛选。")
        note.setObjectName("FileMeta")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(90)
        content = QWidget()
        content.setObjectName("FileListInner")
        grid = QGridLayout(content)
        grid.setContentsMargins(3, 3, 8, 3)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.format_combo = QComboBox()
        self.video_combo = QComboBox()
        self.audio_combo = QComboBox()
        self.quality_spin = self._integer(0, 51, 23)
        self.quality_spin.setToolTip("质量模式下数值越低画质越高；不同编码器由程序换算。")
        self.video_bitrate_spin = self._integer(0, 1_000_000, 0, " kbps", "按质量编码")
        self.width_spin = self._integer(0, 16384, 0, " px", "保持 / 自动")
        self.height_spin = self._integer(0, 16384, 0, " px", "保持 / 自动")
        self.width_spin.setSingleStep(2)
        self.height_spin.setSingleStep(2)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(0, 240)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setSpecialValueText("保持原帧率")
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setKeyboardTracking(False)
        self.audio_bitrate_spin = self._integer(0, 1536, 192, " kbps", "编码器默认")
        self.sample_rate_combo = QComboBox()
        for rate in (0, 8000, 12000, 16000, 22050, 24000, 32000, 44100,
                     48000, 96000, 192000, 384000):
            self.sample_rate_combo.addItem("保持原采样率" if not rate else f"{rate} Hz", rate)
        self.channels_combo = QComboBox()
        for count, label in ((0, "保持原声道"), (1, "单声道"), (2, "立体声"),
                             (6, "5.1 声道"), (8, "7.1 声道")):
            self.channels_combo.addItem(label, count)

        fields = (("输出格式", self.format_combo), ("视频编码", self.video_combo),
                  ("音频编码", self.audio_combo), ("画质（0–51）", self.quality_spin),
                  ("视频码率", self.video_bitrate_spin), ("输出帧率", self.fps_spin),
                  ("输出宽度", self.width_spin), ("输出高度", self.height_spin),
                  ("音频码率", self.audio_bitrate_spin), ("采样率", self.sample_rate_combo),
                  ("输出声道", self.channels_combo))
        for index, (label, control) in enumerate(fields):
            row, col = index // 2, index % 2 * 2
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            grid.addWidget(caption, row, col)
            grid.addWidget(control, row, col + 1)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)

        self._video_controls = (self.quality_spin, self.video_bitrate_spin,
                                self.width_spin, self.height_spin, self.fps_spin)
        self._audio_controls = (self.audio_bitrate_spin, self.sample_rate_combo,
                                self.channels_combo)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self.video_combo.currentIndexChanged.connect(self._selection_changed)
        self.audio_combo.currentIndexChanged.connect(self._selection_changed)
        self.video_bitrate_spin.valueChanged.connect(self._parameters_changed)
        for control in (self.quality_spin, self.width_spin, self.height_spin,
                        self.fps_spin, self.audio_bitrate_spin):
            control.valueChanged.connect(self._parameters_changed)
        self.sample_rate_combo.currentIndexChanged.connect(self._parameters_changed)
        self.channels_combo.currentIndexChanged.connect(self._parameters_changed)

    @staticmethod
    def _integer(minimum, maximum, value, suffix="", special=None):
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(value)
        control.setSuffix(suffix)
        control.setKeyboardTracking(False)
        if special:
            control.setSpecialValueText(special)
        return control

    def refresh_capabilities(self, force=False):
        if self._closed or self._busy:
            return
        command = settings.ffmpeg()
        if not force and self._detecting and command == self._command:
            return
        if not force and self._caps is not None and command == self._command:
            self._update_controls()
            return
        self._request += 1
        request = self._request
        self._test_request += 1
        self._detecting = True
        self._testing = False
        self._caps = None
        self._command = command
        self._tested.clear()
        self._test_error = ""
        self.status.setText("正在检测 FFmpeg 支持的输出格式和编码器…")
        self._update_controls(update_status=False)
        self.changed.emit()

        def work():
            try:
                if force:
                    invalidate_cache(command)
                caps = detect_capabilities(command)
                self._bridge.detected.emit(request, command, caps, "")
            except Exception as exc:
                self._bridge.detected.emit(request, command, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_detected(self, request, command, caps, error):
        if self._closed or request != self._request or command != self._command:
            return
        self._detecting = False
        if command != settings.ffmpeg():
            self._caps = None
            self.status.setText("FFmpeg 路径已更改，请重新检测")
        elif error:
            self._caps = None
            self.status.setText(f"检测失败：{error[:160]}。可点击重新检测。")
        else:
            self._caps = caps
            try:
                self._populate_formats()
            except (KeyError, TypeError, ValueError) as exc:
                self._caps = None
                self.status.setText(f"无法读取可用格式：{exc}。请重新检测。")
        self._update_controls(update_status=self._caps is not None)
        self.changed.emit()

    def _populate_formats(self):
        previous = self.format_combo.currentData() or "mp4"
        profiles = available_formats(self._caps)
        profiles = profiles.values() if isinstance(profiles, dict) else profiles
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for profile in profiles:
            self.format_combo.addItem(profile["label"], profile["id"])
        index = self.format_combo.findData(previous)
        self.format_combo.setCurrentIndex(max(0, index))
        self.format_combo.blockSignals(False)
        self._populate_encoders()

    def _populate_encoders(self):
        format_id = self.format_combo.currentData()
        for kind, combo, default in (("video", self.video_combo, "libx264"),
                                     ("audio", self.audio_combo, "aac")):
            previous = combo.currentData() or default
            options = available_encoders(format_id, kind, self._caps) if format_id else []
            self._encoders[kind] = options
            combo.blockSignals(True)
            combo.clear()
            for option in options:
                combo.addItem(option["label"], option["id"])
            index = combo.findData(previous)
            if previous == "none" and any(option["id"] not in ("copy", "none")
                                           for option in options):
                index = -1
            if index < 0:
                preferred = ("libvpx-vp9", "libvpx") if format_id == "webm" and kind == "video" \
                    else ("libopus", "libvorbis") if format_id == "webm" and kind == "audio" \
                    else (default,)
                index = next((combo.findData(name) for name in preferred
                              if combo.findData(name) >= 0), -1)
            if index < 0:
                index = next((i for i, option in enumerate(options)
                              if option["id"] not in ("copy", "none")
                              and not option.get("hardware")), -1)
            if index < 0:
                index = next((i for i, option in enumerate(options)
                              if option["id"] not in ("copy", "none")), 0)
            combo.setCurrentIndex(index if options else -1)
            combo.blockSignals(False)

    def _format_changed(self, *_):
        if self._caps is not None:
            self._populate_encoders()
        self._selection_changed()

    def _selection_changed(self, *_):
        self._test_request += 1
        self._testing = False
        self._test_error = ""
        self._update_controls()
        self.changed.emit()

    def _parameters_changed(self, *_):
        self._update_controls()
        self.changed.emit()

    def _hardware_selection(self):
        selected = []
        for kind, combo in (("video", self.video_combo), ("audio", self.audio_combo)):
            option = next((item for item in self._encoders[kind]
                           if item["id"] == combo.currentData()), None)
            if option and option.get("hardware"):
                selected.append((kind, option["id"]))
        return tuple(selected)

    def _video_codec(self):
        option = next((item for item in self._encoders["video"]
                       if item["id"] == self.video_combo.currentData()), {})
        return option.get("codec", "")

    def _audio_codec(self):
        option = next((item for item in self._encoders["audio"]
                       if item["id"] == self.audio_combo.currentData()), {})
        return option.get("codec", "")

    def _test_hardware(self):
        selected = self._hardware_selection()
        if self._busy or self._detecting or self._testing or self._closed or not selected:
            return
        if self._caps is None or self._command != settings.ffmpeg():
            self.refresh_capabilities()
            return
        self._test_request += 1
        request, command = self._test_request, self._command
        self._testing = True
        self._test_error = ""
        self._update_controls()
        self.changed.emit()

        def work():
            results = []
            for kind, encoder in selected:
                try:
                    ok, detail = test_encoder(encoder, kind=kind, command=command)
                except Exception as exc:
                    ok, detail = False, str(exc)
                results.append((kind, encoder, ok, detail))
            self._bridge.tested.emit(request, command, selected, results)

        threading.Thread(target=work, daemon=True).start()

    def _on_tested(self, request, command, selected, results):
        if (self._closed or request != self._test_request or command != self._command
                or selected != self._hardware_selection()):
            return
        self._testing = False
        if command != settings.ffmpeg():
            self._update_controls()
            self.changed.emit()
            return
        errors = []
        for kind, encoder, ok, detail in results:
            key = (command, kind, encoder)
            if ok:
                self._tested.add(key)
            else:
                self._tested.discard(key)
                errors.append(f"{encoder}：{detail[:130]}")
        self._test_error = "；".join(errors)
        self._update_controls()
        self.changed.emit()

    def is_ready(self):
        if (self._closed or self._caps is None or self._detecting or self._testing
                or self._command != settings.ffmpeg() or not self.format_combo.currentData()
                or not self.video_combo.currentData() or not self.audio_combo.currentData()):
            return False
        if self.video_combo.currentData() == self.audio_combo.currentData() == "none":
            return False
        return all((self._command, kind, encoder) in self._tested
                   for kind, encoder in self._hardware_selection())

    def build_preset(self, info):
        if not self.is_ready():
            raise ValueError("高级转换参数尚未就绪，请完成 FFmpeg 检测及硬件试编码")
        video = self.video_combo.currentData()
        audio = self.audio_combo.currentData()
        video_transform = video not in ("copy", "none")
        audio_transform = audio not in ("copy", "none")
        options = {
            "format_id": self.format_combo.currentData(),
            "video_encoder": video, "audio_encoder": audio,
            "quality": self.quality_spin.value(),
            "video_bitrate": self.video_bitrate_spin.value()
            if video_transform and self.video_bitrate_spin.isEnabled()
            and self._video_codec() not in
            ("prores", "ffv1", "gif", "apng", "webp") else 0,
            "audio_bitrate": self.audio_bitrate_spin.value()
            if audio_transform and self.audio_bitrate_spin.isEnabled() else 0,
            "width": self.width_spin.value() if video_transform else 0,
            "height": self.height_spin.value() if video_transform else 0,
            "fps": self.fps_spin.value() if video_transform else 0,
            "sample_rate": self.sample_rate_combo.currentData() if audio_transform else 0,
            "channels": self.channels_combo.currentData() if audio_transform else 0,
        }
        return build_conversion(options, info, self._caps)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_controls()

    def _update_controls(self, update_status=True):
        enabled = not (self._busy or self._closed or self._detecting or self._testing)
        available = enabled and self._caps is not None and self._command == settings.ffmpeg()
        self.refresh_btn.setEnabled(enabled)
        for combo in (self.format_combo, self.video_combo, self.audio_combo):
            combo.setEnabled(available)
        for control in self._video_controls:
            control.setEnabled(available and self.video_combo.currentData() not in ("copy", "none"))
        codec = self._video_codec()
        self.video_bitrate_spin.setEnabled(self.video_bitrate_spin.isEnabled()
            and codec not in ("prores", "ffv1", "gif", "apng", "webp"))
        self.video_bitrate_spin.setToolTip(
            "该编码器使用质量或无损模式，不使用视频码率。"
            if codec in ("prores", "ffv1", "gif", "apng", "webp")
            else "0 按画质编码，填写码率后改用目标码率模式。")
        bitrate_mode = self.video_bitrate_spin.isEnabled() and self.video_bitrate_spin.value() > 0
        self.quality_spin.setEnabled(self.quality_spin.isEnabled()
                                     and codec not in ("ffv1", "gif", "apng")
                                     and not bitrate_mode)
        if codec in ("ffv1", "gif", "apng"):
            quality_hint = "该编码器使用固定无损或调色板模式，无需设置画质。"
        elif bitrate_mode:
            quality_hint = "已设置目标码率，画质参数暂不使用。"
        elif codec == "prores":
            quality_hint = "数值越低画质越高；换算为 ProRes q:v（1–32）。"
        elif codec == "webp":
            quality_hint = "数值越低画质越高；换算为 WebP quality（100–0）。"
        elif codec in ("mpeg4", "mpeg1video", "mpeg2video", "mjpeg", "flv1"):
            quality_hint = "数值越低画质越高；换算为该编码器 q:v（2–31）。"
        elif self.video_combo.currentData() == "librav1e":
            quality_hint = "数值越低画质越高；换算为 rav1e qp（0–255）。"
        elif self._hardware_selection():
            quality_hint = "数值越低画质越高；硬件编码器分别使用 CQ、global_quality 或 QP。"
        else:
            quality_hint = "数值越低画质越高；通过 CRF 控制质量，0–51。"
        self.quality_spin.setToolTip(quality_hint)
        for control in self._audio_controls:
            control.setEnabled(available and self.audio_combo.currentData() not in ("copy", "none"))
        audio_codec = self._audio_codec()
        lossless_audio = audio_codec in ("flac", "alac") or audio_codec.startswith("pcm_")
        self.audio_bitrate_spin.setEnabled(self.audio_bitrate_spin.isEnabled() and not lossless_audio)
        self.audio_bitrate_spin.setToolTip("无损编码由采样率与位深决定码率。" if lossless_audio
                                         else "目标音频码率；支持范围按所选编码器验证。")
        hardware = self._hardware_selection()
        self.test_btn.setEnabled(available and bool(hardware))
        if not update_status or self._detecting:
            return
        if self._testing:
            self.status.setText("正在进行硬件试编码，请稍候…")
        elif self._caps is not None and self._command != settings.ffmpeg():
            self.status.setText("FFmpeg 路径已更改，请重新检测")
        elif self._test_error:
            self.status.setText(f"硬件试编码未通过：{self._test_error}。可重试或选择软件编码。")
        elif hardware and not self.is_ready():
            self.status.setText("已列出硬件编码器，使用前请点击测试；可用性取决于显卡和驱动。")
        elif self._caps is not None and not self.format_combo.count():
            self.status.setText("当前 FFmpeg 未提供支持的输出格式，请检查所选程序。")
        elif self._caps is not None and not self.is_ready():
            self.status.setText("请选择可用的输出编码器，至少保留视频或音频。")
        elif self._caps is not None:
            suffix = "；硬件试编码已通过" if hardware else ""
            self.status.setText(f"已开放 {self.format_combo.count()} 种常用输出格式{suffix}")
            self.status.setToolTip(str(self._caps.get("version", "")))

    def shutdown(self):
        self._closed = True
        self._request += 1
        self._test_request += 1
        self._update_controls()
