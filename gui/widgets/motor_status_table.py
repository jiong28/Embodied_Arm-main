from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from gui.core.motor_manager import MotorManager, StatusWorker

COLUMNS = ["ID", "使能", "位置(°)", "转速(RPM)", "温度(℃)", "电压(V)", "电流(A)"]


class MotorStatusTable(QWidget):
    def __init__(self, motor_manager: MotorManager, parent=None):
        super().__init__(parent)
        self.motor_manager = motor_manager
        self._motor_ids = []
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("关节状态监控")
        header.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(header)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table)

    def set_motor_ids(self, motor_ids):
        self._motor_ids = sorted(motor_ids)
        self.table.setRowCount(len(self._motor_ids))
        for row, mid in enumerate(self._motor_ids):
            id_item = QTableWidgetItem(f"关节{mid}")
            id_item.setTextAlignment(Qt.AlignCenter)
            font = id_item.font()
            font.setBold(True)
            id_item.setFont(font)
            self.table.setItem(row, 0, id_item)
            for col in range(1, len(COLUMNS)):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

    def start_refresh(self, interval_ms=500):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
        self._worker = StatusWorker(self.motor_manager)
        self._worker.status_ready.connect(self._update_table)
        self._worker.start()

    def stop_refresh(self):
        if self._worker:
            self._worker.stop()
            self._worker = None

    def _update_table(self, statuses):
        for row, mid in enumerate(self._motor_ids):
            if mid not in statuses:
                continue
            s = statuses[mid]

            if s["error"]:
                for col in range(1, len(COLUMNS)):
                    item = self.table.item(row, col)
                    if item:
                        item.setText("错误")
                        item.setForeground(QColor("#dc2626"))
                continue

            enabled_item = self.table.item(row, 1)
            if enabled_item:
                enabled_item.setText("✓" if s["enabled"] else "✗")
                enabled_item.setForeground(QColor("#16a34a") if s["enabled"] else QColor("#9ca3af"))

            pos_item = self.table.item(row, 2)
            if pos_item:
                pos_item.setText(f"{s['position_joint']:.1f}")

            speed_item = self.table.item(row, 3)
            if speed_item:
                speed_item.setText(f"{s['speed']:.1f}")

            temp_item = self.table.item(row, 4)
            if temp_item:
                temp_item.setText(f"{s['temperature']:.1f}")

            voltage_item = self.table.item(row, 5)
            if voltage_item:
                voltage_item.setText(f"{s['voltage']:.1f}")

            current_item = self.table.item(row, 6)
            if current_item:
                current_item.setText(f"{s['current']:.2f}")

            for col in range(1, len(COLUMNS)):
                item = self.table.item(row, col)
                if item:
                    item.setForeground(QColor("#333333"))

    def clear(self):
        self.stop_refresh()
        self.table.setRowCount(0)
        self._motor_ids.clear()
