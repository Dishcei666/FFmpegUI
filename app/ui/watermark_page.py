# -*- coding: utf-8 -*-
"""水印 / 字幕页：文字水印、图片水印、字幕烧录（含 whisper AI 字幕生成）。"""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QComboBox,
                               QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QSlider, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from app.core.clipping import ENCODE_PRESET_IDS
from app.core.presets import get_preset
from app.core.probe import probe_file
from app.core.runner import Task
from app.core.utils import fmt_size
from app.core.watermark import SUBTITLE_STYLES, build_image_watermark, \
    build_subtitle, build_text_watermark
from app.core.whisper import MODELS, download_model, is_downloaded, transcribe

SWATCH_COLORS = ["#FFFFFF", "#000000", "#FF4D4D", "#FFD21F", "#4D9FFF",
                 "#3DDC84"]


def _repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


class _Bridge(QObject):
    fileProbed = Signal(bool, object, str)
    aiProgress = Signal(float)
    aiDone = Signal(bool, str)         # (ok, srt路径或错误)
    aiLog = Signal(str)
    dlProgress = Signal(float, str)    # (百分比, 文本)
    dlDone = Signal(bool, str)


class NineGrid(QFrame):
    """3×3 位置选择器。"""
    posChanged = Signal(str, str)      # (x_key, y_key)

    COLS = ("left", "center", "right")
    ROWS = ("top", "middle", "bottom")

    def __init__(self, default_x="right", default_y="bottom"):
        super().__init__()
        self._buttons = []
        group = QButtonGroup(self)
        group.setExclusive(True)
        host = QGridLayout(self)
        host.setContentsMargins(0, 0, 0, 0)
        host.setSpacing(4)
        for row in range(3):
            for col in range(3):
                btn = QPushButton()
                btn.setObjectName("GridCell")
                btn.setCheckable(True)
                btn.setFixedSize(38, 26)
                btn.setCursor(Qt.PointingHandCursor)
                group.addButton(btn, row * 3 + col)
                self._buttons.append(btn)
                host.addWidget(btn, row, col)
        idx = self.ROWS.index(default_y) * 3 + self.COLS.index(default_x)
        group.button(idx).setChecked(True)
        self._group = group
        group.idClicked.connect(
            lambda i: self.posChanged.emit(self.COLS[i % 3],
                                           self.ROWS[i // 3]))

    def pos(self):
        for i, btn in enumerate(self._buttons):
            if btn.isChecked():
                return self.COLS[i % 3], self.ROWS[i // 3]
        return "right", "bottom"


class WatermarkPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self._src = None
        self._info = None
        self._out_dir = None
        self._pending = set()
        self._cleanup = None              # 导出结束后的临时文件清理
        self._color = "#FFFFFF"
        self._ai_busy = False
        self._dl_busy = False
        self._bridge = _Bridge()
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect()
        self._refresh()
        self._update_model_status()

    # ==================================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        t = QLabel("水印 / 字幕")
        t.setObjectName("PageTitle")
        tcol.addWidget(t)
        s = QLabel("文字与图片水印、字幕烧录，支持 whisper AI 自动生成字幕")
        s.setObjectName("PageSub")
        tcol.addWidget(s)
        head.addLayout(tcol)
        head.addStretch(1)
        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("GhostBtn")
        self.log_btn.setCheckable(True)
        head.addWidget(self.log_btn)
        layout.addLayout(head)

        layout.addWidget(self._build_panel_source(), 1)
        layout.addWidget(self._build_panel_export())
        self.log_panel = QPlainTextEdit()
        self.log_panel.setObjectName("LogPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(800)
        self.log_panel.setFixedHeight(170)
        self.log_panel.hide()
        layout.addWidget(self.log_panel)

    # ------------------------------------------------------------------
    def _build_panel_source(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(8)

        cap = QLabel("处理内容")
        cap.setObjectName("SectionLabel")
        vbox.addWidget(cap)

        file_row = QHBoxLayout()
        self.btn_open_file = QPushButton("打开文件")
        self.btn_open_file.setObjectName("GhostBtn")
        self.file_name = QLabel("尚未选择视频（也可直接拖入）")
        self.file_name.setObjectName("ValueLabel")
        self.file_meta = QLabel("")
        self.file_meta.setObjectName("FileMeta")
        fcol = QVBoxLayout()
        fcol.setSpacing(1)
        fcol.addWidget(self.file_name)
        fcol.addWidget(self.file_meta)
        file_row.addWidget(self.btn_open_file)
        file_row.addSpacing(6)
        file_row.addLayout(fcol, 1)
        vbox.addLayout(file_row)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        self.rb_text = QRadioButton("文字水印")
        self.rb_image = QRadioButton("图片水印")
        self.rb_sub = QRadioButton("字幕烧录")
        self.rb_text.setChecked(True)
        for rb in (self.rb_text, self.rb_image, self.rb_sub):
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        vbox.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_text_mode())
        self.mode_stack.addWidget(self._build_image_mode())
        self.mode_stack.addWidget(self._build_subtitle_mode())
        vbox.addWidget(self.mode_stack, 1)
        return panel

    # ------------------------------------------------------------------
    def _build_text_mode(self):
        w = QWidget()
        rows = QVBoxLayout(w)
        rows.setContentsMargins(0, 2, 0, 0)
        rows.setSpacing(8)

        row1 = QHBoxLayout()
        lab = QLabel("文本")
        lab.setObjectName("FieldLabel")
        self.edit_text = QLineEdit("FFmpegUI")
        self.spin_font = QSpinBox()
        self.spin_font.setRange(8, 128)
        self.spin_font.setValue(28)
        self.spin_font.setSuffix(" px")
        self.spin_font.setFixedWidth(92)
        self.spin_font.setKeyboardTracking(False)
        lab_f = QLabel("字号")
        lab_f.setObjectName("FieldLabel")
        row1.addWidget(lab)
        row1.addWidget(self.edit_text, 1)
        row1.addSpacing(8)
        row1.addWidget(lab_f)
        row1.addWidget(self.spin_font)
        rows.addLayout(row1)

        row2 = QHBoxLayout()
        lab_c = QLabel("颜色")
        lab_c.setObjectName("FieldLabel")
        row2.addWidget(lab_c)
        self._swatch_group = QButtonGroup(self)
        self._swatch_group.setExclusive(True)
        for i, hexcolor in enumerate(SWATCH_COLORS):
            btn = QPushButton()
            btn.setFixedSize(30, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(hexcolor)
            btn.setStyleSheet(
                f"background:{hexcolor}; border:1px solid "
                f"{'#4a5470' if hexcolor.upper() in ('#FFFFFF', '#FFF')
                 else '#2c3a63'}; border-radius:5px;")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            self._swatch_group.addButton(btn, i)
            row2.addWidget(btn)
        self.btn_custom_color = QPushButton("自定义…")
        self.btn_custom_color.setObjectName("GhostBtn")
        row2.addWidget(self.btn_custom_color)
        row2.addSpacing(12)
        row2.addLayout(self._slider("不透明度", 10, 100, 70, self,
                                    "op_text")[0])
        rows.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addLayout(self._slider("边距", 0, 200, 24, self, "margin_text")[0])
        lab_p = QLabel("位置")
        lab_p.setObjectName("FieldLabel")
        row3.addWidget(lab_p)
        self.grid_text = NineGrid()
        row3.addWidget(self.grid_text)
        row3.addStretch(1)
        rows.addLayout(row3)
        rows.addStretch(1)
        return w

    # ------------------------------------------------------------------
    def _build_image_mode(self):
        w = QWidget()
        rows = QVBoxLayout(w)
        rows.setContentsMargins(0, 2, 0, 0)
        rows.setSpacing(8)

        row1 = QHBoxLayout()
        lab = QLabel("图片")
        lab.setObjectName("FieldLabel")
        self.edit_image = QLineEdit()
        self.edit_image.setPlaceholderText("选择 PNG/JPG 水印图片")
        self.btn_pick_image = QPushButton("选择…")
        self.btn_pick_image.setObjectName("GhostBtn")
        row1.addWidget(lab)
        row1.addWidget(self.edit_image, 1)
        row1.addWidget(self.btn_pick_image)
        rows.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addLayout(self._slider("大小", 3, 60, 15, self, "scale_img",
                                    suffix="%")[0])
        row2.addLayout(self._slider("不透明度", 10, 100, 70, self,
                                    "op_img")[0])
        row2.addLayout(self._slider("边距", 0, 200, 24, self, "margin_img")[0])
        row2.addStretch(1)
        rows.addLayout(row2)

        row3 = QHBoxLayout()
        lab_p = QLabel("位置")
        lab_p.setObjectName("FieldLabel")
        row3.addWidget(lab_p)
        self.grid_image = NineGrid()
        row3.addWidget(self.grid_image)
        row3.addStretch(1)
        rows.addLayout(row3)
        rows.addStretch(1)
        return w

    # ------------------------------------------------------------------
    def _build_subtitle_mode(self):
        w = QWidget()
        rows = QVBoxLayout(w)
        rows.setContentsMargins(0, 2, 0, 0)
        rows.setSpacing(8)
        self.model_status = QLabel("")
        self.model_status.setObjectName("ValueLabel")

        row1 = QHBoxLayout()
        lab = QLabel("字幕")
        lab.setObjectName("FieldLabel")
        self.edit_sub = QLineEdit()
        self.edit_sub.setPlaceholderText("选择 SRT / ASS 字幕文件")
        self.btn_pick_sub = QPushButton("选择…")
        self.btn_pick_sub.setObjectName("GhostBtn")
        row1.addWidget(lab)
        row1.addWidget(self.edit_sub, 1)
        row1.addWidget(self.btn_pick_sub)
        rows.addLayout(row1)

        ai = QFrame()
        ai.setObjectName("Panel")
        av = QVBoxLayout(ai)
        av.setContentsMargins(12, 10, 12, 10)
        av.setSpacing(6)
        ai_cap = QLabel("whisper AI 识别生成字幕（本地运行，不上传）")
        ai_cap.setObjectName("FieldLabel")
        av.addWidget(ai_cap)

        ai_row1 = QHBoxLayout()
        lab_lang = QLabel("语言")
        lab_lang.setObjectName("FieldLabel")
        self.combo_lang = QComboBox()
        self.combo_lang.addItem("自动检测", "auto")
        self.combo_lang.addItem("中文", "zh")
        self.combo_lang.addItem("英文", "en")
        lab_model = QLabel("模型")
        lab_model.setObjectName("FieldLabel")
        self.combo_model = QComboBox()
        for key, meta in MODELS.items():
            self.combo_model.addItem(meta["label"], key)
        self.btn_download = QPushButton("下载模型")
        self.btn_download.setObjectName("GhostBtn")
        self.btn_gen = QPushButton("✨ 生成字幕")
        self.btn_gen.setObjectName("StartBtn")
        ai_row1.addWidget(lab_lang)
        ai_row1.addWidget(self.combo_lang)
        ai_row1.addSpacing(10)
        ai_row1.addWidget(lab_model)
        ai_row1.addWidget(self.combo_model)
        ai_row1.addWidget(self.model_status)
        ai_row1.addStretch(1)
        ai_row1.addWidget(self.btn_download)
        ai_row1.addWidget(self.btn_gen)
        av.addLayout(ai_row1)

        self.ai_bar = QProgressBar()
        self.ai_bar.setObjectName("Bar")
        self.ai_bar.setFixedHeight(6)
        self.ai_bar.setRange(0, 100)
        self.ai_bar.setTextVisible(False)
        self.ai_bar.hide()
        self.ai_status = QLabel("")
        self.ai_status.setObjectName("FileMeta")
        av.addWidget(self.ai_bar)
        av.addWidget(self.ai_status)
        rows.addWidget(ai)

        style_row = QHBoxLayout()
        lab_s = QLabel("样式")
        lab_s.setObjectName("FieldLabel")
        self.combo_style = QComboBox()
        for name, _ in SUBTITLE_STYLES:
            self.combo_style.addItem(name)
        lab_e = QLabel("编码")
        lab_e.setObjectName("FieldLabel")
        self.combo_encode = QComboBox()
        for pid in ENCODE_PRESET_IDS:
            self.combo_encode.addItem(get_preset(pid)["name"], pid)
        style_row.addWidget(lab_s)
        style_row.addWidget(self.combo_style, 1)
        style_row.addSpacing(10)
        lab_out = QLabel("输出")
        lab_out.setObjectName("FieldLabel")
        self.dir_chip = QPushButton("📁  与源文件相同")
        self.dir_chip.setObjectName("DirChip")
        style_row.addWidget(lab_out)
        style_row.addWidget(self.dir_chip)
        rows.addLayout(style_row)
        rows.addStretch(1)
        return w

    # ------------------------------------------------------------------
    def _build_panel_export(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(14, 10, 14, 10)
        pv.setSpacing(8)

        top = QHBoxLayout()
        lab_e = QLabel("编码")
        lab_e.setObjectName("FieldLabel")
        self.combo_encode_main = QComboBox()
        for pid in ENCODE_PRESET_IDS:
            self.combo_encode_main.addItem(get_preset(pid)["name"], pid)
        self.dir_chip_main = QPushButton("📁  输出到：与源文件相同")
        self.dir_chip_main.setObjectName("DirChip")
        top.addWidget(lab_e)
        top.addWidget(self.combo_encode_main)
        top.addSpacing(10)
        top.addWidget(self.dir_chip_main)
        top.addStretch(1)
        pv.addLayout(top)

        prog_row = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setObjectName("Bar")
        self.bar.setFixedHeight(8)
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.chip = QLabel("就绪")
        self.chip.setObjectName("Chip")
        self.chip.setProperty("chip", "ready")
        self.btn_open = QPushButton("📂")
        self.btn_open.setObjectName("OpenBtn")
        self.btn_open.setFixedSize(30, 26)
        self.btn_open.hide()
        prog_row.addWidget(self.bar, 1)
        prog_row.addWidget(self.chip)
        prog_row.addWidget(self.btn_open)
        pv.addLayout(prog_row)

        action_row = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("DangerBtn")
        self.btn_cancel.hide()
        self.btn_start = QPushButton("开始处理")
        self.btn_start.setObjectName("StartBtn")
        action_row.addStretch(1)
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_start)
        pv.addLayout(action_row)
        return panel

    # ------------------------------------------------------------------
    @staticmethod
    def _slider(label, lo, hi, default, page, attr, suffix="%"):
        """生成 (HBox 含 label+slider+数值标签)；slider 挂到 page.attr。"""
        box = QHBoxLayout()
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(default)
        slider.setFixedWidth(150)
        val = QLabel(f"{default}{suffix}")
        val.setObjectName("ValueLabel")
        val.setFixedWidth(46)
        slider.valueChanged.connect(
            lambda v, l=val, s=suffix: l.setText(f"{v}{s}"))
        setattr(page, f"slider_{attr}", slider)
        box.addWidget(lab)
        box.addWidget(slider)
        box.addWidget(val)
        return box, slider

    # ==================================================================
    def _connect(self):
        self.btn_open_file.clicked.connect(self._pick_file)
        self.rb_text.toggled.connect(lambda on: on and self.mode_stack.setCurrentIndex(0))
        self.rb_image.toggled.connect(lambda on: on and self.mode_stack.setCurrentIndex(1))
        self.rb_sub.toggled.connect(lambda on: on and self.mode_stack.setCurrentIndex(2))
        self.btn_pick_image.clicked.connect(self._pick_image)
        self.btn_pick_sub.clicked.connect(self._pick_sub)
        self.btn_custom_color.clicked.connect(self._pick_color)
        self.combo_model.currentIndexChanged.connect(
            lambda _: self._update_model_status())
        self.btn_download.clicked.connect(self._download_model)
        self.btn_gen.clicked.connect(self._generate_subtitles)
        self.combo_encode.currentIndexChanged.connect(
            lambda i: self.combo_encode_main.setCurrentIndex(i))
        self.combo_encode_main.currentIndexChanged.connect(
            lambda i: self.combo_encode.setCurrentIndex(i))
        self.dir_chip.clicked.connect(self._pick_dir)
        self.dir_chip_main.clicked.connect(self._pick_dir)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_open.clicked.connect(
            lambda: self.win._open_in_explorer(self._output))
        self.log_btn.toggled.connect(self.log_panel.setVisible)

        self._bridge.fileProbed.connect(self._on_file_probed)
        self._bridge.aiProgress.connect(self._on_ai_progress)
        self._bridge.aiDone.connect(self._on_ai_done)
        self._bridge.aiLog.connect(self._log)
        self._bridge.dlProgress.connect(self._on_dl_progress)
        self._bridge.dlDone.connect(self._on_dl_done)
        self.win._bridge.events.connect(self._on_queue_event)
        self.win._bridge.logs.connect(self._on_log)

    # ==================================================================
    # 文件加载
    # ==================================================================
    def _pick_file(self):
        exts = " ".join(f"*.{e}" for e in sorted(self.win.EXT_VIDEO))
        path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", "",
                                              f"视频文件 ({exts});;所有文件 (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        self._src = path
        self._info = None
        self.file_name.setText(os.path.basename(path))
        self.file_meta.setText("正在读取媒体信息…")
        threading.Thread(target=self._probe_one, args=(path,),
                         daemon=True).start()
        self._refresh()

    def _probe_one(self, path):
        try:
            info = probe_file(path)
            self._bridge.fileProbed.emit(True, info, "")
        except Exception as exc:              # noqa: BLE001
            self._bridge.fileProbed.emit(False, None, str(exc))

    def _on_file_probed(self, ok, info, error):
        if not ok:
            self._src = None
            self.file_meta.setText("无法读取该文件")
            self.win.toast(f"水印页：文件读取失败 - {error[:80]}")
        else:
            self._info = info
            v = info.get("video")
            parts = [info.get("container") or ""]
            if v:
                parts.append(f"{v['w']}×{v['h']} {v['codec'].upper()}")
            if not info.get("audio"):
                parts.append("无音轨（AI 字幕不可用）")
            self.file_meta.setText(" · ".join(p for p in parts if p))
        self._refresh()

    # ==================================================================
    # AI 字幕
    # ==================================================================
    def _update_model_status(self):
        key = self.combo_model.currentData() or "base"
        if is_downloaded(key):
            self.model_status.setText('<span style="color:#3ddc84;">✓ 已就绪</span>')
            self.btn_download.hide()
        else:
            self.model_status.setText(
                '<span style="color:#ffb454;">未下载</span>')
            self.btn_download.setVisible(not self._dl_busy)

    def _download_model(self):
        if self._dl_busy:
            return
        key = self.combo_model.currentData() or "base"
        self._dl_busy = True
        self.btn_download.setEnabled(False)
        self.model_status.setText("下载中…")
        self.ai_bar.setRange(0, 100)
        self.ai_bar.setValue(0)
        self.ai_bar.show()

        def work():
            try:
                def cb(done, total):
                    pct = done / total * 100 if total else 0
                    self._bridge.dlProgress.emit(
                        pct, f"下载中 {pct:.0f}% · {fmt_size(done)}"
                             f"{' / ' + fmt_size(total) if total else ''}")
                download_model(key, cb)
                self._bridge.dlDone.emit(True, "模型下载完成")
            except Exception as exc:          # noqa: BLE001
                self._bridge.dlDone.emit(False, f"下载失败：{exc}")
        threading.Thread(target=work, daemon=True).start()

    def _on_dl_progress(self, pct, text):
        self.ai_bar.setValue(int(pct))
        self.model_status.setText(text)

    def _on_dl_done(self, ok, text):
        self._dl_busy = False
        self.btn_download.setEnabled(True)
        self.win.toast(text)
        if ok:
            self.ai_bar.hide()
        self._update_model_status()

    def _generate_subtitles(self):
        if self._ai_busy or not self._src:
            if not self._src:
                self.win.toast("请先选择视频文件")
            return
        if not self._info or not self._info.get("audio"):
            self.win.toast("该文件没有音轨，无法识别")
            return
        key = self.combo_model.currentData() or "base"
        if not is_downloaded(key):
            self.win.toast("模型未下载，请先下载")
            return
        lang = self.combo_lang.currentData() or "auto"
        base = os.path.splitext(os.path.basename(self._src))[0]
        out_dir = self._out_dir or os.path.dirname(self._src)
        srt = os.path.join(out_dir, f"{base}.srt")
        i = 0
        while os.path.exists(srt):
            i += 1
            srt = os.path.join(out_dir, f"{base} ({i}).srt")

        self._ai_busy = True
        self.btn_gen.setEnabled(False)
        self.ai_bar.setRange(0, 100)
        self.ai_bar.setValue(0)
        self.ai_bar.show()
        self.ai_status.setText("识别中…（首次加载模型较慢）")
        duration = self._info.get("duration") or 0

        def work():
            try:
                transcribe(self._src, key, lang, srt, duration=duration,
                           on_progress=lambda p: self._bridge.aiProgress.emit(p),
                           on_log=lambda l: self._bridge.aiLog.emit(l))
                self._bridge.aiDone.emit(True, srt)
            except Exception as exc:          # noqa: BLE001
                self._bridge.aiDone.emit(False, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _on_ai_progress(self, pct):
        self.ai_bar.setValue(int(pct))
        self.ai_status.setText(f"识别中 {pct:.0f}%")

    def _on_ai_done(self, ok, result):
        self._ai_busy = False
        self.btn_gen.setEnabled(True)
        if ok:
            self.ai_bar.setValue(100)
            self.ai_status.setText(f"字幕已生成：{os.path.basename(result)}")
            self.edit_sub.setText(result)
            self.win.toast("AI 字幕生成完成，可直接烧录")
        else:
            self.ai_bar.hide()
            self.ai_status.setText(f"识别失败：{result[-120:]}")
            self.win.toast("AI 识别失败，详情见状态文字")

    # ==================================================================
    # 导出
    # ==================================================================
    def _encode_id(self):
        return self.combo_encode_main.currentData() or "x264-hq"

    def start(self):
        if self._pending or not self._src or not self._info:
            return
        base = os.path.splitext(os.path.basename(self._src))[0]
        tid = f"{self._src}#wm"
        duration = self._info.get("duration") or 0
        self._cleanup = None

        if self.rb_text.isChecked():
            if not self.edit_text.text().strip():
                self.win.toast("请输入水印文字")
                return
            px, py = self.grid_text.pos()
            out, ext, label, cleanup = build_text_watermark(
                self.edit_text.text(), self.spin_font.value(),
                self._color, self.slider_op_text.value(), px, py,
                self.slider_margin_text.value(), self._encode_id())
            task = Task(self._src, {"args": out, "ext": ext, "name": label},
                        self._out_dir, duration=duration,
                        base_name=f"{base}_文字水印", task_id=tid)
            self._cleanup = cleanup
        elif self.rb_image.isChecked():
            img = self.edit_image.text().strip()
            if not img or not os.path.exists(img):
                self.win.toast("请选择水印图片")
                return
            px, py = self.grid_image.pos()
            inp, out, ext, label = build_image_watermark(
                img, self.slider_scale_img.value(),
                self.slider_op_img.value(), px, py,
                self.slider_margin_img.value(), self._encode_id())
            inp = [self._src if a == "{VIDEO}" else a for a in inp]
            task = Task(self._src, {"args": out, "ext": ext, "name": label},
                        self._out_dir, duration=duration,
                        base_name=f"{base}_图片水印", task_id=tid,
                        input_args=inp, custom_input=True)
        else:
            sub = self.edit_sub.text().strip()
            if not sub or not os.path.exists(sub):
                self.win.toast("请选择字幕文件（可先用 AI 生成）")
                return
            out, ext, label, cleanup = build_subtitle(
                sub, self.combo_style.currentIndex(), self._encode_id())
            task = Task(self._src, {"args": out, "ext": ext, "name": label},
                        self._out_dir, duration=duration,
                        base_name=f"{base}_字幕", task_id=tid)
            self._cleanup = cleanup

        self._pending = {tid}
        self._output = ""
        self.bar.setValue(0)
        self._set_chip("queued", "排队中")
        mode_name = ("文字水印" if self.rb_text.isChecked()
                     else "图片水印" if self.rb_image.isChecked() else "字幕烧录")
        self._log(f"=== {mode_name} · {os.path.basename(self._src)}")
        self.win.queue.enqueue([task])
        self._refresh()

    def _cancel(self):
        for tid in list(self._pending):
            self.win.queue.cancel(tid)

    def _set_chip(self, status, text):
        self.chip.setText(text)
        self.chip.setProperty("chip", status)
        _repolish(self.chip)

    def _on_queue_event(self, ev):
        if ev.get("event") != "task" or ev.get("id") not in self._pending:
            return
        status = ev["status"]
        if status == "running":
            self.bar.setValue(int(ev.get("percent", 0)))
            self._set_chip("running", f"处理中 {ev.get('percent', 0):.0f}%")
        elif status == "done":
            self.bar.setValue(100)
            self._set_chip("done", "完成")
            self._output = ev.get("output_path", "")
            self.btn_open.setVisible(bool(self._output))
            self.win.toast("水印/字幕处理完成")
        elif status == "error":
            self._set_chip("error", "失败")
            self.chip.setToolTip(ev.get("error", ""))
            self.win.toast(f"处理失败：{(ev.get('error') or '')[-90:]}")
        elif status == "cancelled":
            self._set_chip("cancelled", "已取消")
        if status in ("done", "error", "cancelled"):
            if self._cleanup:
                try:
                    self._cleanup()
                except Exception:            # noqa: BLE001
                    pass
                self._cleanup = None
            self._pending.clear()
        self._refresh()

    def _on_log(self, task_id, line):
        if task_id not in self._pending and not task_id.endswith("#wm"):
            return
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(line)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _log(self, text):
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    # ==================================================================
    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择水印图片", "",
                                              "图片 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)")
        if path:
            self.edit_image.setText(path)

    def _pick_sub(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择字幕文件", "",
                                              "字幕 (*.srt *.ass);;所有文件 (*)")
        if path:
            self.edit_sub.setText(path)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择水印颜色")
        if color.isValid():
            self._color = color.name().upper()
            self.btn_custom_color.setText(f"自定义 {self._color}")
            self._swatch_group.setExclusive(False)
            for b in self._swatch_group.buttons():
                b.setChecked(False)
            self._swatch_group.setExclusive(True)

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                    self._out_dir or "")
        if directory:
            self._out_dir = directory
            shown = directory if len(directory) <= 40 else "…" + directory[-39:]
            self.dir_chip.setText(f"📁  {shown}")
            self.dir_chip_main.setText(f"📁  输出到：{shown}")

    def _refresh(self):
        busy = bool(self._pending)
        loaded = bool(self._src and self._info)
        self.btn_start.setEnabled(loaded and not busy)
        self.btn_start.setText("处理中…" if busy else "开始处理")
        self.btn_cancel.setVisible(busy)
        self.btn_open_file.setEnabled(not busy)
        self.btn_gen.setEnabled(loaded and not self._ai_busy and not busy)
        self.btn_download.setEnabled(not self._dl_busy)
        self.dir_chip.setEnabled(not busy)
        self.dir_chip_main.setEnabled(not busy)
        self.bar.setVisible(busy or self.bar.value() > 0)

    # ==================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        paths = [p for p in paths
                 if os.path.splitext(p)[1][1:].lower() in self.win.EXT_VIDEO]
        if paths:
            event.acceptProposedAction()
            self.load_file(paths[0])
