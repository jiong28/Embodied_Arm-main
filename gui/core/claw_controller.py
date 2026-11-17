import threading
import time

import serial

#important
class ClawController:
    """夹爪舵机控制器 — 通过串口与 ESP32 (claw.ino) 通信"""

    def __init__(self, port: str = "COM6", baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._lock = threading.Lock()

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
    #important
    def set_angle(self, angle: float) -> bool:
        """
        设置夹爪角度 (用户视角: 0=完全闭合, 90=完全张开)
        内部反转: 舵机 90°=闭合, 0°=张开
        """
        angle = max(0.0, min(90.0, angle))
        servo_angle = 90 - int(angle)
        with self._lock:
            if not self.is_connected:
                return False
            try:
                self._serial.write(f"{servo_angle}\n".encode())
                return True
            except serial.SerialException:
                return False

    def open_claw(self, angle: float = 90.0) -> bool:
        return self.set_angle(angle)

    def close_claw(self, angle: float = 0.0) -> bool:
        return self.set_angle(angle)
