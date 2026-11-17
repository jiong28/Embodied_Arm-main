from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QMessageBox, QAction, QApplication,
    QShortcut, QFrame, QTabWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence

from gui.core.motor_manager import MotorManager
from gui.core.crash_handler import register_cleanup
from gui.motor_panel import MotorPanel
from gui.widgets.log_widget import LogWidget
from gui.widgets.camera_panel import CameraPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.motor_manager = MotorManager()
        self.setWindowTitle("HorizonArm 上位机")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        self._init_ui()
        self._init_menu()
        self._init_shortcuts()
        self._init_crash_handler()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.motor_panel = MotorPanel(self.motor_manager)
        self.motor_panel.log_message.connect(self._on_panel_log)
        self.motor_panel.status_changed.connect(self.statusBar().showMessage)

        self.camera_panel = CameraPanel(self.motor_manager)
        self.camera_panel.log_message.connect(self._on_panel_log)
        self.camera_panel.set_claw_controller(self.motor_panel.claw_control.claw)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.motor_panel, "⚙ 机械臂控制")
        self.tabs.addTab(self.camera_panel, "\U0001f4f7  视觉抓取 + 具身智能")
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #d1d5db; border-radius: 4px; }
            QTabBar::tab {
                padding: 10px 28px;
                font-size: 14px;
                font-weight: bold;
                background: #e5e7eb;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #3b82f6;
                color: white;
                border-color: #3b82f6;
            }
            QTabBar::tab:hover:!selected {
                background: #bfdbfe;
            }
        """)
        main_layout.addWidget(self.tabs, stretch=1)

        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(separator2)
        #操作日志
        self.log_widget = LogWidget()
        main_layout.addWidget(self.log_widget, stretch=0)

        self.statusBar().showMessage("未连接")

    def _init_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")
        exit_action = QAction("退出", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)


        help_menu = menubar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _init_shortcuts(self):
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.activated.connect(self._on_global_emergency_stop)

    def _on_global_emergency_stop(self):
        """全局空格紧急停止：根据当前活跃标签页调用对应的停止方法"""
        current_widget = self.tabs.currentWidget()
        if current_widget == self.camera_panel:
            self.camera_panel._on_emergency_stop()
        else:
            self.motor_panel._on_emergency_stop()

    def _init_crash_handler(self):
        register_cleanup(self._cleanup)
        QApplication.instance().aboutToQuit.connect(self._cleanup)

    def _on_panel_log(self, level, msg):
        if level == "error":
            self.log_widget.log_error(msg)
        elif level == "warn":
            self.log_widget.log_warn(msg)
        else:
            self.log_widget.log_info(msg)


    def _show_about(self):
        QMessageBox.about(self, "关于", "HorizonArm 上位机 v1.0\n\n基于 PyQt5 + HorizonArm SDK\n支持6轴机械臂控制")

    def _cleanup(self):
        try:
            self.camera_panel.clear()
            self.motor_panel.stop_monitoring()
            self.motor_panel.claw_control.clear()
            self.motor_manager.disconnect_all()
            self.log_widget.close_log()
        except Exception:
            pass

    def closeEvent(self, event):
        if self.motor_manager.is_connected:
            reply = QMessageBox.question(
                self, "确认退出",
                "关节仍在连接中，退出将断开所有连接。确定退出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

        self._cleanup()
        event.accept()
