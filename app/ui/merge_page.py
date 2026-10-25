# -*- coding: utf-8 -*-
"""视频拼接页：多文件按顺序合并，编码一致走 concat 流复制，否则重编码统一。"""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QVBoxLayout, QWidget)

from app.core.clipping import ENCODE_PRESET_IDS
from app.core.merging import build_fast_join, build_join, check_compatible
from app.core.presets import get_preset
from app.core.probe import probe_file
from app.core.runner import Task
from app.core.utils import fmt_duration, fmt_fps
from app.ui.widgets import apply_default_out_dir


def _repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


class _MergeBridge(QObject):
    probeDone = Signal(str, object, str)      # (path, info|None, error)


class MergeRow(QFrame):
    """顺序列表中的一行：序号徽标 + 名称/信息 + 上移/下移/删除。"""

    def __init__(self, index, path, info, on_up, on_down, on_del,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("FileRow")
        self.path = path

        badge = QLabel(f"{index + 1:02d}")
        badge.setObjectName("TypeBadge")
        badge.setProperty("badgeType", "video")
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        _repolish(badge)

        self.name = QLabel(os.path.basename(path))
        self.name.setObjectName("FileName")
        self.name.setToolTip(path)
        if info:
            v, a = info.get("video"), info.get("audio")
            parts = [f"{v['codec'].upper()} · {v['w']}×{v['h']}"]
            fps = fmt_fps(v.get("fps"))
            if fps:
                parts.append(f"{fps} fps")
            parts.append(fmt_duration(info.get("duration")))
            if not a:
                parts.append("无音轨")
            self.meta = QLabel(" · ".join(parts))
        else:
            self.meta = QLabel("正在读取媒体信息…")
        self.meta.setObjectName("FileMeta")

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(self.name)
        col.addWidget(self.meta)
        col.addStretch(1)

        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_del = QPushButton("✕")
        for b in (self.btn_up, self.btn_down, self.btn_del):
            b.setObjectName("IconBtn")
            b.setFixedSize(28, 26)
        self.btn_up.setToolTip("上移")
        self.btn_down.setToolTip("下移")
        self.btn_del.setToolTip("移除")
        self.btn_up.clicked.connect(lambda: on_up(path))
        self.btn_down.clicked.connect(lambda: on_down(path))
        self.btn_del.clicked.connect(lambda: on_del(path))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(12)
        layout.addWidget(badge)
        layout.addLayout(col, 1)
        layout.addWidget(self.btn_up)
        layout.addWidget(self.btn_down)
        layout.addWidget(self.btn_del)


class MergePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self._items = []                      # [{path, info, error}]，按顺序
        self._out_dir = None
        self._pending = set()
        self._fast_ok = False
        self._list_path = None                # concat 清单临时文件
        self._output = ""
        self._bridge = _MergeBridge()
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect()
        apply_default_out_dir(self, self.dir_chip)
        self._refresh()

    # ==================================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        t = QLabel("视频拼接")
        t.setObjectName("PageTitle")
        tcol.addWidget(t)
        s = QLabel("按顺序合并多个视频，编码一致时免转码秒级完成")
        s.setObjectName("PageSub")
        tcol.addWidget(s)
        head.addLayout(tcol)
        head.addStretch(1)
        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("GhostBtn")
        self.log_btn.setCheckable(True)
        head.addWidget(self.log_btn)
        layout.addLayout(head)

        sec1 = QLabel("文件列表（按顺序拼接）")
        sec1.setObjectName("SectionLabel")
        layout.addWidget(sec1)
        bar_row = QHBoxLayout()
        self.btn_add = QPushButton("＋ 打开文件")
        self.btn_add.setObjectName("GhostBtn")
        self.hint = QLabel("拖入或打开 2 个以上视频文件")
        self.hint.setObjectName("PageSub")
        bar_row.addWidget(self.btn_add)
        bar_row.addSpacing(8)
        bar_row.addWidget(self.hint, 1)
        layout.addLayout(bar_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(110)
        inner = QWidget()
        self.rows_layout = QVBoxLayout(inner)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(inner)
        layout.addWidget(self.scroll, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("Summary")
        layout.addWidget(self.summary)

        sec2 = QLabel("导出设置")
        sec2.setObjectName("SectionLabel")
        layout.addWidget(sec2)
        panel = QFrame()
        panel.setObjectName("Panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(14, 12, 14, 12)
        pv.setSpacing(8)

        self.compat = QLabel("")
        self.compat.setObjectName("ValueLabel")
        self.compat.setWordWrap(True)
        pv.addWidget(self.compat)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        self.rb_fast = QRadioButton("快速合并（流复制，不重编码）")
        self.rb_re = QRadioButton("重编码合并（统一分辨率帧率）")
        self.rb_fast.setChecked(True)
        lab_enc = QLabel("编码")
        lab_enc.setObjectName("FieldLabel")
        self.combo_encode = QComboBox()
        for pid in ENCODE_PRESET_IDS:
            self.combo_encode.addItem(get_preset(pid)["name"], pid)
        mode_row.addWidget(self.rb_fast)
        mode_row.addWidget(self.rb_re)
        mode_row.addStretch(1)
        mode_row.addWidget(lab_enc)
        mode_row.addWidget(self.combo_encode)
        pv.addLayout(mode_row)

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
        self.btn_open.setToolTip("打开输出所在文件夹")
        self.btn_open.hide()
        prog_row.addWidget(self.bar, 1)
        prog_row.addWidget(self.chip)
        prog_row.addWidget(self.btn_open)
        pv.addLayout(prog_row)

        action_row = QHBoxLayout()
        self.dir_chip = QPushButton("📁  输出到：与源文件相同")
        self.dir_chip.setObjectName("DirChip")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("DangerBtn")
        self.btn_cancel.hide()
        self.btn_start = QPushButton("开始合并")
        self.btn_start.setObjectName("StartBtn")
        action_row.addWidget(self.dir_chip)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_start)
        pv.addLayout(action_row)
        layout.addWidget(panel)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setObjectName("LogPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(800)
        self.log_panel.setFixedHeight(170)
        self.log_panel.hide()
        layout.addWidget(self.log_panel)

    def _connect(self):
        self.btn_add.clicked.connect(self._pick_files)
        self.btn_open.clicked.connect(
            lambda: self.win._open_in_explorer(self._output))
        self.dir_chip.clicked.connect(self._pick_dir)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.log_btn.toggled.connect(self.log_panel.setVisible)
        self._bridge.probeDone.connect(self._on_probe_done)
        self.win._bridge.events.connect(self._on_queue_event)
        self.win._bridge.logs.connect(self._on_log)

    # ==================================================================
    def add_paths(self, paths):
        fresh = []
        for path in paths:
            ext = os.path.splitext(path)[1][1:].lower()
            if ext not in self.win.EXT_VIDEO or \
                    any(it["path"] == path for it in self._items):
                continue
            self._items.append({"path": path, "info": None, "error": ""})
            fresh.append(path)
        if fresh:
            for path in fresh:                   # 并行探测，互不排队
                threading.Thread(target=self._probe_one, args=(path,),
                                 daemon=True).start()
        self._rebuild()
        self._update_compat()

    def _probe_one(self, path):
        try:
            info = probe_file(path)
            self._bridge.probeDone.emit(path, info, "")
        except Exception as exc:              # noqa: BLE001
            self._bridge.probeDone.emit(path, None, str(exc))

    def _on_probe_done(self, path, info, error):
        for item in self._items:
            if item["path"] == path:
                item["info"] = info
                item["error"] = error
                break
        self._rebuild()
        self._update_compat()

    # ==================================================================
    def _rebuild(self):
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, entry in enumerate(self._items):
            row = MergeRow(i, entry["path"], entry["info"],
                           self._move_up, self._move_down, self._remove)
            if self._pending:
                for b in (row.btn_up, row.btn_down, row.btn_del):
                    b.setEnabled(False)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        total = sum((it["info"] or {}).get("duration", 0) for it in self._items)
        self.summary.setText(
            f"共 {len(self._items)} 个文件 · 总时长 {fmt_duration(total)}")

    def _move_up(self, path):
        self._swap(path, -1)

    def _move_down(self, path):
        self._swap(path, 1)

    def _swap(self, path, delta):
        if self._pending:
            return
        idx = next((i for i, it in enumerate(self._items)
                    if it["path"] == path), None)
        if idx is None:
            return
        new = idx + delta
        if 0 <= new < len(self._items):
            self._items[idx], self._items[new] = self._items[new], self._items[idx]
            self._rebuild()
            self._update_compat()

    def _remove(self, path):
        if self._pending:
            return
        self._items = [it for it in self._items if it["path"] != path]
        self._rebuild()
        self._update_compat()
        self._refresh()

    # ==================================================================
    def _update_compat(self):
        self._fast_ok = False
        infos = [it["info"] for it in self._items if it["info"]]
        if any(it["error"] for it in self._items):
            self.compat.setText('<span style="color:#ff5d6c;">✗</span> '
                                "有文件读取失败，请移除后继续")
            self.rb_re.setChecked(True)
        elif len(self._items) < 2 or len(infos) < len(self._items):
            self.compat.setText("添加至少 2 个视频文件（正在读取信息…）")
        else:
            ok, reason = check_compatible(infos)
            if ok:
                self.compat.setText(
                    f'<span style="color:#3ddc84;">✓</span> {reason}')
                self._fast_ok = True
                if not self._pending:
                    self.rb_fast.setChecked(True)
            else:
                self.compat.setText(
                    f'<span style="color:#ffb454;">✗</span> {reason}'
                    '<span style="color:#77839f;"> —— 将使用重编码合并</span>')
                self.rb_re.setChecked(True)
        self._refresh()

    # ==================================================================
    def start(self):
        if self._pending or len(self._items) < 2:
            return
        paths = [it["path"] for it in self._items]
        infos = [it["info"] for it in self._items]
        if not all(infos):
            return
        base = os.path.splitext(os.path.basename(paths[0]))[0]
        total = sum((i.get("duration") or 0) for i in infos)
        tid = f"{paths[0]}#join"

        if self.rb_fast.isChecked() and self.rb_fast.isEnabled():
            inp, out, ext, label, self._list_path = build_fast_join(paths)
            task = Task(paths[0], {"args": out, "ext": ext,
                                   "name": f"拼接·{label}"},
                        self._out_dir, duration=total,
                        base_name=f"{base}_拼接", task_id=tid,
                        input_args=inp, custom_input=True)
        else:
            inp, out, ext, label, total = build_join(
                paths, infos, self.combo_encode.currentData() or "x264-hq")
            task = Task(paths[0], {"args": out, "ext": "mp4",
                                   "name": f"拼接·{label}"},
                        self._out_dir, duration=total,
                        base_name=f"{base}_拼接", task_id=tid,
                        input_args=inp, custom_input=True)
        self._pending = {tid}
        self._output = ""
        self.bar.setValue(0)
        self._set_chip("queued", "排队中")
        self._log(f"=== 拼接：{len(paths)} 个文件 · "
                  f"{'快速' if self.rb_fast.isChecked() else '重编码'}合并")
        self.win.queue.enqueue([task])
        self._rebuild()
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
            self._set_chip("running", f"合并中 {ev.get('percent', 0):.0f}%")
        elif status == "done":
            self.bar.setValue(100)
            self._set_chip("done", "完成")
            self._output = ev.get("output_path", "")
            self.btn_open.setVisible(bool(self._output))
            self.win.toast("拼接完成")
        elif status == "error":
            self._set_chip("error", "失败")
            self.chip.setToolTip(ev.get("error", ""))
            self.win.toast(f"拼接失败：{(ev.get('error') or '')[-90:]}")
        elif status == "cancelled":
            self._set_chip("cancelled", "已取消")
        if status in ("done", "error", "cancelled"):
            self._cleanup_list()
            self._pending.clear()
        self._refresh()

    def _cleanup_list(self):
        if self._list_path and os.path.exists(self._list_path):
            try:
                os.remove(self._list_path)
            except OSError:
                pass
        self._list_path = None

    def _on_log(self, task_id, line):
        if task_id not in self._pending and \
                not task_id.endswith("#join"):
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

    def _pick_files(self):
        exts = " ".join(f"*.{e}" for e in sorted(self.win.EXT_VIDEO))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件（可多选）", "",
            f"视频文件 ({exts});;所有文件 (*)")
        if paths:
            self.add_paths(paths)

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                    self._out_dir or "")
        if directory:
            self._out_dir = directory
            shown = directory if len(directory) <= 40 else "…" + directory[-39:]
            self.dir_chip.setText(f"📁  输出到：{shown}")

    def _refresh(self):
        busy = bool(self._pending)
        ready = len(self._items) >= 2 and all(it["info"] for it in self._items)
        self.btn_add.setEnabled(not busy)
        self.rb_fast.setEnabled(not busy and self._fast_ok)
        self.rb_re.setEnabled(not busy)
        self.combo_encode.setEnabled(not busy)
        self.dir_chip.setEnabled(not busy)
        self.btn_start.setEnabled(not busy and ready)
        self.btn_start.setText("合并中…" if busy else "开始合并")
        self.btn_cancel.setVisible(busy)
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
            self.add_paths(paths)

    def shutdown(self):
        self._cleanup_list()
