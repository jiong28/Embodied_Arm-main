from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QGroupBox, QGridLayout, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal

from gui.core.motor_manager import MotorManager


class SyncControl(QWidget):
    log_message = pyqtSignal(str, str)

    def __init__(self, motor_manager: MotorManager, parent=None):
        super().__init__(parent)
        self.motor_manager = motor_manager
        self._motor_ids = []
        self._inputs = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("多机同步控制")
        group_layout = QVBoxLayout()
        group.setLayout(group_layout)

        grid = QGridLayout()
        self._grid_layout = grid
        group_layout.addLayout(grid)

        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("速度 (RPM):"))
        self.speed_input = QLineEdit("100")
        self.speed_input.setFixedWidth(60)
        speed_layout.addWidget(self.speed_input)
        speed_layout.addStretch()
        group_layout.addLayout(speed_layout)

        btn_layout = QHBoxLayout()
        self.btn_sync = QPushButton("同步运动")
        self.btn_sync.setStyleSheet("QPushButton { background-color: #3b82f6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                                     "QPushButton:hover { background-color: #2563eb; }")
        self.btn_sync.clicked.connect(self._on_sync)
        btn_layout.addWidget(self.btn_sync)

        self.btn_home_all = QPushButton("全部回零")
        self.btn_home_all.setStyleSheet("QPushButton { background-color: #8b5cf6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                                         "QPushButton:hover { background-color: #7c3aed; }")
        self.btn_home_all.clicked.connect(self._on_home_all)
        btn_layout.addWidget(self.btn_home_all)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setStyleSheet("QPushButton { background-color: #ef4444; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                                     "QPushButton:hover { background-color: #dc2626; }")
        self.btn_stop.clicked.connect(self._on_stop)
        btn_layout.addWidget(self.btn_stop)

        group_layout.addLayout(btn_layout)
        layout.addWidget(group)

    def set_motor_ids(self, motor_ids):
        self._motor_ids = sorted(motor_ids)
        self._clear_inputs()
        self._build_inputs()

    def _clear_inputs(self):
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._inputs.clear()

    def _build_inputs(self):
        for idx, mid in enumerate(self._motor_ids):
            row = idx // 2
            col = (idx % 2) * 3

            label = QLabel(f"关节{mid}:")
            label.setStyleSheet("font-weight: bold;")
            self._grid_layout.addWidget(label, row, col)

            inp = QLineEdit("0")
            inp.setFixedWidth(60)
            self._grid_layout.addWidget(inp, row, col + 1)
            self._inputs[mid] = inp

            unit = QLabel("°")
            self._grid_layout.addWidget(unit, row, col + 2)

    def _on_sync(self):
        try:
            speed = int(self.speed_input.text().strip() or "100")
        except ValueError:
            speed = 100

        targets = {}
        for mid, inp in self._inputs.items():
            try:
                angle = float(inp.text().strip() or "0")
                targets[mid] = angle
            except ValueError:
                targets[mid] = 0.0

        try:
            self.motor_manager.sync_move_positions(targets, speed=speed)
            self.log_message.emit("info", f"同步运动: {targets}")
        except Exception as e:
            self.log_message.emit("error", f"同步运动失败: {e}")

    def _on_home_all(self):
        reply = QMessageBox.question(self, "全部回零", "确认所有关节回到零点？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.motor_manager.go_to_custom_zero(speed=100)
            self.log_message.emit("info", "全部回零: 同步运动到零点位置")
        except Exception as e:
            self.log_message.emit("error", f"全部回零失败: {e}")

    def _on_stop(self):
        reply = QMessageBox.question(self, "停止", "确认停止所有关节运动？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.motor_manager.stop_all()
            self.log_message.emit("info", "已停止所有关节")
        except Exception as e:
            self.log_message.emit("error", f"停止失败: {e}")

    def clear(self):
        self._clear_inputs()
        self._motor_ids.clear()
