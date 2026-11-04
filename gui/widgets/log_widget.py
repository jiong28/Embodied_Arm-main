import os
import logging
from datetime import datetime

from PyQt5.QtWidgets import QTextEdit, QVBoxLayout, QLabel, QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor, QColor, QTextCharFormat


class LogWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(self._log_dir, exist_ok=True)
        self._log_file = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("操作日志")
        header.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(header)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumHeight(150)
        self.text_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.text_edit)

    def _get_log_file(self):
        if self._log_file is None:
            today = datetime.now().strftime("%Y%m%d")
            log_path = os.path.join(self._log_dir, f"gui_{today}.log")
            self._log_file = open(log_path, "a", encoding="utf-8")
        return self._log_file

    def _append(self, msg: str, color: str = "#333333"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {msg}"

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))

        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(formatted + "\n", fmt)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

        try:
            f = self._get_log_file()
            f.write(formatted + "\n")
            f.flush()
        except Exception:
            pass

    def log_info(self, msg: str):
        self._append(msg, "#333333")

    def log_warn(self, msg: str):
        self._append(f"⚠ {msg}", "#b45309")

    def log_error(self, msg: str):
        self._append(f"✗ {msg}", "#dc2626")

    def log_success(self, msg: str):
        self._append(f"✓ {msg}", "#16a34a")

    def close_log(self):
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
