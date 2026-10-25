# -*- coding: utf-8 -*-
"""文件列表中的一行：类型徽标 + 名称/信息 + 进度 + 状态胶囊 + 操作按钮。"""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSizePolicy, QVBoxLayout)

from app.core.utils import fmt_duration, fmt_fps, fmt_size, fmt_took


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class FileRow(QFrame):
    removeClicked = Signal(str)      # 输入文件路径
    openClicked = Signal(str)        # 输出文件路径

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setObjectName("FileRow")
        self._path = path
        self._full_name = os.path.basename(path)
        self._output = ""

        # 左侧类型徽标（显示容器扩展名）
        self.badge = QLabel("…")
        self.badge.setObjectName("TypeBadge")
        self.badge.setProperty("badgeType", "other")
        self.badge.setFixedSize(46, 46)
        self.badge.setAlignment(Qt.AlignCenter)

        # 中部信息列
        self.name = QLabel(self._full_name)
        self.name.setObjectName("FileName")
        self.name.setMinimumWidth(60)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setToolTip(self._full_name)
        self.meta = QLabel("正在读取媒体信息…")
        self.meta.setObjectName("FileMeta")
        self.out = QLabel("")
        self.out.setObjectName("FileOut")
        self.out.hide()
        self.bar = QProgressBar()
        self.bar.setObjectName("Bar")
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.hide()

        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(3)
        info_col.addWidget(self.name)
        info_col.addWidget(self.meta)
        info_col.addWidget(self.out)
        info_col.addWidget(self.bar)
        info_col.addStretch(1)

        # 右侧状态胶囊 + 速度
        self.chip = QLabel("读取中")
        self.chip.setObjectName("Chip")
        self.chip.setProperty("chip", "probing")
        self.speed = QLabel("")
        self.speed.setObjectName("Speed")
        self.speed.setAlignment(Qt.AlignCenter)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(3)
        right_col.addWidget(self.chip)
        right_col.addWidget(self.speed)
        right_col.addStretch(1)

        # 操作按钮列
        self.btn_open = QPushButton("📂")
        self.btn_open.setObjectName("OpenBtn")
        self.btn_open.setFixedSize(30, 26)
        self.btn_open.setToolTip("打开输出所在文件夹")
        self.btn_open.hide()
        self.btn_open.clicked.connect(
            lambda: self.openClicked.emit(self._output))
        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("IconBtn")
        self.btn_del.setFixedSize(30, 26)
        self.btn_del.setToolTip("从列表移除")
        self.btn_del.clicked.connect(lambda: self.removeClicked.emit(self._path))
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(self.btn_open)
        btn_col.addWidget(self.btn_del)
        btn_col.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(12)
        layout.addWidget(self.badge)
        layout.addLayout(info_col, 1)
        layout.addLayout(right_col)
        layout.addLayout(btn_col)

        self.set_state("probing")

    # ------------------------------------------------------------------
    def set_info(self, info):
        ext = os.path.splitext(self._full_name)[1][1:].upper()[:4] or "?"
        self.badge.setText(ext)
        self.badge.setProperty(
            "badgeType",
            "video" if info.get("video") else
            "audio" if info.get("audio") else
            "gif" if ext.lower() == "gif" else "other")
        _repolish(self.badge)

        parts = []
        video, audio = info.get("video"), info.get("audio")
        if video:
            if video["w"] and video["h"]:
                parts.append(f"{video['w']}×{video['h']}")
            parts.append(video["codec"].upper())
            fps = fmt_fps(video["fps"])
            if fps:
                parts.append(f"{fps} fps")
        if audio:
            parts.append(audio["codec"].upper())
            if audio["sample_rate"]:
                parts.append(f"{audio['sample_rate'] // 1000} kHz")
            if audio["channels"]:
                parts.append("立体声" if audio["channels"] == 2
                             else f"{audio['channels']} 声道")
        parts.append(fmt_duration(info.get("duration")))
        parts.append(fmt_size(info.get("size")))
        self.meta.setText(" · ".join(str(p) for p in parts if p))

    def set_state(self, status, percent=0.0, speed="", error="",
                  output_path="", took_ms=0):
        self.setProperty("status", status)
        _repolish(self)

        chip_text = {
            "probing": "读取信息…",
            "ready": "就绪",
            "queued": "排队中",
            "running": f"转换中 {percent:.0f}%",
            "done": f"完成 · {fmt_took(took_ms)}",
            "error": "失败",
            "cancelled": "已取消",
        }.get(status, status)
        self.chip.setText(chip_text)
        self.chip.setProperty("chip", status)
        _repolish(self.chip)
        self.chip.setToolTip(error if status == "error" else "")

        self.bar.setVisible(status == "running")
        self.bar.setValue(int(percent))
        self.speed.setText(speed if status == "running" else "")

        self.btn_del.setVisible(status not in ("queued", "running"))
        self.btn_open.setVisible(status == "done")
        if status == "done" and output_path:
            self._output = output_path
            self.out.setText("→ " + os.path.basename(output_path))
            self.out.show()
        if error:
            self.setToolTip(error)

    # ------------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        metrics = self.name.fontMetrics()
        elided = metrics.elidedText(self._full_name, Qt.ElideRight,
                                    max(40, self.name.width()))
        self.name.setText(elided)
