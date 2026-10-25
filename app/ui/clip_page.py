# -*- coding: utf-8 -*-
"""剪辑页：时间轴选段 + ffplay 预览 + 片段列表 + 变换（裁切/旋转/变速）+ 导出。"""
import os
import re
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import QRectF

from app.core.clipping import (ENCODE_PRESET_IDS, build_merge, build_segment,
                               detect_crop, probe_keyframes)
from app.core.presets import get_preset
from app.core.runner import Task
from app.ui.widgets import apply_default_out_dir

FFPLAY = os.environ.get("FFPLAY_PATH", "ffplay")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

ROTATE_ITEMS = [("不旋转", 0), ("顺时针 90°", 90), ("旋转 180°", 180),
                ("逆时针 90°", 270), ("水平镜像", "mirror")]


def _repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


def fmt_ts(t):
    t = max(0.0, float(t or 0))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}"


class _ClipBridge(QObject):
    fileProbed = Signal(bool, object, str)   # (ok, info|None, error)
    keyframes = Signal(object)               # list[float] 或空
    cropDone = Signal(str)                   # 'w:h:x:y' 或 ''
    posUpdate = Signal(float)
    playStopped = Signal()


class Timeline(QWidget):
    """时间轴：轨道 + 选中区 + 两个拖柄 + 关键帧刻度。"""
    rangeChanged = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(64)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._keyframes = []
        self._drag = None
        self._region_len = 0.0

    def set_duration(self, duration):
        self._duration = max(0.0, duration)
        self._start, self._end = 0.0, self._duration
        self.update()

    def set_keyframes(self, times):
        self._keyframes = times or []
        self.update()

    def set_range(self, start, end):
        self._start = max(0.0, start)
        self._end = min(self._duration, end)
        self.update()

    def range(self):
        return self._start, self._end

    # -- 坐标换算 -----------------------------------------------------
    def _x(self, t):
        m = 10.0
        w = self.width() - 2 * m
        return m + (t / self._duration) * w if self._duration else m

    def _t(self, x):
        m = 10.0
        w = self.width() - 2 * m
        t = (x - m) / w * self._duration if w > 0 else 0.0
        return max(0.0, min(self._duration, t))

    # -- 绘制 ---------------------------------------------------------
    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        m = 10.0
        track_y, track_h = 16, 8

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1c2438"))
        painter.drawRoundedRect(QRectF(m, track_y, w - 2 * m, track_h), 4, 4)

        x1, x2 = self._x(self._start), self._x(self._end)
        painter.setBrush(QColor(109, 124, 255, 95))
        painter.drawRoundedRect(QRectF(x1, track_y, max(x2 - x1, 2.0), track_h), 4, 4)

        painter.setBrush(QColor("#4d5c8f"))
        for t in self._keyframes:
            x = self._x(t)
            if m <= x <= w - m:
                painter.drawRect(QRectF(x - 1.0, track_y + track_h + 3, 2.0, 6.0))

        painter.setBrush(QColor("#8f9bff"))
        for x in (x1, x2):
            painter.drawRoundedRect(QRectF(x - 5, track_y - 9, 10, track_h + 18), 5, 5)

        font = painter.font()
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(QColor("#aab4d4"))
        painter.drawText(QRectF(m, 0, w - 2 * m, 13), Qt.AlignCenter,
                         f"已选 {fmt_ts(self._end - self._start)}")
        painter.setPen(QColor("#77839f"))
        painter.drawText(QRectF(m, track_y + track_h + 12, (w - 2 * m) / 2, 15),
                         Qt.AlignLeft | Qt.AlignVCenter, fmt_ts(self._start))
        painter.drawText(QRectF(m + (w - 2 * m) / 2, track_y + track_h + 12,
                                (w - 2 * m) / 2, 15),
                         Qt.AlignRight | Qt.AlignVCenter, fmt_ts(self._end))
        painter.end()

    # -- 交互 ---------------------------------------------------------
    def mousePressEvent(self, e):
        if not self._duration:
            return
        x = e.position().x()
        x1, x2 = self._x(self._start), self._x(self._end)
        if abs(x - x1) <= 12:
            self._drag = "start"
        elif abs(x - x2) <= 12:
            self._drag = "end"
        elif x1 < x < x2:
            self._drag = "region"
            self._region_len = self._end - self._start
        else:
            self._drag = "start" if abs(x - x1) < abs(x - x2) else "end"
            self._apply(self._drag, self._t(x))
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        t = self._t(e.position().x())
        if self._drag == "region":
            start = min(max(0.0, t - self._region_len / 2),
                        self._duration - self._region_len)
            self._start, self._end = start, start + self._region_len
        else:
            self._apply(self._drag, t)
        self.rangeChanged.emit(self._start, self._end)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self.rangeChanged.emit(self._start, self._end)
        self._drag = None

    def _apply(self, which, t):
        if which == "start":
            self._start = min(t, self._end - 0.05)
        else:
            self._end = max(t, self._start + 0.05)


class SegmentRow(QFrame):
    """片段行：#01 起点→终点（时长）+ 状态胶囊 + 删除/打开按钮。"""

    def __init__(self, index, start, end, parent=None):
        super().__init__(parent)
        self.setObjectName("FileRow")
        self._output = ""
        self._index = index

        badge = QLabel(f"{index + 1:02d}")
        badge.setObjectName("TypeBadge")
        badge.setProperty("badgeType", "video")
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        _repolish(badge)

        self.title = QLabel(f"{fmt_ts(start)}  →  {fmt_ts(end)}")
        self.title.setObjectName("FileName")
        self.meta = QLabel(f"时长 {fmt_ts(end - start)}")
        self.meta.setObjectName("FileMeta")
        self.out = QLabel("")
        self.out.setObjectName("FileOut")
        self.out.hide()

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(self.title)
        col.addWidget(self.meta)
        col.addWidget(self.out)
        col.addStretch(1)

        self.chip = QLabel("排队中")
        self.chip.setObjectName("Chip")
        self.chip.setProperty("chip", "queued")

        self.btn_open = QPushButton("📂")
        self.btn_open.setObjectName("OpenBtn")
        self.btn_open.setFixedSize(30, 26)
        self.btn_open.hide()
        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("IconBtn")
        self.btn_del.setFixedSize(30, 26)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(12)
        layout.addWidget(badge)
        layout.addLayout(col, 1)
        layout.addWidget(self.chip)
        layout.addWidget(self.btn_open)
        layout.addWidget(self.btn_del)

    def set_state(self, status, percent=0.0, error="", output_path="",
                  took_ms=0):
        from app.core.utils import fmt_took
        self.setProperty("status", status)
        _repolish(self)
        text = {
            "queued": "排队中",
            "running": f"导出中 {percent:.0f}%",
            "done": f"完成 · {fmt_took(took_ms)}",
            "error": "失败",
            "cancelled": "已取消",
        }.get(status, status)
        self.chip.setText(text)
        self.chip.setProperty("chip", status)
        _repolish(self.chip)
        self.chip.setToolTip(error if status == "error" else "")
        self.btn_open.setVisible(status == "done")
        self.btn_del.setVisible(status not in ("queued", "running"))
        if status == "done" and output_path:
            self._output = output_path
            self.out.setText("→ " + os.path.basename(output_path))
            self.out.show()
        if error:
            self.setToolTip(error)


class ClipPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window                      # 复用主窗口的队列 / 提示 / 事件桥
        self._src = None
        self._info = None
        self._segments = []                    # [{start,end,task_id,row}]
        self._seg_by_id = {}
        self._pending = set()
        self._out_dir = None
        self._syncing = False
        self._play_proc = None
        self._playing = False
        self._live_pos = None
        self._live_at = 0.0
        self._last_pos_emit = 0.0

        self._bridge = _ClipBridge()
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect()
        apply_default_out_dir(self, self.dir_chip)

    # ==================================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = QLabel("视频剪辑")
        t.setObjectName("PageTitle")
        title_col.addWidget(t)
        s = QLabel("时间轴选段、多段切分、合并导出，支持裁切 / 旋转 / 变速")
        s.setObjectName("PageSub")
        title_col.addWidget(s)
        head.addLayout(title_col)
        head.addStretch(1)
        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("GhostBtn")
        self.log_btn.setCheckable(True)
        head.addWidget(self.log_btn)
        layout.addLayout(head)

        layout.addWidget(self._build_panel_select())
        layout.addWidget(self._build_panel_export(), 1)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setObjectName("LogPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(800)
        self.log_panel.setFixedHeight(170)
        self.log_panel.hide()
        layout.addWidget(self.log_panel)

    # ------------------------------------------------------------------
    def _build_panel_select(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(8)

        cap = QLabel("选段")
        cap.setObjectName("SectionLabel")
        vbox.addWidget(cap)

        file_row = QHBoxLayout()
        self.btn_open_file = QPushButton("打开文件")
        self.btn_open_file.setObjectName("GhostBtn")
        self.file_name = QLabel("尚未选择文件（也可直接拖入）")
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

        self.timeline = Timeline()
        vbox.addWidget(self.timeline)

        times = QHBoxLayout()
        times.setSpacing(8)
        for text in ("起点", "终点"):
            lab = QLabel(text)
            lab.setObjectName("FieldLabel")
            times.addWidget(lab)
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0.0, 0.0)
            spin.setSingleStep(1.0)
            spin.setFixedWidth(110)
            spin.setKeyboardTracking(False)
            spin.setSuffix(" s")
            times.addWidget(spin)
            if text == "起点":
                self.spin_start = spin
            else:
                self.spin_end = spin
        times.addStretch(1)
        self.pos_label = QLabel("")
        self.pos_label.setObjectName("ValueLabel")
        times.addWidget(self.pos_label)
        self.btn_play = QPushButton("▶ 播放片段")
        self.btn_play.setObjectName("GhostBtn")
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setObjectName("GhostBtn")
        self.btn_stop.setEnabled(False)
        self.btn_setstart = QPushButton("⏬ 设为起点")
        self.btn_setstart.setObjectName("GhostBtn")
        self.btn_setstart.setEnabled(False)
        self.btn_setend = QPushButton("设为终点 ⏫")
        self.btn_setend.setObjectName("GhostBtn")
        self.btn_setend.setEnabled(False)
        for b in (self.btn_play, self.btn_stop, self.btn_setstart,
                  self.btn_setend):
            times.addWidget(b)
        vbox.addLayout(times)
        return panel

    # ------------------------------------------------------------------
    def _build_panel_export(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(8)

        cap = QLabel("片段与导出")
        cap.setObjectName("SectionLabel")
        vbox.addWidget(cap)

        seg_bar = QHBoxLayout()
        seg_bar.setSpacing(8)
        self.btn_add_seg = QPushButton("＋ 添加当前区间")
        self.btn_add_seg.setObjectName("GhostBtn")
        lab_min = QLabel("自动分段：每")
        lab_min.setObjectName("FieldLabel")
        self.spin_split = QDoubleSpinBox()
        self.spin_split.setDecimals(1)
        self.spin_split.setRange(0.1, 480.0)
        self.spin_split.setValue(10.0)
        self.spin_split.setSingleStep(1.0)
        self.spin_split.setSuffix(" 分钟")
        self.spin_split.setFixedWidth(96)
        self.spin_split.setKeyboardTracking(False)
        lab_min2 = QLabel("生成")
        lab_min2.setObjectName("FieldLabel")
        self.btn_split = QPushButton("生成分段")
        self.btn_split.setObjectName("GhostBtn")
        self.btn_clear_segs = QPushButton("清空")
        self.btn_clear_segs.setObjectName("GhostBtn")
        seg_bar.addWidget(self.btn_add_seg)
        seg_bar.addStretch(1)
        seg_bar.addWidget(lab_min)
        seg_bar.addWidget(self.spin_split)
        seg_bar.addWidget(lab_min2)
        seg_bar.addWidget(self.btn_split)
        seg_bar.addWidget(self.btn_clear_segs)
        vbox.addLayout(seg_bar)

        self.seg_scroll = QScrollArea()
        self.seg_scroll.setWidgetResizable(True)
        self.seg_scroll.setMinimumHeight(96)
        inner = QWidget()
        self.seg_layout = QVBoxLayout(inner)
        self.seg_layout.setContentsMargins(0, 0, 4, 0)
        self.seg_layout.setSpacing(8)
        self.seg_layout.addStretch(1)
        self.seg_scroll.setWidget(inner)
        vbox.addWidget(self.seg_scroll, 1)

        tf = QHBoxLayout()
        tf.setSpacing(8)
        lab_rot = QLabel("旋转")
        lab_rot.setObjectName("FieldLabel")
        self.combo_rotate = QComboBox()
        for name, _ in ROTATE_ITEMS:
            self.combo_rotate.addItem(name)
        lab_crop = QLabel("裁切")
        lab_crop.setObjectName("FieldLabel")
        self.edit_crop = QLineEdit()
        self.edit_crop.setPlaceholderText("w:h:x:y，可自动检测")
        self.edit_crop.setFixedWidth(150)
        self.btn_detect = QPushButton("🔍 自动检测黑边")
        self.btn_detect.setObjectName("GhostBtn")
        lab_spd = QLabel("变速")
        lab_spd.setObjectName("FieldLabel")
        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setDecimals(2)
        self.spin_speed.setRange(0.5, 2.0)
        self.spin_speed.setValue(1.0)
        self.spin_speed.setSingleStep(0.05)
        self.spin_speed.setSuffix(" ×")
        self.spin_speed.setFixedWidth(88)
        self.spin_speed.setKeyboardTracking(False)
        tf.addWidget(lab_rot)
        tf.addWidget(self.combo_rotate)
        tf.addSpacing(8)
        tf.addWidget(lab_crop)
        tf.addWidget(self.edit_crop)
        tf.addWidget(self.btn_detect)
        tf.addSpacing(8)
        tf.addWidget(lab_spd)
        tf.addWidget(self.spin_speed)
        tf.addStretch(1)
        vbox.addLayout(tf)

        mode = QHBoxLayout()
        mode.setSpacing(10)
        self.rb_lossless = QRadioButton("无损快切（关键帧对齐，最快）")
        self.rb_precise = QRadioButton("精确剪切（逐帧准确，重编码）")
        self.rb_lossless.setChecked(True)
        lab_enc = QLabel("编码")
        lab_enc.setObjectName("FieldLabel")
        self.combo_encode = QComboBox()
        for pid in ENCODE_PRESET_IDS:
            self.combo_encode.addItem(get_preset(pid)["name"], pid)
        self.dir_chip = QPushButton("📁  输出到：与源文件相同")
        self.dir_chip.setObjectName("DirChip")
        mode.addWidget(self.rb_lossless)
        mode.addWidget(self.rb_precise)
        mode.addStretch(1)
        mode.addWidget(lab_enc)
        mode.addWidget(self.combo_encode)
        mode.addSpacing(8)
        mode.addWidget(self.dir_chip)
        vbox.addLayout(mode)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_cancel = QPushButton("取消本页任务")
        self.btn_cancel.setObjectName("DangerBtn")
        self.btn_cancel.hide()
        self.btn_merge = QPushButton("合并导出为 1 个文件")
        self.btn_merge.setObjectName("GhostBtn")
        self.btn_export = QPushButton("导出全部片段")
        self.btn_export.setObjectName("StartBtn")
        actions.addStretch(1)
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_merge)
        actions.addWidget(self.btn_export)
        vbox.addLayout(actions)
        return panel

    # ==================================================================
    def _connect(self):
        self.btn_open_file.clicked.connect(self._pick_file)
        self.btn_play.clicked.connect(self._play_segment)
        self.btn_stop.clicked.connect(self.stop_preview)
        self.btn_setstart.clicked.connect(lambda: self._set_boundary("start"))
        self.btn_setend.clicked.connect(lambda: self._set_boundary("end"))
        self.log_btn.toggled.connect(self.log_panel.setVisible)

        self.spin_start.valueChanged.connect(
            lambda v: self._sync_to_timeline(self.spin_start, v))
        self.spin_end.valueChanged.connect(
            lambda v: self._sync_to_timeline(self.spin_end, v))
        self.timeline.rangeChanged.connect(self._sync_from_timeline)

        self.btn_add_seg.clicked.connect(self._add_segment)
        self.btn_split.clicked.connect(self._auto_split)
        self.btn_clear_segs.clicked.connect(self._clear_segments)
        self.btn_detect.clicked.connect(self._detect_crop)
        self.dir_chip.clicked.connect(self._pick_dir)
        self.btn_export.clicked.connect(self._export_segments)
        self.btn_merge.clicked.connect(self._export_merge)
        self.btn_cancel.clicked.connect(self._cancel_own)

        for combo in (self.combo_rotate,):
            combo.currentIndexChanged.connect(self._on_transform_changed)
        self.edit_crop.textChanged.connect(self._on_transform_changed)
        self.spin_speed.valueChanged.connect(self._on_transform_changed)

        self._bridge.fileProbed.connect(self._on_file_probed)
        self._bridge.keyframes.connect(self.timeline.set_keyframes)
        self._bridge.cropDone.connect(self._on_crop_done)
        self._bridge.posUpdate.connect(self._on_pos)
        self._bridge.playStopped.connect(self._on_play_stopped)

        self.win._bridge.events.connect(self._on_queue_event)
        self.win._bridge.logs.connect(self._on_log)
        self._refresh()

    # ==================================================================
    # 文件加载
    # ==================================================================
    def _pick_file(self):
        exts = " ".join(f"*.{e}" for e in
                        (self.win and self.win.EXT_ALL or {"mp4", "mkv", "mov", "avi"}))
        path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", "",
                                              f"视频文件 ({exts});;所有文件 (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        self.stop_preview()
        self._clear_segments()
        self._src = path
        self._info = None
        self.file_name.setText(os.path.basename(path))
        self.file_meta.setText("正在读取媒体信息…")
        self.timeline.set_keyframes([])
        threading.Thread(target=self._probe_worker, args=(path,),
                         daemon=True).start()
        self._refresh()

    def _probe_worker(self, path):
        from app.core.probe import probe_file
        try:
            info = probe_file(path)
            self._bridge.fileProbed.emit(True, info, "")
        except Exception as exc:              # noqa: BLE001
            self._bridge.fileProbed.emit(False, None, str(exc))

    def _on_file_probed(self, ok, info, error):
        if not ok:
            self.file_meta.setText("无法读取该文件")
            self.win.toast(f"剪辑：文件读取失败 - {error[:80]}")
            self._src = None
            self._refresh()
            return
        self._info = info
        dur = info.get("duration") or 0.0
        v, a = info.get("video"), info.get("audio")
        parts = [info.get("container") or ""]
        if v:
            parts.append(f"{v['w']}×{v['h']} {v['codec'].upper()}")
        if a:
            parts.append(f"{a['codec'].upper()} 音轨")
        self.file_meta.setText(" · ".join(p for p in parts if p))

        self._syncing = True
        for spin in (self.spin_start, self.spin_end):
            spin.setMaximum(dur)
        self.spin_start.setValue(0.0)
        self.spin_end.setValue(dur)
        self._syncing = False
        self.timeline.set_duration(dur)

        size = info.get("size") or 0
        if size < 1_500_000_000:              # 过大文件跳过关键帧刻度
            threading.Thread(target=self._keyframe_worker,
                             args=(self._src,), daemon=True).start()
        self._refresh()

    def _keyframe_worker(self, path):
        try:
            times = probe_keyframes(path)
            self._bridge.keyframes.emit(times)
        except Exception:                      # noqa: BLE001 - 失败仅降级为无刻度
            self._bridge.keyframes.emit([])

    # ==================================================================
    # 选段 / 预览
    # ==================================================================
    def _sync_to_timeline(self, spin, value):
        if self._syncing:
            return
        start, end = self.timeline.range()
        if spin is self.spin_start:
            start = min(value, end - 0.05)
        else:
            end = max(value, start + 0.05)
        # 用户输入越界时把数字框拉回到实际生效值
        self._syncing = True
        spin.setValue(start if spin is self.spin_start else end)
        self._syncing = False
        self.timeline.set_range(start, end)

    def _sync_from_timeline(self, start, end):
        self._syncing = True
        self.spin_start.setValue(start)
        self.spin_end.setValue(end)
        self._syncing = False

    def _play_segment(self):
        if not self._src:
            return
        start, end = self.timeline.range()
        duration = max(0.1, end - start)
        cmd = [FFPLAY, "-hide_banner", "-autoexit",
               "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
               "-window_title", "FFmpegUI 片段预览", self._src]
        try:
            self._play_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW)
        except FileNotFoundError:
            self.win.toast("未找到 ffplay，无法预览")
            return
        self._playing = True
        self._live_pos = None
        threading.Thread(target=self._pump_pos, args=(self._play_proc,),
                         daemon=True).start()
        self.btn_play.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_setstart.setEnabled(True)
        self.btn_setend.setEnabled(True)
        self.pos_label.setText("预览启动…")

    def _pump_pos(self, proc):
        buf = b""
        while proc.poll() is None:
            try:
                chunk = proc.stderr.read(64)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for line in lines:
                m = re.match(rb"\s*([0-9]+\.?[0-9]*)", line)
                if not m:
                    continue
                try:
                    pos = float(m.group(1))
                except ValueError:
                    continue
                now = time.time()
                if now - self._last_pos_emit >= 0.1:
                    self._last_pos_emit = now
                    self._bridge.posUpdate.emit(pos)
        self._bridge.playStopped.emit()

    def _on_pos(self, pos):
        if not self._playing:
            return
        self._live_pos = pos
        self._live_at = time.time()
        total = self.spin_end.value() - self.spin_start.value()
        self.pos_label.setText(f"播放中 {pos:.2f}s / {max(total, 0):.2f}s")

    def _on_play_stopped(self):
        self._playing = False
        self.btn_play.setEnabled(bool(self._src))
        self.btn_stop.setEnabled(False)
        self.btn_setstart.setEnabled(False)
        self.btn_setend.setEnabled(False)
        self.pos_label.setText("预览结束")

    def stop_preview(self):
        proc = self._play_proc
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid),
                                    "/T", "/F"], capture_output=True,
                                   creationflags=_NO_WINDOW)
                else:
                    proc.kill()
            except OSError:
                pass

    def _set_boundary(self, which):
        if self._live_pos is None or time.time() - self._live_at > 3:
            self.win.toast("请先播放，播放中可实时取点")
            return
        pos = self.spin_start.value() + self._live_pos
        if which == "start":
            self.spin_start.setValue(min(pos, self.spin_end.value() - 0.05))
        else:
            self.spin_end.setValue(max(pos, self.spin_start.value() + 0.05))

    # ==================================================================
    # 片段管理
    # ==================================================================
    def _add_segment(self):
        start, end = self.timeline.range()
        if end - start < 0.05:
            self.win.toast("区间太短")
            return
        self._insert_segment(start, end)

    def _insert_segment(self, start, end):
        row = SegmentRow(len(self._segments), start, end)
        row.btn_del.clicked.connect(
            lambda _, r=row: self._remove_segment(r))
        row.btn_open.clicked.connect(
            lambda _, r=row: self.win._open_in_explorer(r._output))
        self._segments.append({"start": start, "end": end,
                               "task_id": None, "row": row})
        self.seg_layout.insertWidget(self.seg_layout.count() - 1, row)
        self._refresh()

    def _remove_segment(self, row):
        self._segments = [s for s in self._segments if s["row"] is not row]
        self._seg_by_id = {s["task_id"]: s for s in self._segments
                           if s["task_id"]}
        row.setParent(None)
        row.deleteLater()
        self._refresh()

    def _clear_segments(self):
        if self._pending:
            self.win.toast("有任务进行中，无法清空")
            return
        for seg in list(self._segments):
            self._remove_segment(seg["row"])

    def _auto_split(self):
        if not self._info:
            self.win.toast("请先选择文件")
            return
        dur = self._info.get("duration") or 0.0
        step = self.spin_split.value() * 60.0
        if step <= 0 or dur <= 0:
            return
        if self._pending:
            self.win.toast("有任务进行中，无法重新分段")
            return
        for seg in list(self._segments):
            self._remove_segment(seg["row"])
        count = 0
        t = 0.0
        while t < dur and count < 200:
            self._insert_segment(t, min(t + step, dur))
            t += step
            count += 1
        self.win.toast(f"已生成 {count} 段")

    # ==================================================================
    # 变换 / 黑边检测
    # ==================================================================
    def _transform(self):
        return {
            "rotate": ROTATE_ITEMS[self.combo_rotate.currentIndex()][1],
            "crop": self.edit_crop.text().strip() or None,
            "speed": self.spin_speed.value(),
            "has_audio": bool(self._info and self._info.get("audio")),
        }

    def _transform_active(self):
        tf = self._transform()
        return bool(tf["rotate"] or tf["crop"] or
                    abs(tf["speed"] - 1.0) > 1e-6)

    def _on_transform_changed(self, *_):
        if self._transform_active() and self.rb_lossless.isChecked():
            self.rb_precise.setChecked(True)
            self.win.toast("已切换到精确剪切：裁切 / 旋转 / 变速需要重编码")

    def _detect_crop(self):
        if not self._src:
            self.win.toast("请先选择文件")
            return
        self.btn_detect.setEnabled(False)
        self.edit_crop.setPlaceholderText("检测中…")

        def work():
            result = detect_crop(self._src)
            self._bridge.cropDone.emit(result or "")
        threading.Thread(target=work, daemon=True).start()

    def _on_crop_done(self, result):
        self.btn_detect.setEnabled(True)
        self.edit_crop.setPlaceholderText("w:h:x:y，可自动检测")
        if result:
            self.edit_crop.setText(result)
            self.win.toast(f"检测到画面有效区域：{result}")
        else:
            self.win.toast("未检测到黑边（画面已是全幅）")

    # ==================================================================
    # 导出
    # ==================================================================
    def _encode_id(self):
        return self.combo_encode.currentData() or "x264-hq"

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                    self._out_dir or "")
        if directory:
            self._out_dir = directory
            shown = directory if len(directory) <= 40 else "…" + directory[-39:]
            self.dir_chip.setText(f"📁  输出到：{shown}")

    def _export_segments(self):
        if not self._src or not self._segments:
            self.win.toast("请先添加片段")
            return
        mode = "lossless" if self.rb_lossless.isChecked() else "precise"
        transform = self._transform()
        base = os.path.splitext(os.path.basename(self._src))[0]
        tasks = []
        for i, seg in enumerate(self._segments):
            inp, out, ext, label = build_segment(
                self._src, seg["start"], seg["end"], mode,
                self._encode_id(), transform)
            preset = {"args": out, "ext": ext, "name": f"剪辑·{label}"}
            task_id = f"{self._src}#seg{i}"
            task = Task(self._src, preset, self._out_dir,
                        duration=seg["end"] - seg["start"],
                        base_name=f"{base}_片段{i + 1:02d}",
                        task_id=task_id, input_args=inp)
            seg["task_id"] = task_id
            self._seg_by_id[task_id] = seg
            seg["row"].set_state("queued")
            self._pending.add(task_id)
            tasks.append(task)
        self._log(f"=== 剪辑：导出 {len(tasks)} 个片段（{mode}）")
        self.win.queue.enqueue(tasks)
        self._refresh()

    def _export_merge(self):
        if not self._src or not self._segments:
            self.win.toast("请先添加片段")
            return
        segments = [(s["start"], s["end"]) for s in self._segments]
        if len(segments) == 1:
            # 单段直接按普通片段导出，无需 concat
            self._export_single_merge(segments[0])
            return
        inp, out, ext, label, total = build_merge(
            self._src, segments, self._encode_id(), self._transform())
        base = os.path.splitext(os.path.basename(self._src))[0]
        preset = {"args": out, "ext": ext, "name": f"剪辑·合并·{label}"}
        task_id = f"{self._src}#merge"
        task = Task(self._src, preset, self._out_dir, duration=total,
                    base_name=f"{base}_合并", task_id=task_id,
                    input_args=inp, custom_input=True)
        seg = self._segments[0]
        seg["task_id"] = task_id
        self._seg_by_id[task_id] = seg
        seg["row"].set_state("queued")
        self._pending.add(task_id)
        self._log(f"=== 剪辑：合并 {len(segments)} 段导出")
        self.win.queue.enqueue([task])
        self._refresh()

    def _export_single_merge(self, span):
        inp, out, ext, label = build_segment(
            self._src, span[0], span[1],
            "lossless" if self.rb_lossless.isChecked() else "precise",
            self._encode_id(), self._transform())
        base = os.path.splitext(os.path.basename(self._src))[0]
        preset = {"args": out, "ext": ext, "name": f"剪辑·{label}"}
        task_id = f"{self._src}#merge"
        task = Task(self._src, preset, self._out_dir,
                    duration=span[1] - span[0], base_name=f"{base}_合并",
                    task_id=task_id, input_args=inp)
        seg = self._segments[0]
        seg["task_id"] = task_id
        self._seg_by_id[task_id] = seg
        seg["row"].set_state("queued")
        self._pending.add(task_id)
        self._log("=== 剪辑：导出所选区间")
        self.win.queue.enqueue([task])
        self._refresh()

    def _cancel_own(self):
        for task_id in list(self._pending):
            self.win.queue.cancel(task_id)

    def _on_queue_event(self, ev):
        if ev.get("event") != "task":
            return
        seg = self._seg_by_id.get(ev.get("id"))
        if seg is None:
            return
        seg["row"].set_state(ev["status"],
                             percent=ev.get("percent", 0),
                             error=ev.get("error", ""),
                             output_path=ev.get("output_path", ""),
                             took_ms=ev.get("took_ms", 0))
        if ev["status"] in ("done", "error", "cancelled"):
            self._pending.discard(ev["id"])
            if not self._pending:
                self.win.toast("剪辑任务全部结束")
        self._refresh()

    def _on_log(self, task_id, line):
        seg = self._seg_by_id.get(task_id)
        if seg is None:
            return
        self._log(f"[{os.path.basename(task_id.split('#')[0])}] {line}")

    def _log(self, text):
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    # ==================================================================
    def _refresh(self):
        loaded = bool(self._src and self._info)
        has_segs = bool(self._segments)
        busy = bool(self._pending)
        self.btn_play.setEnabled(loaded and not self._playing)
        self.btn_add_seg.setEnabled(loaded and not busy)
        self.btn_split.setEnabled(loaded and not busy)
        self.btn_clear_segs.setEnabled(has_segs and not busy)
        self.btn_detect.setEnabled(loaded)
        self.btn_export.setEnabled(loaded and has_segs and not busy)
        self.btn_export.setText("导出中…" if busy else "导出全部片段")
        self.btn_merge.setEnabled(loaded and has_segs and not busy)
        self.btn_cancel.setVisible(busy)
        self.btn_open_file.setEnabled(not busy)
        self.dir_chip.setEnabled(not busy)
        self.edit_crop.setEnabled(not busy)
        self.combo_encode.setEnabled(not busy)

    def shutdown(self):
        self.stop_preview()

    # ==================================================================
    # 拖放
    # ==================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        videos = [p for p in paths
                  if os.path.splitext(p)[1][1:].lower()
                  in (self.win.EXT_VIDEO if self.win else set())]
        if videos:
            event.acceptProposedAction()
            self.load_file(videos[0])
        else:
            self.win.toast("请拖入视频文件")
