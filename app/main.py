# -*- coding: utf-8 -*-
"""应用入口：python -m app.main"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.theme import QSS


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(QSS)
    try:                                          # 深色标题栏（Qt 6.8+）
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:                             # noqa: BLE001
        pass

    win = MainWindow()
    win.show()

    # 供演示 / 自动化验证：命令行传入的文件会自动加入列表
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        win.add_paths([sys.argv[1]])

    page = os.environ.get("FFGUI_PAGE")
    if page:
        win.show_page(page)
        if page == "clip" and len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            win.clip_page.load_file(sys.argv[1])

    shot = os.environ.get("FFGUI_SCREENSHOT")
    if shot:
        delay = int(os.environ.get("FFGUI_SHOT_DELAY", "2600"))
        QTimer.singleShot(
            delay, lambda: (win.grab().save(shot), app.quit()))

    autoquit = os.environ.get("FFGUI_AUTOQUIT_MS")
    if autoquit:
        QTimer.singleShot(int(autoquit), app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
