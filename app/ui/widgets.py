# -*- coding: utf-8 -*-
"""跨页面共享的小控件：拖放区、预设卡片。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)


class DropZone(QFrame):
    """可点击的拖放区。文案可自定义，compact 模式收缩为细条。"""
    clicked = Signal()

    def __init__(self, title="拖入视频 / 音频文件", sub="支持批量添加，或点击选择文件"):
        super().__init__()
        self.setObjectName("DropZone")
        self.setFixedHeight(116)
        self.setCursor(Qt.PointingHandCursor)

        self.icon = QLabel("⇩")
        self.icon.setObjectName("DropIcon")
        self.icon.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.title.setObjectName("DropTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.sub = QLabel(sub)
        self.sub.setObjectName("DropSub")
        self.sub.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        layout.addStretch(1)
        layout.addWidget(self.icon)
        layout.addWidget(self.title)
        layout.addWidget(self.sub)
        layout.addStretch(1)

    def set_compact(self, compact):
        self.icon.setVisible(not compact)
        self.sub.setVisible(not compact)
        self.setFixedHeight(58 if compact else 116)

    def set_hover(self, hover):
        self.setProperty("hover", "true" if hover else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class PresetCard(QPushButton):
    """预设卡片（名称 + 一行描述），配合 QButtonGroup 单选使用。"""

    def __init__(self, preset):
        super().__init__()
        self.setObjectName("PresetCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(68)
        self.preset = preset

        name = QLabel(preset["name"])
        name.setObjectName("PresetName")
        desc = QLabel(preset["desc"])
        desc.setObjectName("PresetDesc")
        desc.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)
        layout.addWidget(name)
        layout.addWidget(desc)
