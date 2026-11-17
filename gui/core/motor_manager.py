import sys
import os
import json
import time
import logging
from typing import Dict, List, Optional, Any

from PyQt5.QtCore import QThread, pyqtSignal, QMutex

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Embodied_SDK import create_motor_controller, setup_logging, close_all_shared_interfaces
from gui.core.config_loader import get_reducer_ratios, get_directions

logger = logging.getLogger(__name__)

_CUSTOM_ZERO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "custom_zero.json"
)


class StatusWorker(QThread):
    status_ready = pyqtSignal(dict)

    def __init__(self, motor_manager, parent=None):
        super().__init__(parent)
        self.motor_manager = motor_manager
        self._running = True
        self._interval = 0.5
        self._mutex = QMutex()

    def run(self):
        while self._running:
            if self.motor_manager.is_connected:
                try:
                    statuses = self.motor_manager.get_all_status()
                    self.status_ready.emit(statuses)
                except Exception:
                    pass
            time.sleep(self._interval)

    def stop(self):
        self._mutex.lock()
        self._running = False
        self._mutex.unlock()
        self.wait(2000)


class MotorManager:
    def __init__(self):
        self.motors: Dict[int, Any] = {}
        self.broadcast_controller: Optional[Any] = None
        self.connected_motor_ids: List[int] = []
        self.interface_params: Dict[str, Any] = {}
        self._ratios: Dict[int, float] = {}
        self._dirs: Dict[int, int] = {}
        self._connected = False
        self._custom_zero_motor: Dict[int, float] = {}

    @property
    def is_connected(self):
        return self._connected and len(self.connected_motor_ids) > 0

    @property
    def custom_zero(self) -> Dict[int, float]:
        """暴露零点偏移给外部（如视觉 SDK wrapper）"""
        return dict(self._custom_zero_motor)

    def connect_motors(self, port: str, motor_ids: List[int], baudrate: int = 500000) -> Dict[str, Any]:
        result = {"success": [], "failed": [], "errors": []}

        self.interface_params = {
            "interface_type": "slcan",
            "port": port,
            "baudrate": baudrate,
            "shared_interface": True,
        }

        all_ratios = get_reducer_ratios()
        all_dirs = get_directions()
        logger.info(f"all_ratios:{all_ratios}")
        logger.info(f"all_dirs:{all_ratios}")
        for motor_id in motor_ids:
            try:
                motor = create_motor_controller(motor_id=motor_id, **self.interface_params)
                motor.connect()
                self.motors[motor_id] = motor
                self.connected_motor_ids.append(motor_id)
                self._ratios[motor_id] = all_ratios.get(motor_id, 1.0)
                self._dirs[motor_id] = all_dirs.get(motor_id, 1)
                result["success"].append(motor_id)
                logger.info(f"关节 {motor_id} 连接成功")
            except Exception as e:
                result["failed"].append(motor_id)
                result["errors"].append(f"关节 {motor_id}: {e}")
                logger.error(f"关节 {motor_id} 连接失败: {e}")

        if result["success"]:
            try:
                self.broadcast_controller = create_motor_controller(motor_id=0, **self.interface_params)
                if self.broadcast_controller.can_interface is None:
                    self.broadcast_controller.connect()
                logger.info("广播控制器创建成功")
            except Exception as e:
                logger.warning(f"广播控制器创建失败: {e}")

        self._connected = len(result["success"]) > 0
        if self._connected:
            self._load_custom_zero()
        return result

    def disconnect_all(self):
        for motor_id in list(self.connected_motor_ids):
            try:
                self.motors[motor_id].disconnect()
            except Exception:
                pass

        if self.broadcast_controller:
            try:
                self.broadcast_controller.disconnect()
            except Exception:
                pass
            self.broadcast_controller = None

        try:
            close_all_shared_interfaces()
        except Exception:
            pass

        self.motors.clear()
        self.connected_motor_ids.clear()
        self._ratios.clear()
        self._dirs.clear()
        self.interface_params.clear()
        self._connected = False
        logger.info("所有关节已断开")

    def enable_all(self):
        results = {}
        for motor_id in self.connected_motor_ids:
            try:
                self.motors[motor_id].control_actions.enable()
                results[motor_id] = True
            except Exception as e:
                results[motor_id] = str(e)
        return results

    def disable_all(self):
        results = {}
        for motor_id in self.connected_motor_ids:
            try:
                self.motors[motor_id].control_actions.disable()
                results[motor_id] = True
            except Exception as e:
                results[motor_id] = str(e)
        return results

    def stop_all(self):
        results = {}
        for motor_id in self.connected_motor_ids:
            try:
                self.motors[motor_id].control_actions.stop()
                results[motor_id] = True
            except Exception as e:
                results[motor_id] = str(e)
        return results

    # ---- 零点管理 ----

    def set_custom_zero(self):
        """读取当前所有电机编码器角度，存储为零点偏移并持久化"""
        self._custom_zero_motor.clear()
        for motor_id in self.connected_motor_ids:
            try:
                pos_motor = self.motors[motor_id].read_parameters.get_position()
                self._custom_zero_motor[motor_id] = pos_motor
            except Exception as e:
                logger.warning(f"读取关节 {motor_id} 位置失败: {e}")
        if self._custom_zero_motor:
            self._save_custom_zero()
            logger.info(f"零点已存储（电机端编码器值）: {self._custom_zero_motor}")

    def _save_custom_zero(self):
        try:
            os.makedirs(os.path.dirname(_CUSTOM_ZERO_PATH), exist_ok=True)
            data = {str(k): v for k, v in self._custom_zero_motor.items()}
            with open(_CUSTOM_ZERO_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"保存零点失败: {e}")

    def _load_custom_zero(self):
        try:
            if os.path.exists(_CUSTOM_ZERO_PATH):
                with open(_CUSTOM_ZERO_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._custom_zero_motor = {int(k): float(v) for k, v in data.items()}
                logger.info(f"已加载零点: {self._custom_zero_motor}")
        except Exception as e:
            logger.warning(f"加载零点失败: {e}")

    @property
    def has_custom_zero(self) -> bool:
        return len(self._custom_zero_motor) > 0

    def go_to_custom_zero(self, speed: int = 100, extra_offsets: Optional[Dict[int, float]] = None):
        """
        同步运动到零点位置。
        extra_offsets: 可选的额外偏移（关节角度），如 {5: 90.0} 表示关节5在零点基础上再加90°
        """
        targets = {mid: 0.0 for mid in self.connected_motor_ids}
        if extra_offsets:
            for mid, offset in extra_offsets.items():
                targets[mid] = targets.get(mid, 0.0) + offset
        self.sync_move_positions(targets, speed=speed)

    def go_to_custom_zero_sequential(self, speed: int = 100, last_joint: int = 5,
                                      extra_offsets: Optional[Dict[int, float]] = None,
                                      delay: float = 3.0):
        """
        顺序归位：先动其他关节，延迟一小段时间后动 last_joint。
        避免减速比较小的关节（如关节5）先到位导致夹爪刮蹭。

        Args:
            speed: 运动速度
            last_joint: 最后运动的关节ID（默认5）
            extra_offsets: 额外偏移
            delay: 其他关节开始运动后，等待多少秒再动 last_joint（默认3秒）
        """
        import threading

        targets = {mid: 0.0 for mid in self.connected_motor_ids}
        if extra_offsets:
            for mid, offset in extra_offsets.items():
                targets[mid] = targets.get(mid, 0.0) + offset

        final_last = targets.get(last_joint, 0.0)
        other_targets = {mid: t for mid, t in targets.items() if mid != last_joint}

        def _run():
            import time

            # 第一步：先动其他关节
            if other_targets:
                logger.info(f"顺序归位 ① 其他关节先动: {other_targets}")
                self.sync_move_positions(other_targets, speed=speed)

            # 第二步：延迟后动 last_joint
            time.sleep(delay)
            logger.info(f"顺序归位 ② 关节{last_joint}跟上: {final_last}°")
            self.sync_move_positions({last_joint: final_last}, speed=speed)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    # ---- 电机控制 ----

    def enable_motor(self, motor_id: int):
        if motor_id in self.motors:
            self.motors[motor_id].control_actions.enable()

    def disable_motor(self, motor_id: int):
        if motor_id in self.motors:
            self.motors[motor_id].control_actions.disable()

    def move_position(self, motor_id: int, joint_angle: float, speed: int = 100, is_absolute: bool = True):
        if motor_id not in self.motors:
            raise ValueError(f"关节 {motor_id} 未连接")
        motor_deg = self._joint_deg_to_motor_deg(motor_id, joint_angle, relative=not is_absolute)
        self.motors[motor_id].control_actions.move_to_position(
            position=motor_deg, speed=speed, is_absolute=is_absolute
        )
        logger.info(f"关节 {motor_id}: 目标 {joint_angle:.2f}° (电机端 {motor_deg:.2f}°)")

    def sync_move_positions(self, targets: Dict[int, float], speed: int = 100, is_absolute: bool = True):
        if not self.broadcast_controller:
            raise RuntimeError("广播控制器未创建")

        for motor_id, joint_angle in targets.items():
            if motor_id not in self.motors:
                raise ValueError(f"电机 {motor_id} 未连接")
            motor_deg = self._joint_deg_to_motor_deg(motor_id, joint_angle)
            self.motors[motor_id].control_actions.move_to_position(
                position=motor_deg, speed=speed, is_absolute=is_absolute, multi_sync=True
            )

        self.broadcast_controller.control_actions.sync_motion()
        logger.info(f"同步运动: {targets}")

    def get_all_status(self) -> Dict[int, Dict[str, Any]]:
        statuses = {}
        for motor_id in self.connected_motor_ids:
            try:
                motor = self.motors[motor_id]
                pos_motor = motor.read_parameters.get_position()
                pos_joint = self._motor_deg_to_joint_deg(motor_id, pos_motor)
                speed = motor.read_parameters.get_speed()
                temp = motor.read_parameters.get_temperature()
                status = motor.read_parameters.get_motor_status()
                voltage = motor.read_parameters.get_bus_voltage()
                current = motor.read_parameters.get_bus_current()

                statuses[motor_id] = {
                    "enabled": status.enabled,
                    "position_motor": pos_motor,
                    "position_joint": pos_joint,
                    "speed": speed,
                    "temperature": temp,
                    "voltage": voltage,
                    "current": current,
                    "error": None,
                }
            except Exception as e:
                statuses[motor_id] = {
                    "enabled": False,
                    "position_motor": 0,
                    "position_joint": 0,
                    "speed": 0,
                    "temperature": 0,
                    "voltage": 0,
                    "current": 0,
                    "error": str(e),
                }
        return statuses

    def _motor_deg_to_joint_deg(self, motor_id: int, motor_deg: float) -> float:
        ratio = self._ratios.get(motor_id, 1.0)
        direction = self._dirs.get(motor_id, 1)
        zero_offset = self._custom_zero_motor.get(motor_id, 0.0)
        return (motor_deg - zero_offset) / direction / ratio

    def _joint_deg_to_motor_deg(self, motor_id: int, joint_deg: float, relative: bool = False) -> float:
        ratio = self._ratios.get(motor_id, 1.0)
        direction = self._dirs.get(motor_id, 1)
        if relative:
            return joint_deg * direction * ratio
        zero_offset = self._custom_zero_motor.get(motor_id, 0.0)
        return joint_deg * direction * ratio + zero_offset
