# -*- coding: utf-8 -*-
"""主窗口：侧栏导航（含"待开发"占位）+ 转换页（拖放/预设/队列/日志）。"""
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)
from PySide6.QtCore import QUrl

from app.core.presets import PRESETS
from app.core.probe import probe_file
from app.core.queue import TaskQueue
from app.core.runner import FFMPEG, Task
from app.core.utils import fmt_size
from app.ui.audio_page import AudioPage
from app.ui.clip_page import ClipPage
from app.ui.file_row import FileRow
from app.ui.merge_page import MergePage
from app.ui.watermark_page import WatermarkPage
from app.ui.widgets import DropZone, PresetCard

VIDEO_EXT = {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "ts",
             "mpg", "mpeg", "3gp", "vob", "m2ts", "mts", "rmvb", "rm", "ogv"}
AUDIO_EXT = {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "ape",
             "aiff", "amr"}
ALL_EXT = VIDEO_EXT | AUDIO_EXT

# 侧栏"待开发"模块：名称 -> 计划版本
PLANNED_MODULES = [
    ("录屏 / 下载", "0.5"),
    ("设置", "0.5"),
]

# 已上线页面：导航名 -> (图标, 页面索引)
PAGES = [("转换", "🔄", 0), ("剪辑", "✂️", 1), ("音频工具", "🎵", 2),
         ("视频拼接", "🔗", 3), ("水印 / 字幕", "🏷️", 4)]

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class _Bridge(QObject):
    """跨线程信号桥：工作线程 -> Qt 主线程。"""
    events = Signal(object)          # 队列事件 dict
    logs = Signal(str, str)          # (任务id, 日志行)
    probeDone = Signal(str, object, str)   # (path, info|None, error)
    ffmpegInfo = Signal(bool, str)


class MainWindow(QMainWindow):
    EXT_ALL = ALL_EXT
    EXT_VIDEO = VIDEO_EXT

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpegUI — FFmpeg 图形界面工具")
        self.resize(1120, 750)
        self.setMinimumSize(960, 660)
        self.setAcceptDrops(True)

        self._files = {}            # path -> {"info", "status", "row"}
        self._busy = False
        self._preset = PRESETS[0]
        self._out_dir = None

        self._bridge = _Bridge()
        self.queue = TaskQueue(on_event=self._bridge.events.emit,
                               on_log=lambda i, l: self._bridge.logs.emit(i, l))

        self._build_ui()
        self._connect()
        self._detect_ffmpeg()
        self._refresh()

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self):
        root = QFrame()
        root.setObjectName("Root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()

        main = QFrame()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(10)

        # 页头
        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(QLabel("格式转换"))
        title_col.itemAt(0).widget().setObjectName("PageTitle")
        sub = QLabel("拖入文件，选择输出预设，一键完成转换")
        sub.setObjectName("PageSub")
        title_col.addWidget(sub)
        head.addLayout(title_col)
        head.addStretch(1)
        self.summary = QLabel("")
        self.summary.setObjectName("Summary")
        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("GhostBtn")
        self.log_btn.setCheckable(True)
        head_right = QVBoxLayout()
        head_right.setSpacing(6)
        hr = QHBoxLayout()
        hr.addStretch(1)
        hr.addWidget(self.summary)
        head_right.addLayout(hr)
        hr2 = QHBoxLayout()
        hr2.addStretch(1)
        hr2.addWidget(self.log_btn)
        head_right.addLayout(hr2)
        head.addLayout(head_right)
        main_layout.addLayout(head)

        # 拖放区
        self.drop = DropZone()
        main_layout.addWidget(self.drop)

        # 预设卡片
        sec1 = QLabel("输出预设")
        sec1.setObjectName("SectionLabel")
        main_layout.addWidget(sec1)
        grid_host = QWidget()
        self.preset_grid = QGridLayout(grid_host)
        self.preset_grid.setContentsMargins(0, 0, 0, 0)
        self.preset_grid.setSpacing(8)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        for i, preset in enumerate(PRESETS):
            card = PresetCard(preset)
            self.preset_group.addButton(card)
            self.preset_grid.addWidget(card, i // 4, i % 4)
        self.preset_grid.setColumnStretch(0, 1)
        self.preset_grid.setColumnStretch(1, 1)
        self.preset_grid.setColumnStretch(2, 1)
        self.preset_grid.setColumnStretch(3, 1)
        self.preset_group.buttons()[0].setChecked(True)
        main_layout.addWidget(grid_host)

        # 文件列表
        sec2 = QLabel("文件列表")
        sec2.setObjectName("SectionLabel")
        main_layout.addWidget(sec2)
        self.file_scroll = QScrollArea()
        self.file_scroll.setWidgetResizable(True)
        self.file_scroll.setMinimumHeight(90)
        inner = QWidget()
        inner.setObjectName("FileListInner")
        self.files_layout = QVBoxLayout(inner)
        self.files_layout.setContentsMargins(0, 0, 4, 0)
        self.files_layout.setSpacing(8)
        self.files_layout.addStretch(1)
        self.file_scroll.setWidget(inner)
        main_layout.addWidget(self.file_scroll, 1)

        # 日志面板（默认隐藏）
        self.log_panel = QPlainTextEdit()
        self.log_panel.setObjectName("LogPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(800)
        self.log_panel.setFixedHeight(170)
        self.log_panel.hide()
        main_layout.addWidget(self.log_panel)

        # 底部操作栏
        bar = QFrame()
        bar.setObjectName("ActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 8, 10, 8)
        bar_layout.setSpacing(10)
        self.dir_chip = QPushButton("📁  输出到：与源文件相同")
        self.dir_chip.setObjectName("DirChip")
        self.dir_chip.setCursor(Qt.PointingHandCursor)
        bar_layout.addWidget(self.dir_chip)
        bar_layout.addStretch(1)
        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setObjectName("GhostBtn")
        self.cancel_btn = QPushButton("取消全部")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.hide()
        self.start_btn = QPushButton("开始转换")
        self.start_btn.setObjectName("StartBtn")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        bar_layout.addWidget(self.clear_btn)
        bar_layout.addWidget(self.cancel_btn)
        bar_layout.addWidget(self.start_btn)
        main_layout.addWidget(bar)

        self.stack.addWidget(main)                 # 页 0：转换
        self.clip_page = ClipPage(self)
        self.stack.addWidget(self.clip_page)       # 页 1：剪辑
        self.audio_page = AudioPage(self)
        self.stack.addWidget(self.audio_page)      # 页 2：音频工具
        self.merge_page = MergePage(self)
        self.stack.addWidget(self.merge_page)      # 页 3：视频拼接
        self.watermark_page = WatermarkPage(self)
        self.stack.addWidget(self.watermark_page)  # 页 4：水印/字幕
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        # Toast 提示
        self.toast_label = QLabel("", self)
        self.toast_label.setObjectName("Toast")
        self.toast_label.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast_label.hide)

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(200)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(2)

        logo = QLabel("FFmpegUI")
        logo.setObjectName("Logo")
        layout.addWidget(logo)
        logo_sub = QLabel("简洁的音视频转换工具")
        logo_sub.setObjectName("LogoSub")
        layout.addWidget(logo_sub)
        layout.addSpacing(12)

        layout.addWidget(self._nav_section_label("功能"))
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (name, icon, _) in enumerate(PAGES):
            btn = self._nav_item(icon, name, active=True)
            self.nav_group.addButton(btn, index)
            layout.addWidget(btn)
        self.nav_group.button(0).setChecked(True)
        self.nav_group.idToggled.connect(
            lambda i, checked: checked and hasattr(self, "stack")
            and self.stack.setCurrentIndex(i))

        layout.addSpacing(10)
        layout.addWidget(self._nav_section_label("即将推出"))
        icons = {"录屏 / 下载": "⏺️", "设置": "⚙️"}
        for name, ver in PLANNED_MODULES:
            layout.addWidget(self._nav_item(icons[name], name,
                                            badge="待开发", ver=ver))

        layout.addStretch(1)

        foot = QFrame()
        foot.setObjectName("SidebarFoot")
        foot_layout = QVBoxLayout(foot)
        foot_layout.setContentsMargins(0, 0, 0, 0)
        foot_layout.setSpacing(0)
        self.foot_status = QLabel("正在检测 ffmpeg…")
        self.foot_status.setObjectName("FootStatus")
        self.foot_status.setWordWrap(True)
        self.foot_ver = QLabel("v0.4 · 水印字幕与 AI 字幕已上线")
        self.foot_ver.setObjectName("FootVer")
        foot_layout.addWidget(self.foot_status)
        foot_layout.addWidget(self.foot_ver)
        layout.addWidget(foot)
        return side

    @staticmethod
    def _nav_section_label(text):
        label = QLabel(text)
        label.setObjectName("NavSection")
        return label

    def _nav_item(self, icon, text, active=False, badge=None, ver=None):
        """统一结构的导航项：图标(20px) + 文字 + 徽标紧贴文字 + 弹性留白。"""
        btn = QPushButton()
        btn.setObjectName("NavButton" if active else "NavButtonLocked")
        btn.setFixedHeight(38)
        btn.setCursor(Qt.PointingHandCursor if active else Qt.ForbiddenCursor)

        btn_layout = QHBoxLayout(btn)
        btn_layout.setContentsMargins(12, 0, 12, 0)
        btn_layout.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setObjectName("NavIcon")
        icon_lbl.setFixedWidth(20)
        icon_lbl.setAlignment(Qt.AlignCenter)
        text_lbl = QLabel(text)
        text_lbl.setObjectName("NavText")
        btn_layout.addWidget(icon_lbl)
        btn_layout.addWidget(text_lbl)
        if badge:
            badge_lbl = QLabel(badge)
            badge_lbl.setObjectName("NavBadge")
            btn_layout.addSpacing(2)
            btn_layout.addWidget(badge_lbl)
        btn_layout.addStretch(1)

        if active:
            btn.setCheckable(True)
        else:
            btn.setToolTip(f"该模块开发中，计划于 v{ver} 推出")
            btn.clicked.connect(lambda _, n=text, v=ver:
                                self.toast(f"「{n}」模块开发中，计划于 v{v} 推出"))
        return btn

    # ==================================================================
    # 信号连接
    # ==================================================================
    def _connect(self):
        self.drop.clicked.connect(self._pick_files)
        self.dir_chip.clicked.connect(self._pick_dir)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self._cancel_all)
        self.clear_btn.clicked.connect(self._clear_finished)
        self.log_btn.toggled.connect(self.log_panel.setVisible)
        self.preset_group.buttonToggled.connect(self._on_preset_toggled)

        self._bridge.events.connect(self._on_queue_event)
        self._bridge.logs.connect(self._on_task_log)
        self._bridge.probeDone.connect(self._on_probe_done)
        self._bridge.ffmpegInfo.connect(self._on_ffmpeg_info)

    # ==================================================================
    # 文件添加 / 探测
    # ==================================================================
    def add_paths(self, paths):
        added, dup, skipped = [], 0, 0
        for path in paths:
            ext = os.path.splitext(path)[1][1:].lower()
            if ext not in ALL_EXT:
                skipped += 1
                continue
            if path in self._files:
                dup += 1
                continue
            row = FileRow(path)
            row.removeClicked.connect(self._remove_file)
            row.openClicked.connect(self._open_in_explorer)
            self._files[path] = {"info": None, "status": "probing", "row": row}
            self.files_layout.insertWidget(self.files_layout.count() - 1, row)
            added.append(path)

        if dup:
            self.toast(f"{dup} 个文件已在列表中，已跳过")
        if skipped:
            self.toast(f"已跳过 {skipped} 个不支持的文件")
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
        except Exception as exc:                # noqa: BLE001 - 探测失败要展示给用户
            self._bridge.probeDone.emit(path, None, str(exc))

    def _on_probe_done(self, path, info, error):
        item = self._files.get(path)
        if item is None:
            return
        if error:
            item["status"] = "error"
            item["row"].meta.setText("无法读取该文件")
            item["row"].set_state("error", error=error)
        else:
            item["info"] = info
            item["status"] = "ready"
            item["row"].set_info(info)
            item["row"].set_state("ready")
        self._refresh()

    def _pick_files(self):
        exts = " ".join(f"*.{e}" for e in sorted(ALL_EXT))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择媒体文件", "",
            f"所有支持的媒体文件 ({exts});;所有文件 (*)")
        if paths:
            self.add_paths(paths)

    # ==================================================================
    # 转换
    # ==================================================================
    def start(self):
        if self._busy:
            return
        convertible = [(p, it) for p, it in self._files.items()
                       if it["status"] in ("ready", "cancelled", "error")]
        # 探测失败的文件（无 info）不能转换
        convertible = [(p, it) for p, it in convertible if it["info"]]
        if not convertible:
            self.toast("请先添加可转换的文件")
            return

        tasks = []
        for path, item in convertible:
            duration = (item["info"] or {}).get("duration", 0)
            tasks.append(Task(path, self._preset, self._out_dir,
                              duration=duration))
            item["status"] = "queued"
            item["row"].set_state("queued")

        self._busy = True
        name = os.path.basename(convertible[0][0])
        self._log_line(f"=== 开始 {len(tasks)} 个任务 · 预设：{self._preset['name']}"
                       f" · 首个文件：{name}")
        self.queue.enqueue(tasks)
        self._refresh()

    def _on_queue_event(self, ev):
        if ev.get("event") == "queue-done":
            done = sum(1 for it in self._files.values()
                       if it["status"] == "done")
            failed = sum(1 for it in self._files.values()
                         if it["status"] == "error")
            self._busy = False
            text = f"全部任务结束：成功 {done} 个"
            if failed:
                text += f"，失败 {failed} 个"
            self.toast(text)
            self._refresh()
            return

        item = self._files.get(ev.get("id"))
        if item is None:
            return
        item["status"] = ev["status"]
        item["row"].set_state(ev["status"],
                              percent=ev.get("percent", 0),
                              speed=ev.get("speed", ""),
                              error=ev.get("error", ""),
                              output_path=ev.get("output_path", ""),
                              took_ms=ev.get("took_ms", 0))
        self._refresh()

    def _on_task_log(self, task_id, line):
        name = os.path.basename(task_id)
        self._log_line(f"[{name}] {line}")

    def _cancel_all(self):
        self.queue.cancel_all()
        self.toast("已请求取消全部任务")

    # ==================================================================
    # 列表管理
    # ==================================================================
    def _remove_file(self, path):
        item = self._files.get(path)
        if item is None:
            return
        if item["status"] in ("queued", "running"):
            self.toast("任务进行中，无法移除（可先取消）")
            return
        item["row"].setParent(None)
        item["row"].deleteLater()
        del self._files[path]
        if not self._files:
            self.drop.set_compact(False)
        self._refresh()

    def _clear_finished(self):
        for path in [p for p, it in self._files.items()
                     if it["status"] not in ("queued", "running")]:
            self._remove_file(path)

    # ==================================================================
    # 杂项
    # ==================================================================
    def _on_preset_toggled(self, btn, checked):
        if checked:
            self._preset = btn.preset
            self._refresh()

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                     self._out_dir or "")
        if directory:
            self._out_dir = directory
            home = os.path.expanduser("~")
            shown = directory if len(directory) <= 44 else "…" + directory[-43:]
            if shown.startswith(home):
                shown = "~" + shown[len(home):]
            self.dir_chip.setText(f"📁  输出到：{shown}")

    def _open_in_explorer(self, path):
        if not path:
            return
        if os.name == "nt" and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _detect_ffmpeg(self):
        def work():
            try:
                proc = subprocess.run([FFMPEG, "-version"], capture_output=True,
                                      timeout=5, creationflags=_NO_WINDOW)
                first = proc.stdout.decode("utf-8", "ignore").splitlines()
                ver = first[0].split()[2] if first and len(first[0].split()) > 2 else "?"
                self._bridge.ffmpegInfo.emit(True, ver)
            except Exception:                     # noqa: BLE001
                self._bridge.ffmpegInfo.emit(False, "")
        threading.Thread(target=work, daemon=True).start()

    def _on_ffmpeg_info(self, ok, ver):
        if ok:
            self.foot_status.setText(
                f'<span style="color:#3ddc84;">●</span> ffmpeg {ver} · 已就绪')
        else:
            self.foot_status.setText(
                '<span style="color:#ff5d6c;">●</span> 未检测到 ffmpeg，请安装并加入 PATH')

    def _log_line(self, text):
        sb = self.log_panel.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        self.log_panel.appendPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _refresh(self):
        n = len(self._files)
        ready = sum(1 for it in self._files.values() if it["status"] == "ready")
        done = sum(1 for it in self._files.values() if it["status"] == "done")
        failed = sum(1 for it in self._files.values() if it["status"] == "error")
        parts = [f"共 {n} 个文件"] if n else ["尚未添加文件"]
        if ready:
            parts.append(f"就绪 {ready}")
        if done:
            parts.append(f"完成 {done}")
        if failed:
            parts.append(f"失败 {failed}")
        self.summary.setText(" · ".join(parts))

        convertible = any(it["info"] and it["status"] in
                          ("ready", "cancelled", "error")
                          for it in self._files.values())
        self.start_btn.setEnabled(not self._busy and convertible)
        self.start_btn.setText("转换中…" if self._busy else "开始转换")
        self.cancel_btn.setVisible(self._busy)
        self.dir_chip.setEnabled(not self._busy)

    def show_page(self, name):
        """切换页面：'convert' / 'clip' / 'audio' / 'merge' / 'wm'。"""
        index = {"convert": 0, "clip": 1, "audio": 2, "merge": 3,
                 "wm": 4}.get(name, 0)
        self.nav_group.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)

    def toast(self, text, ms=2600):
        self.toast_label.setText(text)
        self.toast_label.adjustSize()
        self.toast_label.move(
            (self.width() - self.toast_label.width()) // 2, 58)
        self.toast_label.show()
        self.toast_label.raise_()
        self.toast_timer.start(ms)

    # ==================================================================
    # 拖放
    # ==================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop.set_hover(True)

    def dragLeaveEvent(self, event):
        self.drop.set_hover(False)

    def dropEvent(self, event):
        self.drop.set_hover(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def closeEvent(self, event):
        if hasattr(self, "clip_page"):
            self.clip_page.shutdown()
        if hasattr(self, "merge_page"):
            self.merge_page.shutdown()
        self.queue.cancel_all()
        super().closeEvent(event)
