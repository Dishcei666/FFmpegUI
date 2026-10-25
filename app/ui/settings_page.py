# -*- coding: utf-8 -*-
"""设置页：FFmpeg 路径、默认输出目录、任务并发、GPU 硬件编码检测。"""
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from app.core import gpu, settings

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class _Bridge(QObject):
    checkDone = Signal(str, bool, str)     # (which, ok, text)
    gpuEach = Signal(str, bool)
    gpuDone = Signal(object)               # {encoder: bool}


def _shorten(path, limit=52):
    return path if len(path) <= limit else "…" + path[-(limit - 1):]


class SettingsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self._gpu_results = {}
        self._bridge = _Bridge()
        self._build_ui()
        self._connect()
        self._load_values()

    # ==================================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        t = QLabel("设置")
        t.setObjectName("PageTitle")
        s = QLabel("路径、输出目录、并发与硬件加速；修改即时保存")
        s.setObjectName("PageSub")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(t)
        head.addWidget(s)
        layout.addLayout(head)
        layout.addStretch(0)

        layout.addWidget(self._panel_paths())
        layout.addWidget(self._panel_tasks())

        layout.addStretch(1)
        foot = QLabel("设置保存在 %LOCALAPPDATA%\\FFmpegUI\\settings.json")
        foot.setObjectName("Summary")
        layout.addWidget(foot)

    # ------------------------------------------------------------------
    def _panel_paths(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(10)
        cap = QLabel("FFmpeg 工具路径（留空使用系统 PATH 中的默认命令）")
        cap.setObjectName("SectionLabel")
        vbox.addWidget(cap)
        self.edits = {}
        self.checks = {}
        for key, name in (("ffmpeg_path", "ffmpeg"),
                          ("ffprobe_path", "ffprobe")):
            row = QHBoxLayout()
            lab = QLabel(name)
            lab.setObjectName("FieldLabel")
            lab.setFixedWidth(64)
            edit = QLineEdit()
            edit.setPlaceholderText("系统 PATH 默认")
            self.edits[key] = edit
            btn_browse = QPushButton("浏览…")
            btn_browse.setObjectName("GhostBtn")
            btn_browse.clicked.connect(lambda _, k=key: self._pick_path(k))
            btn_clear = QPushButton("恢复默认")
            btn_clear.setObjectName("GhostBtn")
            btn_clear.clicked.connect(lambda _, k=key: self._clear_path(k))
            btn_test = QPushButton("测试")
            btn_test.setObjectName("GhostBtn")
            btn_test.clicked.connect(lambda _, k=key: self._check(k))
            status = QLabel("")
            status.setObjectName("ValueLabel")
            self.checks[key] = status
            row.addWidget(lab)
            row.addWidget(edit, 1)
            row.addWidget(btn_browse)
            row.addWidget(btn_clear)
            row.addWidget(btn_test)
            vbox.addLayout(row)
            vbox.addWidget(status)
        return panel

    # ------------------------------------------------------------------
    def _panel_tasks(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(10)

        cap = QLabel("任务与性能")
        cap.setObjectName("SectionLabel")
        vbox.addWidget(cap)

        row1 = QHBoxLayout()
        lab1 = QLabel("默认输出目录")
        lab1.setObjectName("FieldLabel")
        lab1.setFixedWidth(88)
        self.edit_outdir = QLineEdit()
        self.edit_outdir.setPlaceholderText("与源文件相同")
        btn_dir = QPushButton("选择…")
        btn_dir.setObjectName("GhostBtn")
        btn_dir.clicked.connect(self._pick_outdir)
        btn_dir_clear = QPushButton("清除")
        btn_dir_clear.setObjectName("GhostBtn")
        btn_dir_clear.clicked.connect(self._clear_outdir)
        row1.addWidget(lab1)
        row1.addWidget(self.edit_outdir, 1)
        row1.addWidget(btn_dir)
        row1.addWidget(btn_dir_clear)
        vbox.addLayout(row1)

        row2 = QHBoxLayout()
        lab2 = QLabel("任务并发数")
        lab2.setObjectName("FieldLabel")
        lab2.setFixedWidth(88)
        self.spin_conc = QSpinBox()
        self.spin_conc.setRange(1, 4)
        self.spin_conc.setFixedWidth(70)
        self.spin_conc.setKeyboardTracking(False)
        note2 = QLabel("同时运行的 ffmpeg 数量；重编码建议 1-2，无损快转可更高")
        note2.setObjectName("FileMeta")
        row2.addWidget(lab2)
        row2.addWidget(self.spin_conc)
        row2.addSpacing(8)
        row2.addWidget(note2)
        row2.addStretch(1)
        vbox.addLayout(row2)

        row3 = QHBoxLayout()
        lab3 = QLabel("GPU 加速")
        lab3.setObjectName("FieldLabel")
        lab3.setFixedWidth(88)
        self.btn_detect = QPushButton("检测硬件编码器")
        self.btn_detect.setObjectName("GhostBtn")
        self.cb_gpu = QCheckBox("优先使用硬件编码（转换 / 剪辑 / 拼接 / 水印）")
        self.cb_gpu.setEnabled(False)
        row3.addWidget(lab3)
        row3.addWidget(self.btn_detect)
        row3.addSpacing(10)
        row3.addWidget(self.cb_gpu)
        row3.addStretch(1)
        vbox.addLayout(row3)

        self.gpu_status = QLabel("尚未检测。检测需逐个试编码，约 5-15 秒。")
        self.gpu_status.setObjectName("ValueLabel")
        self.gpu_status.setWordWrap(True)
        vbox.addWidget(self.gpu_status)

        row4 = QHBoxLayout()
        self.btn_reset = QPushButton("恢复全部默认设置")
        self.btn_reset.setObjectName("GhostBtn")
        row4.addStretch(1)
        row4.addWidget(self.btn_reset)
        vbox.addLayout(row4)
        return panel

    # ==================================================================
    def _connect(self):
        self._bridge.checkDone.connect(self._on_check_done)
        self._bridge.gpuEach.connect(self._on_gpu_each)
        self._bridge.gpuDone.connect(self._on_gpu_done)
        for key, edit in self.edits.items():
            edit.editingFinished.connect(lambda k=key, e=edit:
                                         self._save_path(k, e.text()))
        self.edit_outdir.editingFinished.connect(
            lambda: self._save_outdir(self.edit_outdir.text().strip()))
        self.spin_conc.valueChanged.connect(
            lambda v: settings.set("concurrency", v))
        self.btn_detect.clicked.connect(self._detect_gpu)
        self.cb_gpu.toggled.connect(
            lambda on: settings.set("prefer_gpu", on))
        self.btn_reset.clicked.connect(self._reset_all)

    # ==================================================================
    def _load_values(self):
        for key, edit in self.edits.items():
            edit.setText(settings.get(key) or "")
        self.edit_outdir.setText(settings.get("out_dir") or "")
        self.spin_conc.setValue(int(settings.get("concurrency") or 1))
        self.cb_gpu.blockSignals(True)
        self.cb_gpu.setChecked(bool(settings.get("prefer_gpu")))
        self.cb_gpu.blockSignals(False)
        for key in self.edits:
            self._check(key)
        enc = settings.get("hw_encoder") or ""
        if enc:
            self.gpu_status.setText(
                f"当前硬件编码器：<b>{gpu.LABELS.get(enc, enc)}</b>"
                "（来自上次检测）")
            self.cb_gpu.setEnabled(True)

    # ------------------------------------------------------------------
    def _pick_path(self, key):
        path, _ = QFileDialog.getOpenFileName(self, f"选择 {key.replace('_', '')} 可执行文件",
                                              "", "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self.edits[key].setText(path)
            self._save_path(key, path)

    def _clear_path(self, key):
        self.edits[key].clear()
        self._save_path(key, "")

    def _save_path(self, key, value):
        settings.set(key, value.strip())
        self._check(key)
        self.win.toast("已保存")

    def _check(self, key):
        cmd_path = settings.ffmpeg() if key == "ffmpeg_path" \
            else settings.ffprobe()
        self.checks[key].setText("检测中…")

        def work():
            name = "ffmpeg" if key == "ffmpeg_path" else "ffprobe"
            try:
                proc = subprocess.run([cmd_path, "-version"],
                                      capture_output=True, timeout=8,
                                      creationflags=_NO_WINDOW)
                first = proc.stdout.decode("utf-8", "ignore").splitlines()
                ver = first[0].split()[2] if first and len(first[0].split()) > 2 else "?"
                ok = proc.returncode == 0
                self._bridge.checkDone.emit(
                    key, ok,
                    f'<span style="color:#3ddc84;">✓ {name} {ver}</span>' if ok
                    else f'<span style="color:#ff5d6c;">✗ 无法运行（退出码 '
                         f'{proc.returncode}）</span>')
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._bridge.checkDone.emit(
                    key, False,
                    f'<span style="color:#ff5d6c;">✗ {exc}</span>')
        threading.Thread(target=work, daemon=True).start()

    def _on_check_done(self, key, ok, text):
        if key in self.checks:
            self.checks[key].setText(text)

    # ------------------------------------------------------------------
    def _pick_outdir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择默认输出目录",
                                                     self.edit_outdir.text()
                                                     or "")
        if directory:
            self.edit_outdir.setText(directory)
            self._save_outdir(directory)

    def _clear_outdir(self):
        self.edit_outdir.clear()
        self._save_outdir("")

    def _save_outdir(self, value):
        settings.set("out_dir", value)
        self.win.toast("已保存")

    # ------------------------------------------------------------------
    def _detect_gpu(self):
        self.btn_detect.setEnabled(False)
        self.gpu_status.setText("检测中…（逐个试编码）")
        self._gpu_results = {}

        def each(enc, ok):
            self._bridge.gpuEach.emit(enc, ok)

        def work():
            results = gpu.detect_all(on_each=each)
            self._bridge.gpuDone.emit(results)
        threading.Thread(target=work, daemon=True).start()

    def _on_gpu_each(self, enc, ok):
        self._gpu_results[enc] = ok
        mark = '<span style="color:#3ddc84;">✓</span>' if ok else \
            '<span style="color:#6a7594;">✗</span>'
        self.gpu_status.setText(
            "检测中…  " + "  ".join(
                f"{mark} {gpu.LABELS[e]}" for e, o in self._gpu_results.items()))

    def _on_gpu_done(self, results):
        self.btn_detect.setEnabled(True)
        best = gpu.pick_default(results)
        if best:
            settings.set("hw_encoder", best)
            self.gpu_status.setText(
                f"检测完成：<b>{gpu.LABELS[best]}</b> 可用，已设为硬件编码器。"
                "勾选上方开关后，所有 H.264 编码任务自动改用硬件加速。")
            self.cb_gpu.setEnabled(True)
            self.win.toast(f"GPU 检测完成：{gpu.LABELS[best]} 可用")
        else:
            self.gpu_status.setText(
                "检测完成：未发现可用的硬件编码器（需要 NVIDIA / Intel 核显"
                " / AMD 显卡及对应驱动），将使用 CPU 编码。")
            self.cb_gpu.setEnabled(False)
            self.cb_gpu.setChecked(False)
            settings.set("prefer_gpu", False)

    # ------------------------------------------------------------------
    def _reset_all(self):
        settings.reset()
        self._load_values()
        self.win.toast("已恢复默认设置")
