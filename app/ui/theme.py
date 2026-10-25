# -*- coding: utf-8 -*-
"""全局 QSS 深色主题。配色：底 #0d1017 / 面板 #131826 / 主色 #6d7cff→#9a6dff。"""

QSS = """
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    outline: none;
}
QMainWindow, QFrame#Root { background: #0d1017; }

/* ---------------- 侧栏 ---------------- */
QFrame#Sidebar { background: #10141d; border-right: 1px solid #1d2436; }
QLabel#Logo { color: #e8ecf4; font-size: 16px; font-weight: 700; padding: 16px 14px 2px 14px; }
QLabel#LogoSub { color: #566080; font-size: 10px; padding: 0 14px 0 14px; }
QLabel#NavSection { color: #4a5470; font-size: 10px; font-weight: 600; padding: 0 12px; }
QPushButton#NavButton, QPushButton#NavButtonLocked {
    color: transparent; background: transparent; border: none;
    border-radius: 9px; padding: 0; font-size: 12.5px; text-align: left;
}
QPushButton#NavButton:hover, QPushButton#NavButtonLocked:hover { background: #1a2132; }
QPushButton#NavButton:checked { background: rgba(109,124,255,0.15); }
QLabel#NavIcon { background: transparent; border: none; font-size: 13px; }
QLabel#NavText { color: #98a2bb; font-size: 12.5px; background: transparent; border: none; }
QPushButton#NavButtonLocked QLabel#NavText { color: #566080; }
QPushButton#NavButtonLocked:hover QLabel#NavText { color: #8b96b6; }
QPushButton#NavButton:checked QLabel#NavText { color: #ffffff; font-weight: 600; }
QLabel#NavBadge {
    background: #1c2438; color: #6a7594; border-radius: 7px;
    padding: 2px 8px; font-size: 10px;
}
QPushButton#NavButtonLocked:hover QLabel#NavBadge { background: #232c48; color: #8b96b6; }
QFrame#SidebarFoot { border-top: 1px solid #1d2436; }
QLabel#FootStatus { color: #566080; font-size: 10.5px; padding: 10px 12px 4px 12px; }
QLabel#FootVer { color: #3c445e; font-size: 10px; padding: 0 12px 10px 12px; }

/* ---------------- 页头 ---------------- */
QLabel#PageTitle { color: #f0f3fa; font-size: 19px; font-weight: 700; }
QLabel#PageSub { color: #77839f; font-size: 11.5px; }
QLabel#Summary { color: #566080; font-size: 11px; }
QLabel#SectionLabel {
    color: #77839f; font-size: 11px; font-weight: 600;
    padding: 2px 2px 0 2px;
}

/* ---------------- 拖放区 ---------------- */
QFrame#DropZone {
    background: #10141f; border: 2px dashed #273352; border-radius: 14px;
}
QFrame#DropZone[hover="true"] {
    background: rgba(109,124,255,0.07); border-color: #6d7cff;
}
QLabel#DropIcon { color: #6d7cff; font-size: 30px; background: transparent; border: none; }
QLabel#DropTitle { color: #dbe2f2; font-size: 14px; font-weight: 600; background: transparent; border: none; }
QLabel#DropSub { color: #566080; font-size: 11px; background: transparent; border: none; }

/* ---------------- 文件行 ---------------- */
QScrollArea { background: transparent; border: none; }
QWidget#FileListInner { background: transparent; }
QFrame#FileRow {
    background: #131826; border: 1px solid #1f2740; border-radius: 12px;
}
QFrame#FileRow:hover { border-color: #2c3a63; }
QFrame#FileRow[status="running"] { background: #141b30; border-color: #3a4a8c; }
QFrame#FileRow[status="done"] { border-color: #1f4a38; }
QFrame#FileRow[status="error"] { border-color: #4a2830; }

QLabel#TypeBadge {
    border-radius: 10px; font-size: 10px; font-weight: 700;
}
QLabel[badgeType="video"] { background: rgba(109,124,255,0.16); color: #9aa7ff; }
QLabel[badgeType="audio"] { background: rgba(45,212,191,0.13); color: #5fe0c8; }
QLabel[badgeType="gif"]   { background: rgba(255,122,176,0.14); color: #ff9ec6; }
QLabel[badgeType="other"] { background: #1c2438; color: #8b96b6; }

QLabel#FileName { color: #e8ecf4; font-size: 12.5px; font-weight: 600; background: transparent; }
QLabel#FileMeta { color: #77839f; font-size: 11px; background: transparent; }
QLabel#FileOut { color: #4dd6b8; font-size: 11px; background: transparent; }

QProgressBar#Bar { background: #1c2438; border: none; border-radius: 3px; }
QProgressBar#Bar::chunk {
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #6d7cff, stop:1 #9a6dff);
}

QLabel#Chip { border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 600; background: transparent; }
QLabel#Chip[chip="probing"]  { background: #16223a; color: #7f9bd8; }
QLabel#Chip[chip="ready"]    { background: #1c2438; color: #98a2bb; }
QLabel#Chip[chip="queued"]   { background: #1c2438; color: #98a2bb; }
QLabel#Chip[chip="running"]  { background: rgba(109,124,255,0.16); color: #9aa7ff; }
QLabel#Chip[chip="done"]     { background: rgba(61,220,132,0.12); color: #3ddc84; }
QLabel#Chip[chip="error"]    { background: rgba(255,93,108,0.13); color: #ff5d6c; }
QLabel#Chip[chip="cancelled"]{ background: #1c2438; color: #6a7594; }
QLabel#Speed { color: #566080; font-size: 10.5px; background: transparent; }

QPushButton#IconBtn {
    background: transparent; border: none; border-radius: 8px;
    color: #77839f; font-size: 13px;
}
QPushButton#IconBtn:hover { background: #202941; color: #dbe2f2; }
QPushButton#IconBtn#OpenBtn { font-size: 14px; }

/* ---------------- 预设卡片 ---------------- */
QPushButton#PresetCard {
    background: #131826; border: 1px solid #1f2740; border-radius: 12px;
    padding: 0; text-align: left;
}
QPushButton#PresetCard:hover { border-color: #33426f; }
QPushButton#PresetCard:checked {
    background: rgba(109,124,255,0.09); border: 1px solid #6d7cff;
}
QLabel#PresetName { color: #dbe2f2; font-size: 12px; font-weight: 600; background: transparent; }
QLabel#PresetDesc { color: #77839f; font-size: 10.5px; background: transparent; }
QPushButton#PresetCard:checked QLabel#PresetName { color: #ffffff; }
QPushButton#PresetCard:checked QLabel#PresetDesc { color: #aab4d4; }

/* ---------------- 底部操作栏 ---------------- */
QFrame#ActionBar {
    background: #10141d; border: 1px solid #1d2436; border-radius: 14px;
}
QPushButton#DirChip {
    background: #131826; border: 1px solid #1f2740; border-radius: 9px;
    color: #aab4d4; padding: 8px 12px; font-size: 11px;
}
QPushButton#DirChip:hover { border-color: #33426f; color: #dbe2f2; }
QPushButton#DirChip:disabled { color: #566080; }

QPushButton#StartBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #6d7cff, stop:1 #9a6dff);
    color: #ffffff; border: none; border-radius: 10px;
    padding: 10px 28px; font-size: 13px; font-weight: 700;
}
QPushButton#StartBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #7d8eff, stop:1 #a87dff);
}
QPushButton#StartBtn:disabled { background: #232b42; color: #566080; }

QPushButton#GhostBtn {
    background: transparent; border: 1px solid #232c48; border-radius: 10px;
    color: #98a2bb; padding: 9px 16px; font-size: 11.5px;
}
QPushButton#GhostBtn:hover { border-color: #33426f; color: #dbe2f2; }
QPushButton#GhostBtn:checked {
    background: rgba(109,124,255,0.12); border-color: #6d7cff; color: #dbe2f2;
}
QPushButton#GhostBtn:disabled { color: #3c445e; border-color: #1a2132; }

QPushButton#DangerBtn {
    background: rgba(255,93,108,0.10); border: 1px solid rgba(255,93,108,0.35);
    border-radius: 10px; color: #ff8b96; padding: 9px 16px; font-size: 11.5px;
}
QPushButton#DangerBtn:hover { background: rgba(255,93,108,0.18); }

/* ---------------- 剪辑页 ---------------- */
QFrame#Panel { background: #10141f; border: 1px solid #1d2436; border-radius: 14px; }
QLabel#FieldLabel { color: #77839f; font-size: 11px; background: transparent; }
QLabel#ValueLabel { color: #dbe2f2; font-size: 11.5px; background: transparent; }
QLineEdit, QDoubleSpinBox, QSpinBox {
    background: #131826; border: 1px solid #1f2740; border-radius: 8px;
    padding: 6px 8px; color: #dbe2f2; font-size: 11.5px;
    selection-background-color: #6d7cff; selection-color: #ffffff;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #6d7cff; }
QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
    color: #566080; background: #10141f;
}
QLineEdit { min-width: 60px; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }
QComboBox {
    background: #131826; border: 1px solid #1f2740; border-radius: 8px;
    padding: 6px 10px; color: #dbe2f2; font-size: 11.5px;
}
QComboBox:hover { border-color: #33426f; }
QComboBox:disabled { color: #566080; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 6px solid #77839f;
}
QComboBox QAbstractItemView {
    background: #161c2c; border: 1px solid #2c3a63; color: #dbe2f2;
    selection-background-color: rgba(109,124,255,0.30);
}
QRadioButton { color: #98a2bb; font-size: 11.5px; spacing: 6px; background: transparent; }
QRadioButton:disabled { color: #566080; }
QRadioButton::indicator {
    width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid #33426f; background: #131826;
}
QRadioButton::indicator:checked { background: #6d7cff; border-color: #6d7cff; }
QCheckBox { color: #98a2bb; font-size: 11.5px; spacing: 7px; background: transparent; }
QCheckBox:disabled { color: #566080; }
QCheckBox::indicator {
    width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid #33426f; background: #131826;
}
QCheckBox::indicator:checked { background: #6d7cff; border-color: #6d7cff; }
QPushButton#GridCell { background: #131826; border: 1px solid #1f2740; border-radius: 6px; }
QPushButton#GridCell:hover { border-color: #33426f; }
QPushButton#GridCell:checked { background: rgba(109,124,255,0.35); border-color: #6d7cff; }
QSlider::groove:horizontal { height: 4px; background: #1c2438; border-radius: 2px; }
QSlider::sub-page:horizontal {
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #6d7cff, stop:1 #9a6dff);
}
QSlider::handle:horizontal {
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    background: #dbe2f2;
}
QSlider::handle:horizontal:hover { background: #ffffff; }

/* ---------------- 日志与杂项 ---------------- */
QPlainTextEdit#LogPanel {
    background: #0b0e15; border: 1px solid #1d2436; border-radius: 10px;
    color: #8fa3c8; font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 10.5px; padding: 8px;
}
QLabel#Toast {
    background: #1d2740; color: #e8ecf4; border: 1px solid #2c3a63;
    border-radius: 18px; padding: 9px 18px; font-size: 12px;
}
QToolTip {
    background: #1d2740; color: #e8ecf4; border: 1px solid #2c3a63;
    padding: 6px 8px; font-size: 11px;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #232c48; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #33426f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
