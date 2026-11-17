from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGroupBox, QMessageBox,
    QComboBox, QCheckBox, QFrame, QApplication
)
from PyQt5.QtCore import pyqtSignal, Qt
from can import logger

from gui.core.motor_manager import MotorManager
from gui.widgets.motor_status_table import MotorStatusTable
from gui.widgets.position_control import PositionControl
from gui.widgets.sync_control import SyncControl
from gui.widgets.claw_control import ClawControl
import logging
logger = logging.getLogger(__name__)


class MotorPanel(QWidget):
    log_message = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)

    def __init__(self, motor_manager: MotorManager, parent=None):
        super().__init__(parent)
        self.motor_manager = motor_manager
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 连接栏
        layout.addWidget(self._build_connection_bar())

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # 关节状态监控
        self.status_table = MotorStatusTable(self.motor_manager) 
        layout.addWidget(self.status_table)

        control_layout = QHBoxLayout()
        #位置微调（关节端角度）
        self.position_control = PositionControl(self.motor_manager)
        self.position_control.log_message.connect(self.log_message)
        self.position_control.btn_refresh.clicked.connect(self._on_refresh_status)
        control_layout.addWidget(self.position_control, stretch=3)

        right_panel = QVBoxLayout()

        #批量操作
        batch_group = QGroupBox("批量操作")
        batch_layout = QVBoxLayout()
        self.btn_enable_all = QPushButton("全部使能")
        self.btn_enable_all.setStyleSheet("QPushButton { background-color: #22c55e; color: white; font-weight: bold; padding: 8px; border-radius: 4px; }"
                                          "QPushButton:hover { background-color: #16a34a; }")
        self.btn_enable_all.clicked.connect(self._on_enable_all)
        batch_layout.addWidget(self.btn_enable_all)

        self.btn_disable_all = QPushButton("全部失能")
        self.btn_disable_all.setStyleSheet("QPushButton { background-color: #f59e0b; color: white; font-weight: bold; padding: 8px; border-radius: 4px; }"
                                           "QPushButton:hover { background-color: #d97706; }")
        self.btn_disable_all.clicked.connect(self._on_disable_all)
        batch_layout.addWidget(self.btn_disable_all)
        batch_group.setLayout(batch_layout)
        right_panel.addWidget(batch_group)
        #多机同步控制
        self.sync_control = SyncControl(self.motor_manager)
        self.sync_control.log_message.connect(self.log_message)
        right_panel.addWidget(self.sync_control)
        #夹爪控制
        self.claw_control = ClawControl()
        self.claw_control.log_message.connect(self.log_message)
        right_panel.addWidget(self.claw_control)

        right_panel.addStretch()
        control_layout.addLayout(right_panel, stretch=2)

        layout.addLayout(control_layout)

        self.status_table._worker_ref = None

    def _build_connection_bar(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)

        layout.addWidget(QLabel("端口:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems([ "COM3", "COM4", "COM5","COM6", "COM7", "COM8"])
        self.port_combo.setFixedWidth(100)
        layout.addWidget(self.port_combo)

        layout.addWidget(QLabel("关节:"))
        self.motor_checks = {}
        for mid in range(1, 7):
            cb = QCheckBox(str(mid))
            cb.setChecked(True)
            self.motor_checks[mid] = cb
            layout.addWidget(cb)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setStyleSheet("QPushButton { background-color: #3b82f6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                                       "QPushButton:hover { background-color: #2563eb; }")
        self.btn_connect.clicked.connect(self._on_connect)
        layout.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setStyleSheet("QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                                          "QPushButton:hover { background-color: #4b5563; }")
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_disconnect.setEnabled(False)
        layout.addWidget(self.btn_disconnect)

        layout.addStretch()

        self.btn_emergency = QPushButton("紧急停止 (空格)")
        self.btn_emergency.setStyleSheet("QPushButton { background-color: #ef4444; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; font-size: 14px; }"
                                         "QPushButton:hover { background-color: #dc2626; }")
        self.btn_emergency.clicked.connect(self._on_emergency_stop)
        layout.addWidget(self.btn_emergency)

        return bar

    def _on_connect(self):
        port = self.port_combo.currentText().strip()
        motor_ids = [mid for mid, cb in self.motor_checks.items() if cb.isChecked()]

        if not port:
            QMessageBox.warning(self, "提示", "请输入串口号")
            return
        if not motor_ids:
            QMessageBox.warning(self, "提示", "请至少选择一个关节")
            return

        self.log_message.emit("info", f"正在连接: 端口 {port}, 关节 {motor_ids}")
        self.btn_connect.setEnabled(False)
        QApplication.processEvents()

        result = self.motor_manager.connect_motors(port, motor_ids)

        if result["success"]:
            self.set_motor_ids(result["success"])
            #self.start_monitoring()  #关节状态监控
            self.log_message.emit("info", f"连接成功: 关节 {result['success']}")
            self.status_changed.emit(f"已连接 {len(result['success'])} 个关节: {result['success']}")
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            logger.info(f"self.motor_checks.values:{self.motor_checks.values()}")
            for cb in self.motor_checks.values():
                cb.setEnabled(False)
            self.port_combo.setEnabled(False)

        if result["failed"]:
            self.log_message.emit("error", f"连接失败: {result['errors']}")
            if not result["success"]:
                self.btn_connect.setEnabled(True)

    def _on_disconnect(self):
        reply = QMessageBox.question(self, "断开连接", "确认断开所有关节连接？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.stop_monitoring()
        self.clear()
        self.motor_manager.disconnect_all()
        self.log_message.emit("info", "所有关节已断开")
        self.status_changed.emit("未连接")
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        for cb in self.motor_checks.values():
            cb.setEnabled(True)
        self.port_combo.setEnabled(True)

    def _on_emergency_stop(self):
        if self.motor_manager.is_connected:
            self.motor_manager.stop_all()
            self.log_message.emit("warn", "紧急停止: 所有关节已停止")

    def set_motor_ids(self, motor_ids):
        self.status_table.set_motor_ids(motor_ids)
        self.position_control.set_motor_ids(motor_ids)
        self.sync_control.set_motor_ids(motor_ids)

    def start_monitoring(self):
        self.status_table.start_refresh(500)
        if self.status_table._worker:
            self.status_table._worker.status_ready.connect(self.position_control.update_positions)

    def stop_monitoring(self):
        self.status_table.stop_refresh()

    def _on_refresh_status(self):
        if not self.motor_manager.is_connected:
            return
        try:
            statuses = self.motor_manager.get_all_status()
            self.position_control.update_positions(statuses)
            self.log_message.emit("info", "状态已刷新")
        except Exception as e:
            self.log_message.emit("error", f"刷新失败: {e}")

    def _on_enable_all(self):
        reply = QMessageBox.question(self, "全部使能", "确认使能所有关节？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        results = self.motor_manager.enable_all()
        successes = [f"关节{mid}" for mid, ok in results.items() if ok is True]
        fails = [f"关节{mid}" for mid, ok in results.items() if ok is not True]
        if successes:
            self.log_message.emit("info", f"使能成功: {', '.join(successes)}")
        if fails:
            self.log_message.emit("error", f"使能失败: {', '.join(fails)}")

    def _on_disable_all(self):
        reply = QMessageBox.question(self, "全部失能", "确认失能所有关节？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        results = self.motor_manager.disable_all()
        successes = [f"关节{mid}" for mid, ok in results.items() if ok is True]
        fails = [f"关节{mid}" for mid, ok in results.items() if ok is not True]
        if successes:
            self.log_message.emit("info", f"失能成功: {', '.join(successes)}")
        if fails:
            self.log_message.emit("error", f"失能失败: {', '.join(fails)}")

    def clear(self):
        self.status_table.clear()
        self.position_control.clear()
        self.sync_control.clear()
        self.claw_control.clear()
