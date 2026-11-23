import os
import json

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, QLineEdit,
    QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal

from gui.core.claw_controller import ClawController

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "claw_config.json")


def _load_close_angle() -> float:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            return float(data.get("close_angle", 65.0))
    except Exception:
        pass
    return 65.0


def _save_close_angle(angle: float):
    """读-改-写，保留 servo_invert 等其他字段"""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        data = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        data["close_angle"] = float(angle)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


class ClawControl(QWidget):
    log_message = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.claw = ClawController()
        self._close_angle = _load_close_angle()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("夹爪控制")
        group_layout = QVBoxLayout()
        group.setLayout(group_layout)

        # 连接行
        conn_layout = QHBoxLayout()
        conn_layout.addWidget(QLabel("串口:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(["COM6", "COM3", "COM4", "COM5", "COM7", "COM8"])
        self.port_combo.setFixedWidth(80)
        conn_layout.addWidget(self.port_combo)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setStyleSheet(
            "QPushButton { background-color: #3b82f6; color: white; font-weight: bold; padding: 4px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2563eb; }"
        )
        self.btn_connect.clicked.connect(self._on_connect)
        conn_layout.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 4px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_disconnect.setEnabled(False)
        conn_layout.addWidget(self.btn_disconnect)
        conn_layout.addStretch()
        group_layout.addLayout(conn_layout)

        # 闭合角度输入
        angle_layout = QHBoxLayout()
        angle_layout.addWidget(QLabel("闭合角度:"))
        self.angle_input = QLineEdit(str(self._close_angle))
        self.angle_input.setFixedWidth(60)
        self.angle_input.setAlignment(Qt.AlignCenter)
        self.angle_input.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        self.angle_input.editingFinished.connect(self._on_angle_changed)
        angle_layout.addWidget(self.angle_input)
        angle_layout.addWidget(QLabel("°  (0=完全闭合, 90=完全张开)"))
        angle_layout.addStretch()
        group_layout.addLayout(angle_layout)

        # 按钮行
        btn_layout = QHBoxLayout()

        self.btn_open = QPushButton("张开 (90°)")
        self.btn_open.setStyleSheet(
            "QPushButton { background-color: #22c55e; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background-color: #16a34a; }"
        )
        self.btn_open.clicked.connect(self._on_open)
        self.btn_open.setEnabled(False)
        btn_layout.addWidget(self.btn_open)

        self.btn_close = QPushButton("闭合")
        self.btn_close.setStyleSheet(
            "QPushButton { background-color: #f59e0b; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background-color: #d97706; }"
        )
        self.btn_close.clicked.connect(self._on_close)
        self.btn_close.setEnabled(False)
        btn_layout.addWidget(self.btn_close)

        group_layout.addLayout(btn_layout)
        layout.addWidget(group)

    def _on_angle_changed(self):
        try:
            val = float(self.angle_input.text())
            val = max(0.0, min(90.0, val))
            self._close_angle = val
            self.angle_input.setText(str(val))
            _save_close_angle(val)
        except ValueError:
            self.angle_input.setText(str(self._close_angle))

    def _on_connect(self):
        port = self.port_combo.currentText().strip()
        self.claw.port = port
        if self.claw.connect():
            self.claw.reload_config()
            self.log_message.emit("info", f"夹爪已连接: {port}｜方向: {self.claw.direction_desc()}")
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.btn_open.setEnabled(True)
            self.btn_close.setEnabled(True)
            self.port_combo.setEnabled(False)
        else:
            self.log_message.emit("error", f"夹爪连接失败: {port}")

    def _on_disconnect(self):
        reply = QMessageBox.question(self, "断开夹爪", "确认断开夹爪连接？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._do_disconnect()

    def _do_disconnect(self):
        self.claw.disconnect()
        self.log_message.emit("info", "夹爪已断开")
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.btn_open.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.port_combo.setEnabled(True)

    def _on_open(self):
        if self.claw.set_angle(90):
            self.log_message.emit("info", "夹爪张开: 90°")
        else:
            self.log_message.emit("error", "夹爪控制失败")

    def _on_close(self):
        angle = self._close_angle
        if self.claw.set_angle(angle):
            self.log_message.emit("info", f"夹爪闭合: {angle}°")
        else:
            self.log_message.emit("error", "夹爪控制失败")

    def clear(self):
        self._do_disconnect()
