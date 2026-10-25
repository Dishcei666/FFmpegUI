# -*- coding: utf-8 -*-
"""音频工具页：批量格式转换 + 音量归一化 + 降噪（视频文件自动提取音轨）。"""
import os
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from app.core.audio import AUDIO_PRESETS, build_audio
from app.core.probe import probe_file
from app.core.runner import Task
from app.ui.file_row import FileRow
from app.ui.widgets import DropZone, PresetCard, apply_default_out_dir


class _AudioBridge(QObject):
    probeDone = Signal(str, object, str)      # (path, info|None, error)


class AudioPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self._files = {}                      # path -> {info,status,row,tid}
        self._by_tid = {}
        self._preset = AUDIO_PRESETS[0]
        self._out_dir = None
        self._pending = set()
        self._bridge = _AudioBridge()
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
        t = QLabel("音频工具")
        t.setObjectName("PageTitle")
        tcol.addWidget(t)
        s = QLabel("批量格式转换 · 音量归一化 · 降噪（视频文件自动提取音轨）")
        s.setObjectName("PageSub")
        tcol.addWidget(s)
        head.addLayout(tcol)
        head.addStretch(1)
        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("GhostBtn")
        self.log_btn.setCheckable(True)
        head.addWidget(self.log_btn)
        layout.addLayout(head)

        self.drop = DropZone("拖入音频 / 视频文件", "视频将自动提取音轨 · 支持批量")
        layout.addWidget(self.drop)

        sec1 = QLabel("输出格式")
        sec1.setObjectName("SectionLabel")
        layout.addWidget(sec1)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        for i, preset in enumerate(AUDIO_PRESETS):
            card = PresetCard(preset)
            self.preset_group.addButton(card)
            grid.addWidget(card, i // 4, i % 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        self.preset_group.buttons()[0].setChecked(True)
        layout.addWidget(grid_host)

        opts = QHBoxLayout()
        opts.setSpacing(14)
        lab = QLabel("处理选项")
        lab.setObjectName("FieldLabel")
        self.cb_norm = QCheckBox("音量归一化")
        self.cb_norm.setToolTip("EBU R128 响度统一到 -16 LUFS，多平台发布音量一致")
        self.cb_dn = QCheckBox("降噪")
        self.cb_dn.setToolTip("afftdn 频域降噪，适合录音/语音素材")
        opts.addWidget(lab)
        opts.addWidget(self.cb_norm)
        opts.addWidget(self.cb_dn)
        opts.addStretch(1)
        layout.addLayout(opts)

        sec2 = QLabel("文件列表")
        sec2.setObjectName("SectionLabel")
        layout.addWidget(sec2)
        self.file_scroll = QScrollArea()
        self.file_scroll.setWidgetResizable(True)
        self.file_scroll.setMinimumHeight(80)
        inner = QWidget()
        self.files_layout = QVBoxLayout(inner)
        self.files_layout.setContentsMargins(0, 0, 4, 0)
        self.files_layout.setSpacing(8)
        self.files_layout.addStretch(1)
        self.file_scroll.setWidget(inner)
        layout.addWidget(self.file_scroll, 1)

        bar = QFrame()
        bar.setObjectName("ActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 8, 10, 8)
        bar_layout.setSpacing(10)
        self.dir_chip = QPushButton("📁  输出到：与源文件相同")
        self.dir_chip.setObjectName("DirChip")
        bar_layout.addWidget(self.dir_chip)
        bar_layout.addStretch(1)
        self.cancel_btn = QPushButton("取消全部")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.hide()
        self.start_btn = QPushButton("开始转换")
        self.start_btn.setObjectName("StartBtn")
        bar_layout.addWidget(self.cancel_btn)
        bar_layout.addWidget(self.start_btn)
        layout.addWidget(bar)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setObjectName("LogPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(800)
        self.log_panel.setFixedHeight(170)
        self.log_panel.hide()
        layout.addWidget(self.log_panel)

    def _connect(self):
        self.drop.clicked.connect(self._pick_files)
        self.dir_chip.clicked.connect(self._pick_dir)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self._cancel_all)
        self.log_btn.toggled.connect(self.log_panel.setVisible)
        self.preset_group.buttonToggled.connect(
            lambda btn, checked: checked and setattr(self, "_preset",
                                                     btn.preset))
        self._bridge.probeDone.connect(self._on_probe_done)
        self.win._bridge.events.connect(self._on_queue_event)
        self.win._bridge.logs.connect(self._on_log)

    # ==================================================================
    def add_paths(self, paths):
        added = []
        for path in paths:
            ext = os.path.splitext(path)[1][1:].lower()
            if ext not in self.win.EXT_ALL or path in self._files:
                continue
            row = FileRow(path)
            row.removeClicked.connect(self._remove_file)
            row.openClicked.connect(self.win._open_in_explorer)
            self._files[path] = {"info": None, "status": "probing",
                                 "row": row, "tid": None}
            self.files_layout.insertWidget(self.files_layout.count() - 1, row)
            added.append(path)
        if added:
            self.drop.set_compact(True)
            for path in added:                   # 并行探测，互不排队
                threading.Thread(target=self._probe_one, args=(path,),
                                 daemon=True).start()
        self._refresh()

    def _probe_one(self, path):
        try:
            info = probe_file(path)
            self._bridge.probeDone.emit(path, info, "")
        except Exception as exc:              # noqa: BLE001
            self._bridge.probeDone.emit(path, None, str(exc))

    def _on_probe_done(self, path, info, error):
        item = self._files.get(path)
        if item is None:
            return
        if error:
            item["status"] = "error"
            item["row"].meta.setText("无法读取该文件")
            item["row"].set_state("error", error=error)
        elif not info.get("audio"):
            item["status"] = "error"
            item["row"].meta.setText("该文件没有音轨")
            item["row"].set_state("error", error="没有音轨，无法转换")
        else:
            item["info"] = info
            item["status"] = "ready"
            item["row"].set_info(info)
            item["row"].set_state("ready")
        self._refresh()

    def _pick_files(self):
        exts = " ".join(f"*.{e}" for e in sorted(self.win.EXT_ALL))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择音频/视频文件", "",
            f"所有支持的文件 ({exts});;所有文件 (*)")
        if paths:
            self.add_paths(paths)

    def _remove_file(self, path):
        item = self._files.get(path)
        if item is None or item["status"] in ("queued", "running"):
            return
        if item["tid"]:
            self._by_tid.pop(item["tid"], None)
        item["row"].setParent(None)
        item["row"].deleteLater()
        del self._files[path]
        if not self._files:
            self.drop.set_compact(False)
        self._refresh()

    # ==================================================================
    def start(self):
        if self._pending:
            return
        items = [(p, it) for p, it in self._files.items()
                 if it["status"] in ("ready", "error", "cancelled")
                 and it["info"] and it["info"].get("audio")]
        if not items:
            self.win.toast("请先添加含音轨的文件")
            return
        out, ext, label = build_audio(self._preset["id"],
                                      self.cb_norm.isChecked(),
                                      self.cb_dn.isChecked())
        tasks = []
        for path, item in items:
            tid = f"{path}#audio"
            task = Task(path, {"args": out, "ext": ext, "name": f"音频·{label}"},
                        self._out_dir, duration=item["info"].get("duration", 0),
                        task_id=tid)
            item["tid"] = tid
            self._by_tid[tid] = path
            item["row"].set_state("queued")
            self._pending.add(tid)
            tasks.append(task)
        self._log(f"=== 音频：{len(tasks)} 个文件 · {label}")
        self.win.queue.enqueue(tasks)
        self._refresh()

    def _cancel_all(self):
        for tid in list(self._pending):
            self.win.queue.cancel(tid)

    def _on_queue_event(self, ev):
        if ev.get("event") != "task":
            return
        path = self._by_tid.get(ev.get("id"))
        if path is None:
            return
        item = self._files.get(path)
        if item is None:
            return
        item["status"] = ev["status"]
        item["row"].set_state(ev["status"],
                              percent=ev.get("percent", 0),
                              speed=ev.get("speed", ""),
                              error=ev.get("error", ""),
                              output_path=ev.get("output_path", ""),
                              took_ms=ev.get("took_ms", 0))
        if ev["status"] in ("done", "error", "cancelled"):
            self._pending.discard(ev["id"])
            if not self._pending:
                done = sum(1 for it in self._files.values()
                           if it["status"] == "done")
                self.win.toast(f"音频任务结束：成功 {done} 个")
        self._refresh()

    def _on_log(self, task_id, line):
        path = self._by_tid.get(task_id)
        if path is None:
            return
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(f"[{os.path.basename(path)}] {line}")
        if at_bottom:
            sb.setValue(sb.maximum())

    def _log(self, text):
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                     self._out_dir or "")
        if directory:
            self._out_dir = directory
            shown = directory if len(directory) <= 44 else "…" + directory[-43:]
            self.dir_chip.setText(f"📁  输出到：{shown}")

    def _refresh(self):
        busy = bool(self._pending)
        convertible = any(it["info"] and it["info"].get("audio")
                          and it["status"] in ("ready", "error", "cancelled")
                          for it in self._files.values())
        self.start_btn.setEnabled(not busy and convertible)
        self.start_btn.setText("转换中…" if busy else "开始转换")
        self.cancel_btn.setVisible(busy)
        self.dir_chip.setEnabled(not busy)
        self.drop.setEnabled(not busy)

    # ==================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        paths = [p for p in paths
                 if os.path.splitext(p)[1][1:].lower() in self.win.EXT_ALL]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)
