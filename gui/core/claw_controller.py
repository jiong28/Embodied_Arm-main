import json
import os
import threading
import time

import serial

# 配置文件路径：<项目根>/config/claw_config.json
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "claw_config.json",
)


def _load_config() -> dict:
    """读取夹爪配置（失败返回空字典，不影响主流程）"""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class ClawController:
    """夹爪舵机控制器 — 通过串口与 ESP32 (claw.ino) 通信

    全项目统一角度约定（用户视角）：
        0° = 完全闭合，90° = 完全张开

    实际舵机方向由 config/claw_config.json 的 servo_invert 决定：
        servo_invert = false (默认) → 舵机 0°=闭合, 90°=张开
        servo_invert = true          → 舵机 0°=张开, 90°=闭合
    若现场发现张开/闭合反了，只需改这一个字段，无需改代码。
    """

    def __init__(self, port: str = "COM6", baudrate: int = 9600, servo_invert: bool = None):
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._lock = threading.Lock()
        # 最近一次实际发送给舵机的原始角度，便于诊断
        self.last_servo_angle: int | None = None
        self._servo_invert = False if servo_invert is None else bool(servo_invert)
        if servo_invert is None:
            self.reload_config()

    # ---------- 配置 ----------

    def reload_config(self) -> bool:
        """重新读取方向配置，返回当前 servo_invert"""
        cfg = _load_config()
        self._servo_invert = bool(cfg.get("servo_invert", False))
        return self._servo_invert

    @property
    def servo_invert(self) -> bool:
        return self._servo_invert

    @servo_invert.setter
    def servo_invert(self, value: bool):
        self._servo_invert = bool(value)

    def direction_desc(self) -> str:
        if self._servo_invert:
            return "舵机 0°=张开, 90°=闭合（servo_invert=true）"
        return "舵机 0°=闭合, 90°=张开（servo_invert=false）"

    # ---------- 连接 ----------

    def connect(self) -> bool:
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(2)  # 等待 ESP32 重启
            return True
        except serial.SerialException:
            return False

    def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ---------- 角度控制 ----------

    def _to_servo_angle(self, angle: float) -> int:
        """用户角度(0=闭合,90=张开) → 舵机原始角度"""
        angle = max(0.0, min(90.0, float(angle)))
        servo = (90.0 - angle) if self._servo_invert else angle
        return max(0, min(180, int(round(servo))))

    def set_angle(self, angle: float) -> bool:
        """
        设置夹爪角度 (用户视角: 0=完全闭合, 90=完全张开)
        根据 servo_invert 自动换算为舵机角度后下发。
        """
        return self._write_servo(self._to_servo_angle(angle))

    def raw_set_servo(self, servo_angle: float) -> bool:
        """直接下发舵机原始角度（0~180），仅用于方向自检，业务代码不要用"""
        return self._write_servo(max(0, min(180, int(round(float(servo_angle))))))

    def _write_servo(self, servo_angle: int) -> bool:
        with self._lock:
            if not self.is_connected:
                return False
            try:
                self._serial.write(f"{servo_angle}\n".encode())
                self.last_servo_angle = servo_angle
                return True
            except serial.SerialException:
                return False

    # ---------- 语义化封装 ----------

    def open_claw(self, angle: float = 90.0) -> bool:
        """张开夹爪（默认全开）"""
        return self.set_angle(angle)

    def close_claw(self, angle: float = 0.0) -> bool:
        """闭合夹爪（默认全闭）"""
        return self.set_angle(angle)
