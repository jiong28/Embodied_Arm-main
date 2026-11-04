import time
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, QGridLayout, QMessageBox, QButtonGroup
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread

from gui.core.motor_manager import MotorManager

STEPS = [0.1, 0.5, 1.0, 2.0, 5.0]


class PositionControl(QWidget):
    log_message = pyqtSignal(str, str)

    def __init__(self, motor_manager: MotorManager, parent=None):
        super().__init__(parent)
        self.motor_manager = motor_manager
        self._motor_ids = []
        self._angle_labels = {}
        self._btn_minus = {}
        self._btn_plus = {}
        self._step = 1.0
        self._init_ui()

    def _init_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("位置微调 (关节端角度)")
        self.group_layout = QGridLayout()
        group.setLayout(self.group_layout)
        self.layout.addWidget(group)

        # 步进选择 + 功能按钮行
        self.btn_layout_widget = QWidget()
        self._extra_btn_layout = QHBoxLayout(self.btn_layout_widget)
        self._extra_btn_layout.setContentsMargins(0, 4, 0, 0)

        self._extra_btn_layout.addWidget(QLabel("步进:"))
        self._step_group = QButtonGroup(self)
        self._step_group.setExclusive(True)
        self._step_buttons = {}
        for step in STEPS:
            btn = QPushButton(f"{step:g}°")
            btn.setCheckable(True)
            btn.setFixedWidth(48)
            btn.setStyleSheet(
                "QPushButton { padding: 3px; border-radius: 3px; border: 1px solid #ccc; }"
                "QPushButton:checked { background-color: #3b82f6; color: white; border-color: #3b82f6; }"
            )
            btn.clicked.connect(lambda checked, s=step: self._on_step_changed(s))
            self._step_group.addButton(btn)
            self._step_buttons[step] = btn
            self._extra_btn_layout.addWidget(btn)
        self._step_buttons[1.0].setChecked(True)

        self._extra_btn_layout.addSpacing(16)

        self.btn_refresh = QPushButton("刷新状态")
        self.btn_refresh.setStyleSheet(
            "QPushButton { background-color: #0ea5e9; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #0284c7; }"
        )
        self._extra_btn_layout.addWidget(self.btn_refresh)

        self.btn_set_zero = QPushButton("设置零点")
        self.btn_set_zero.setStyleSheet(
            "QPushButton { background-color: #8b5cf6; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #7c3aed; }"
        )
        self.btn_set_zero.clicked.connect(self._on_set_zero)
        self._extra_btn_layout.addWidget(self.btn_set_zero)

        self._extra_btn_layout.addStretch()
        self.layout.addWidget(self.btn_layout_widget)

    def _on_step_changed(self, step):
        self._step = step
        for mid in self._motor_ids:
            if mid in self._btn_minus:
                self._btn_minus[mid].setText(f"-{step:g}°")
                self._btn_plus[mid].setText(f"+{step:g}°")

    def set_motor_ids(self, motor_ids):
        self._motor_ids = sorted(motor_ids)
        self._clear_controls()
        self._build_controls()

    def _clear_controls(self):
        while self.group_layout.count():
            item = self.group_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._angle_labels.clear()
        self._btn_minus.clear()
        self._btn_plus.clear()

    def _build_controls(self):
        for row, mid in enumerate(self._motor_ids):
            label = QLabel(f"关节{mid}:")
            label.setStyleSheet("font-weight: bold;")
            self.group_layout.addWidget(label, row, 0)

            btn_minus = QPushButton(f"-{self._step:g}°")
            btn_minus.setFixedWidth(50)
            btn_minus.clicked.connect(lambda checked, m=mid: self._on_adjust(m, -self._step))
            self.group_layout.addWidget(btn_minus, row, 1)
            self._btn_minus[mid] = btn_minus

            angle_label = QLabel("-- °")
            angle_label.setFixedWidth(80)
            angle_label.setAlignment(Qt.AlignCenter)
            angle_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 2px; border: 1px solid #ccc; border-radius: 3px;")
            self.group_layout.addWidget(angle_label, row, 2)
            self._angle_labels[mid] = angle_label

            btn_plus = QPushButton(f"+{self._step:g}°")
            btn_plus.setFixedWidth(50)
            btn_plus.clicked.connect(lambda checked, m=mid: self._on_adjust(m, self._step))
            self.group_layout.addWidget(btn_plus, row, 3)
            self._btn_plus[mid] = btn_plus

    def _on_adjust(self, motor_id, delta):
        try:
            self.motor_manager.move_position(motor_id, delta, is_absolute=False)
        except Exception as e:
            self.log_message.emit("error", f"关节{motor_id} 运动失败: {e}")

    def _on_set_zero(self):
        reply = QMessageBox.question(self, "设置零点",
                                     "确认将所有关节当前位置设为零点？\n\n"
                                     "此操作会记录当前各电机编码器值作为零点偏移，\n"
                                     "之后关节角度将相对于当前位置显示和控制。",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.motor_manager.set_custom_zero()
            self.log_message.emit("info", "零点已设置（软件层面，当前关节位置=0°）")
        except Exception as e:
            self.log_message.emit("error", f"设置零点失败: {e}")

    def update_positions(self, statuses):
        for mid in self._motor_ids:
            if mid in statuses and mid in self._angle_labels:
                s = statuses[mid]
                if s["error"] is None:
                    self._angle_labels[mid].setText(f"{s['position_joint']:.1f} °")
                else:
                    self._angle_labels[mid].setText("错误")

    def clear(self):
        self._clear_controls()
        self._motor_ids.clear()
