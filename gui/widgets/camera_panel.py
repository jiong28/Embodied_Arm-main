import os
import json
import base64
import tempfile
from typing import Optional

import cv2
import numpy as np
import requests
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QMessageBox,
    QGroupBox, QGridLayout, QFrame, QLineEdit, QScrollArea, QSpinBox, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint, QThread
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont

from gui.core.vision_manager import VisionManager, LLMDetectWorker
from Embodied_SDK.visual_grasp import open_capture


_BOX_COLORS = [
    QColor(255, 87, 34),
    QColor(33, 150, 243),
    QColor(76, 175, 80),
    QColor(156, 39, 176),
    QColor(255, 193, 7),
    QColor(0, 188, 212),
]

# 拍照位姿态已改为示教式（见 motor_manager.record_photo_position / go_to_photo_position）：
# 手动摆好姿态后点「记录拍照位」，全部关节角保存到 config/photo_position.json；
# 未记录时回退为「全 0 + j5_deg」。


class ClickableLabel(QLabel):
    """支持鼠标点击的 QLabel，点击只记录坐标不触发运动"""
    clicked = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._crosshair_pos: QPoint | None = None
        self._detection_boxes: list = []
        self._det_frame_w: int = 0
        self._det_frame_h: int = 0
        self.setMouseTracking(True)

    def set_crosshair(self, pos: QPoint | None):
        self._crosshair_pos = pos
        self.update()

    def set_detection_boxes(self, boxes: list, frame_w: int = 0, frame_h: int = 0):
        self._detection_boxes = boxes
        self._det_frame_w = frame_w
        self._det_frame_h = frame_h
        self.update()

    def clear_detection_boxes(self):
        self._detection_boxes = []
        self._det_frame_w = 0
        self._det_frame_h = 0
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._crosshair_pos = event.pos()
            self.update()
            self.clicked.emit(event.x(), event.y())

# important Qt的事件回调，首次显示、被遮挡后露出、缩放窗口、更新时会回调
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if self._crosshair_pos is not None:
            pen = QPen(QColor(255, 0, 0), 2)
            painter.setPen(pen)
            x, y = self._crosshair_pos.x(), self._crosshair_pos.y()
            painter.drawLine(x - 15, y, x + 15, y)
            painter.drawLine(x, y - 15, x, y + 15)
        if self._detection_boxes and self._det_frame_w > 0 and self._det_frame_h > 0:
            pixmap = self.pixmap()
            if pixmap is not None and not pixmap.isNull():
                pix_w = pixmap.width()
                pix_h = pixmap.height()
                lbl_w = self.width()
                lbl_h = self.height()
                off_x = (lbl_w - pix_w) / 2
                off_y = (lbl_h - pix_h) / 2
                sx = pix_w / self._det_frame_w
                sy = pix_h / self._det_frame_h
                for box in self._detection_boxes:
                    x1, y1, x2, y2 = box["bbox"]
                    lx1 = x1 * sx + off_x
                    ly1 = y1 * sy + off_y
                    lx2 = x2 * sx + off_x
                    ly2 = y2 * sy + off_y
                    color = box.get("color", _BOX_COLORS[0])
                    pen = QPen(color, 2)
                    painter.setPen(pen)
                    painter.drawRect(int(lx1), int(ly1), int(lx2 - lx1), int(ly2 - ly1))
                    painter.setFont(QFont("", 10, QFont.Bold))
                    painter.drawText(int(lx1), int(ly1) - 4, box["name"])
        painter.end()


# ---- 具身智能指令执行 Worker ----

class PresetActionWorker(QThread):
    """后台执行预设动作序列"""
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, motor_manager, joints_list: list, duration: float, speed: int = 200):
        super().__init__()
        self._mm = motor_manager
        self._joints_list = joints_list
        self._duration = duration
        self._speed = speed

    def run(self):
        try:
            import time
            for joints in self._joints_list:
                if not self._mm.is_connected:
                    self.error.emit("电机未连接")
                    return
                targets = {mid: angle for mid, angle in enumerate(joints, 1) if mid in self._mm.motors}
                if targets:
                    self._mm.sync_move_positions(targets, speed=self._speed)
                time.sleep(self._duration)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class _CompoundSequenceWorker(QThread):
    """串行执行：闭合夹爪 → 回拍照位置 → 移动到放回位置 → 松开夹爪 → 回拍照位置"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, motor_manager, claw, target_world, close_angle, speed):
        super().__init__()
        self._mm = motor_manager
        self._claw = claw
        self._target = target_world
        self._close_angle = close_angle
        self._speed = speed

    def run(self):
        try:
            from Horizon_Core import gateway as horizon_gateway
            import time

            # ① 闭合夹爪
            self.progress.emit("① 闭合夹爪...")
            if self._claw and self._claw.is_connected:
                self._claw.set_angle(self._close_angle)
            time.sleep(1.0)

            # ② 回到拍照位置（安全高度）
            self.progress.emit("② 回到拍照位置...")
            _tg = self._mm.go_to_photo_position(speed=self._speed)
            # 等待到位
            self._wait_joint5((_tg or {}).get(5, 0.0), timeout=20)

            # ③ 移动到放回位置
            x, y, z = self._target
            self.progress.emit(f"③ 移动到放回位置 ({x:.0f},{y:.0f},{z:.0f})...")
            embodied_internal = horizon_gateway.get_embodied_internal_module()
            embodied_internal._set_motion_params(
                max_speed=self._speed, acceleration=self._speed, deceleration=self._speed
            )
            embodied_func = horizon_gateway.get_embodied_module()
            ok = embodied_func.c_a_p(
                [float(x), float(y), float(z)],
                [0.0, 0.0, 180.0]
            )
            if not ok:
                self.error.emit("移动到放回位置失败")
                return
            time.sleep(1.0)

            # ④ 松开夹爪
            self.progress.emit("④ 松开夹爪...")
            if self._claw and self._claw.is_connected:
                self._claw.set_angle(90)
            time.sleep(1.0)

            # ⑤ 回到拍照位置
            self.progress.emit("⑤ 回到拍照位置...")
            _tg = self._mm.go_to_photo_position(speed=self._speed)
            self._wait_joint5((_tg or {}).get(5, 0.0), timeout=20)

            self.finished.emit("已完成：抓取 → 放回原位")

        except Exception as e:
            self.error.emit(f"连续动作失败: {e}")

    def _wait_joint5(self, target_deg, timeout=20):
        """等待关节5到达目标角度"""
        import time
        time.sleep(2.0)
        ratio = self._mm._ratios.get(5, 8.32)
        direction = self._mm._dirs.get(5, -1)
        target_encoder = target_deg * direction * ratio
        start = time.time()
        while time.time() - start < timeout:
            try:
                pos = self._mm.motors[5].read_parameters.get_position()
                if abs(pos - target_encoder) < 100:
                    time.sleep(1.0)
                    return
            except Exception:
                pass
            time.sleep(0.3)


class _PickPlaceWorker(QThread):
    """多步抓取-放置流程：抓取物体 → 回位 → 移动到容器 → 松开 → 回位"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, vision_manager, motor_manager, claw,
                 target_box: dict, container_box: dict,
                 speed: int = 500, close_angle: float = 25.0):
        super().__init__()
        self._vm = vision_manager
        self._mm = motor_manager
        self._claw = claw
        self._target = target_box
        self._container = container_box
        self._speed = speed
        self._close_angle = close_angle

    def run(self):
        try:
            import time
            from Horizon_Core import gateway as horizon_gateway

            if not self._vm or not self._vm._vision_sdk:
                self.error.emit("视觉 SDK 未初始化")
                return

            embodied_internal = horizon_gateway.get_embodied_internal_module()
            target_name = self._target["name"]
            container_name = self._container["name"]

            # 设置运动速度
            embodied_internal._set_motion_params(
                max_speed=self._speed,
                acceleration=self._speed,
                deceleration=self._speed,
            )

            # ⓪ 抓取前先张开夹爪（避免沿用上一次的闭合状态导致抓空）
            if self._claw and self._claw.is_connected:
                self._claw.set_angle(90)
                time.sleep(0.8)

            # ① 抓取目标物体
            self.progress.emit(f"① 抓取 {target_name}...")
            tx1, ty1, tx2, ty2 = self._target["bbox"]
            tcx, tcy = (tx1 + tx2) / 2, (ty1 + ty2) / 2
            ok = self._vm._vision_sdk.grasp_at_pixel(tcx, tcy)
            if not ok:
                self.error.emit(f"抓取 {target_name} 失败")
                return
            time.sleep(0.5)

            # ② 闭合夹爪
            self.progress.emit("② 闭合夹爪...")
            if self._claw and self._claw.is_connected:
                self._claw.set_angle(self._close_angle)
            time.sleep(1.0)

            # ③ 回到拍照位置
            self.progress.emit("③ 回到拍照位置...")
            self._mm.go_to_photo_position(speed=self._speed)
            time.sleep(5.0)

            # ④ 移动到容器上方（高于容器 150mm）
            self.progress.emit(f"④ 移动到 {container_name} 上方...")
            cx1, cy1, cx2, cy2 = self._container["bbox"]
            ccx, ccy = (cx1 + cx2) / 2, (cy1 + cy2) / 2

            # 计算容器的世界坐标，Z 抬高 150mm
            container_info = self._vm.compute_pixel_info(ccx, ccy)
            if container_info is None:
                self.error.emit(f"无法计算 {container_name} 的位置")
                return

            wx, wy, wz = container_info["world"]
            yaw, pitch, roll = container_info["euler_angles"]
            safe_z = wz + 150.0  # 抬高 150mm

            # 设置速度并移动到容器上方
            embodied_internal._set_motion_params(
                max_speed=self._speed,
                acceleration=self._speed,
                deceleration=self._speed,
            )
            embodied_func = horizon_gateway.get_embodied_module()
            ok = embodied_func.c_a_p(
                [float(wx), float(wy), float(safe_z)],
                [float(yaw), float(pitch), float(roll)]
            )
            if not ok:
                self.error.emit(f"移动到 {container_name} 上方失败")
                return
            time.sleep(0.5)

            # ⑤ 松开夹爪
            self.progress.emit("⑤ 松开夹爪...")
            if self._claw and self._claw.is_connected:
                self._claw.set_angle(90)
            time.sleep(1.0)

            # ⑥ 回到拍照位置
            self.progress.emit("⑥ 回到拍照位置...")
            self._mm.go_to_photo_position(speed=self._speed)
            time.sleep(5.0)

            self.finished.emit(f"已完成：{target_name} 放入 {container_name}")

        except Exception as e:
            self.error.emit(f"放置失败: {e}")


class _ChatWorker(QThread):
    """LLM 普通对话"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, instruction: str, llm_config: dict, frame=None):
        super().__init__()
        self._instruction = instruction
        self._config = llm_config
        self._frame = frame

    def run(self):
        try:
            import base64
            import cv2

            api_base = self._config["api_base"]
            api_key = self._config["api_key"]
            model = self._config.get("model", "qwen-vl-plus")
            max_tokens = self._config.get("max_tokens", 2048)

            # 优先使用 voice_config 的 system_prompt
            system_prompt = "你是贾维斯，一个智能机械臂助手。回答必须简短精炼，最多两三句话，不要废话。"
            try:
                vc_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "config", "voice_config.json"
                )
                if os.path.exists(vc_path):
                    with open(vc_path, "r", encoding="utf-8") as f:
                        vc = json.load(f)
                    if vc.get("llm_system_prompt"):
                        system_prompt = vc["llm_system_prompt"]
            except Exception:
                pass

            if self._frame is not None:
                # 有画面 → 多模态
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                cv2.imwrite(tmp.name, self._frame)
                tmp.close()
                with open(tmp.name, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                os.unlink(tmp.name)

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": self._instruction},
                    ]},
                ]
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._instruction},
                ]

            url = api_base.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            body = {"model": model, "max_tokens": max_tokens, "temperature": 0.7, "messages": messages}

            resp = requests.post(url, headers=headers, json=body, timeout=30)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()

            self.finished.emit(text)

        except Exception as e:
            self.error.emit(f"对话失败: {e}")


class _InstructionWorker(QThread):
    """后台执行自然语言指令（兼容旧接口）"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, embodied_sdk, instruction: str):
        super().__init__()
        self._sdk = embodied_sdk
        self._instruction = instruction

    def run(self):
        try:
            self._sdk.run_nl_instruction_stream(
                self._instruction,
                progress_handler=lambda msg: self.progress.emit(str(msg)),
            )
            self.finished.emit(self._instruction)
        except Exception as e:
            self.error.emit(str(e))


# 视觉相关关键词（描述类）
_VISION_KEYWORDS = [
    "画面", "摄像头", "相机", "看到", "视野", "镜头",
    "物体", "目标", "东西", "什么", "几个", "多少",
    "检测", "识别", "看看", "拍到", "面前", "桌上",
    "放到", "放进", "放入", "装进", "装入", "放去",
]
# 动作类关键词（搭配物体名 → 需要先检测再执行）
_ACTION_KEYWORDS = [
    "抓取", "抓", "拿", "取", "夹", "拾", "搬", "移动", "递给",
]


class _VisionInstructionWorker(QThread):
    """后台执行视觉问答指令：同时调用 LLM 描述 + 检测标框"""
    finished = pyqtSignal(str, list)  # (llm_response, detection_boxes)
    error = pyqtSignal(str)

    def __init__(self, frame: np.ndarray, instruction: str, llm_config: dict):
        super().__init__()
        self._frame = frame
        self._instruction = instruction
        self._config = llm_config

    def run(self):
        try:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            cv2.imwrite(tmp.name, self._frame)
            tmp.close()

            with open(tmp.name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            os.unlink(tmp.name)

            frame_h, frame_w = self._frame.shape[:2]

            # 判断是否为抓取/放置类指令
            place_keywords = ["放到", "放进", "放入", "放到", "放去", "装进", "装入"]
            is_place_instruction = any(kw in self._instruction for kw in place_keywords)

            if is_place_instruction:
                # 放置类指令：需要返回动作计划 + 检测框
                prompt = (
                    f"你是贾维斯，智能机械臂助手。看这张 {frame_w}x{frame_h} 像素的图片。\n"
                    f"用户指令：{self._instruction}\n\n"
                    f"注意：画面顶部边缘可能出现机械臂自身的绿色夹爪，请忽略它。\n\n"
                    f"请分析画面，用中文简短回复（一两句话），然后输出动作计划和检测框。\n"
                    f"动作计划格式：ACTION_PLAN:{{\"target\":\"实际物体名\",\"container\":\"实际容器名\"}}\n"
                    f"检测框格式：DETECTIONS:[{{\"name\":\"实际物体名\",\"bbox\":[x1,y1,x2,y2]}}]\n"
                    f"其中 name 必须是你在画面中看到的物体的真实名称，不要用占位符。\n"
                    f"如果没有找到目标，target 或 container 设为 null。"
                )
            else:
                # 普通指令：返回描述 + 检测框
                prompt = (
                    f"你是贾维斯，智能机械臂助手。看这张 {frame_w}x{frame_h} 像素的图片。\n"
                    f"用户问：{self._instruction}\n\n"
                    f"注意：画面顶部边缘可能出现机械臂自身的绿色夹爪，请忽略它。\n\n"
                    f"请用中文回答，逐一列出桌面上每个物体的具体名称（如药品名、品牌、颜色等细节），不要笼统概括。\n"
                    f"然后另起一行输出检测框（不要加任何过渡语句）：\n"
                    f"DETECTIONS:[{{\"name\":\"实际看到的物体名\",\"bbox\":[x1,y1,x2,y2]}}]\n"
                    f"其中 name 必须是你在画面中实际看到的物体名称（如红色苹果、维生素C片等），绝不能用占位符。"
                )

            api_base = self._config["api_base"]
            api_key = self._config["api_key"]
            model = self._config.get("model", "qwen-vl-plus")
            max_tokens = self._config.get("max_tokens", 2048)
            temperature = self._config.get("temperature", 0.3)

            # 调用 LLM
            if "/anthropic" in api_base:
                text = LLMDetectWorker._call_anthropic(api_base, api_key, model, b64, prompt, max_tokens, temperature)
            else:
                text = LLMDetectWorker._call_openai(api_base, api_key, model, b64, prompt, max_tokens, temperature)

            # 分离文字回复和检测结果
            import re
            llm_response = text
            boxes = []

            # 1. 尝试提取 JSON 检测框（从代码块或裸 JSON）
            json_str = None
            m = re.search(r'```json\s*([\s\S]*?)```', text)
            if m:
                json_str = m.group(1)
            else:
                m = re.search(r'```([\s\S]*?)```', text)
                if m:
                    json_str = m.group(1)
                else:
                    m = re.search(r'(\[\s*\{[\s\S]*?\}\s*\])', text)
                    if m:
                        json_str = m.group(1)

            if json_str:
                boxes = LLMDetectWorker._parse_response(json_str)

            if "DETECTIONS:" in text:
                parts = text.split("DETECTIONS:", 1)
                llm_response = parts[0].strip()
                det_boxes = LLMDetectWorker._parse_response(parts[1].strip())
                if det_boxes:
                    boxes = det_boxes

            # 2. 清理文字中的所有 markdown/JSON 标记（避免 TTS 读出来）
            llm_response = re.sub(r'```json[\s\S]*', '', llm_response)
            llm_response = re.sub(r'```[\s\S]*', '', llm_response)
            llm_response = re.sub(r'\[\s*\{[\s\S]*?\}\s*\]', '', llm_response)
            llm_response = llm_response.strip()

            self.finished.emit(llm_response, boxes)

        except Exception as e:
            self.error.emit(str(e))


class CameraPanel(QWidget):
    log_message = pyqtSignal(str, str)

    def __init__(self, motor_manager=None, parent=None):
        super().__init__(parent)
        self._cap: cv2.VideoCapture | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_frame)
        self._frame: np.ndarray | None = None
        self._click_pixel: tuple | None = None
        self._pixel_info: dict | None = None
        self._claw = None
        self._grasp_worker = None
        self._detect_worker = None
        self._detection_boxes = []

        self._motor_manager = motor_manager
        self._vision_manager: VisionManager | None = None
        self._embodied_sdk = None
        self._instruction_worker = None
        self._voice_manager = None
        self._voice_active = False
        self._auto_grasp_enabled = False
        self._adaptive_enabled = False
        self._current_grasp_box: dict | None = None  # 当前抓取目标的检测框信息
        self._detect_then_grasp_worker = None
        self._pending_grasp_text = ""
        self._compound_action = ""
        self._compound_place_text = ""
        self._compound_container_box = None
        self._compound_frame = None
        self._last_grasp_position: dict | None = None  # 抓取时记录的原始位置 {"pixel": (u,v), "world": (x,y,z)}
        if motor_manager is not None:
            self._vision_manager = VisionManager(motor_manager)

        self._init_ui()

    def set_claw_controller(self, claw):
        self._claw = claw
        # 同步给视觉管理器：抓取前自动张开夹爪
        if self._vision_manager is not None:
            self._vision_manager.set_claw(claw)

    def _get_speed(self) -> int:
        """获取当前速度设置"""
        return self.speed_spin.value()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("相机ID:"))
        self.id_combo = QComboBox()
        self.id_combo.addItems(["0", "1", "2", "3"])
        self.id_combo.setFixedWidth(60)
        toolbar.addWidget(self.id_combo)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setStyleSheet(
            "QPushButton { background-color: #3b82f6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2563eb; }"
        )
        self.btn_connect.clicked.connect(self._on_connect)
        toolbar.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_disconnect.setEnabled(False)
        toolbar.addWidget(self.btn_disconnect)

        self.btn_photo_pos = QPushButton("拍照位置")
        self.btn_photo_pos.setStyleSheet(            "QPushButton { background-color: #8b5cf6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #7c3aed; }"
        )
        self.btn_photo_pos.clicked.connect(self._on_photo_position)
        toolbar.addWidget(self.btn_photo_pos)

        self.btn_record_photo = QPushButton("记录拍照位")
        self.btn_record_photo.setToolTip(
            "手动把机械臂摆到理想的拍照姿态（相机朝向桌面），\n"
            "点此记录。之后\"拍照位置\"按钮与抓取回位都会走到该姿态。"
        )
        self.btn_record_photo.clicked.connect(self._on_record_photo_position)
        toolbar.addWidget(self.btn_record_photo)

        toolbar.addWidget(QLabel("速度:"))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(50, 800)
        self.speed_spin.setValue(500)
        self.speed_spin.setSingleStep(50)
        self.speed_spin.setSuffix(" RPM")
        self.speed_spin.setFixedWidth(90)
        self.speed_spin.setToolTip("关节运动速度 (50-800 RPM)")
        toolbar.addWidget(self.speed_spin)

        toolbar.addWidget(QLabel("闭合角:"))
        self.claw_angle_spin = QSpinBox()
        self.claw_angle_spin.setRange(0, 90)
        self.claw_angle_spin.setValue(int(self._load_claw_close_angle()))
        self.claw_angle_spin.setSuffix("°")
        self.claw_angle_spin.setFixedWidth(70)
        self.claw_angle_spin.setToolTip("自动抓取时夹爪闭合角度 (0=完全闭合, 90=完全张开)")
        self.claw_angle_spin.valueChanged.connect(self._on_claw_angle_changed)
        toolbar.addWidget(self.claw_angle_spin)

        self.chk_adaptive = QCheckBox("自适应")
        self.chk_adaptive.setChecked(self._load_adaptive_enabled())
        self.chk_adaptive.setToolTip("根据检测框大小自动调整闭合角度")
        self.chk_adaptive.stateChanged.connect(self._on_adaptive_changed)
        toolbar.addWidget(self.chk_adaptive)

        self.btn_auto_grasp = QPushButton("自动抓取: 关")
        self.btn_auto_grasp.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
        self.btn_auto_grasp.setToolTip("开启后：抓取到位 → 自动闭合夹爪 → 回到拍照位置")
        self.btn_auto_grasp.clicked.connect(self._on_toggle_auto_grasp)
        toolbar.addWidget(self.btn_auto_grasp)

        toolbar.addWidget(QLabel("检测:"))
        self.detect_mode_combo = QComboBox()
        self.detect_mode_combo.addItems(["YOLO 本地", "AI 多模态"])
        self.detect_mode_combo.setFixedWidth(110)
        self.detect_mode_combo.setToolTip("YOLO: 本地ONNX模型，快速稳定\nAI: 千问VL Plus多模态，需联网")
        toolbar.addWidget(self.detect_mode_combo)

        self.btn_ai_detect = QPushButton("检测")
        self.btn_ai_detect.setStyleSheet(
            "QPushButton { background-color: #0ea5e9; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #0284c7; }"
        )
        self.btn_ai_detect.clicked.connect(self._on_ai_detect)
        toolbar.addWidget(self.btn_ai_detect)

        self.btn_mic = QPushButton("🎤 语音")
        self.btn_mic.setStyleSheet(
            "QPushButton { background-color: #f59e0b; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #d97706; }"
        )
        self.btn_mic.clicked.connect(self._on_mic_toggle)
        toolbar.addWidget(self.btn_mic)

        toolbar.addStretch()
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: #6b7280; font-weight: bold;")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        # ---- 预览区域（摄像头画面） ----
        self.preview_label = ClickableLabel("相机未连接")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(640, 480)
        self.preview_label.setStyleSheet(
            "background-color: #1f2937; color: #9ca3af; font-size: 16px; border: 2px solid #374151; border-radius: 8px;"
        )
        self.preview_label.clicked.connect(self._on_preview_click)
        layout.addWidget(self.preview_label, stretch=1)

        # ---- 自然语言指令输入（紧贴画布下方） ----
        layout.addWidget(self._build_instruction_bar())

        # ---- 下方信息区：左右布局 ----
        bottom = QHBoxLayout()

        # 左侧：抓取状态 + 功能按钮
        left = QVBoxLayout()
        left.addWidget(self._build_info_panel())
        left.addWidget(self._build_action_buttons())
        bottom.addLayout(left, stretch=2)

        # 右侧：AI 检测结果（可滚动）
        bottom.addWidget(self._build_detect_panel(), stretch=1)

        layout.addLayout(bottom)

    def _build_instruction_bar(self) -> QWidget:
        """构建自然语言指令输入栏（紧贴画布下方）"""
        bar = QWidget()
        bar.setStyleSheet("QWidget { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)

        icon = QLabel("🤖")
        icon.setStyleSheet("font-size: 16px; border: none;")
        layout.addWidget(icon)

        self.instruction_input = QLineEdit()
        self.instruction_input.setPlaceholderText("输入指令，例如：把红色杯子拿过来、点头、去初始位置…")
        self.instruction_input.setStyleSheet(
            "QLineEdit { padding: 6px 10px; font-size: 13px; border: 1px solid #d1d5db; border-radius: 4px; background: white; }"
            "QLineEdit:focus { border-color: #3b82f6; }"
        )
        self.instruction_input.returnPressed.connect(self._on_execute_instruction)
        layout.addWidget(self.instruction_input, stretch=1)

        self.btn_execute = QPushButton("执行")
        self.btn_execute.setStyleSheet(
            "QPushButton { background-color: #3b82f6; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2563eb; }"
            "QPushButton:disabled { background-color: #9ca3af; }"
        )
        self.btn_execute.clicked.connect(self._on_execute_instruction)
        layout.addWidget(self.btn_execute)

        self.btn_inst_stop = QPushButton("停止")
        self.btn_inst_stop.setStyleSheet(
            "QPushButton { background-color: #ef4444; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #dc2626; }"
        )
        self.btn_inst_stop.clicked.connect(self._on_stop_instruction)
        self.btn_inst_stop.setEnabled(False)
        layout.addWidget(self.btn_inst_stop)

        self.inst_status = QLabel("")
        self.inst_status.setStyleSheet("color: #6b7280; font-size: 12px; border: none; padding-left: 4px;")
        layout.addWidget(self.inst_status)

        return bar

    def _build_info_panel(self):
        group = QGroupBox("抓取状态信息")
        grid = QGridLayout()
        grid.setContentsMargins(8, 4, 8, 4)

        grid.addWidget(QLabel("像素坐标:"), 0, 0)
        self.lbl_pixel = QLabel("--")
        self.lbl_pixel.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.lbl_pixel, 0, 1)

        grid.addWidget(QLabel("检测框:"), 0, 2)
        self.lbl_bbox = QLabel("--")
        self.lbl_bbox.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.lbl_bbox, 0, 3)

        grid.addWidget(QLabel("旋转角:"), 0, 4)
        self.lbl_rotation = QLabel("--")
        self.lbl_rotation.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.lbl_rotation, 0, 5)

        grid.addWidget(QLabel("深度:"), 1, 0)
        self.lbl_depth = QLabel("--")
        self.lbl_depth.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.lbl_depth, 1, 1, 1, 2)

        grid.addWidget(QLabel("世界坐标:"), 1, 3)
        self.lbl_world = QLabel("--")
        self.lbl_world.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.lbl_world, 1, 4, 1, 2)

        group.setLayout(grid)
        return group

    def _build_detect_panel(self):
        """构建 AI 检测结果面板（可滚动，无高度限制）"""
        self._detect_group = QGroupBox("AI 检测结果")
        outer = QVBoxLayout()
        outer.setContentsMargins(4, 4, 4, 4)

        self._detect_scroll = QScrollArea()
        self._detect_scroll.setWidgetResizable(True)
        self._detect_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._detect_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 6px; }"
        )

        self._detect_container = QWidget()
        self._detect_layout = QVBoxLayout(self._detect_container)
        self._detect_layout.setContentsMargins(4, 2, 4, 2)
        self._detect_layout.setSpacing(2)

        self._detect_label = QLabel("未检测")
        self._detect_label.setStyleSheet("color: #6b7280;")
        self._detect_layout.addWidget(self._detect_label)
        self._detect_layout.addStretch()

        self._detect_scroll.setWidget(self._detect_container)
        outer.addWidget(self._detect_scroll)
        self._detect_group.setLayout(outer)
        return self._detect_group

    def _display_detection_boxes(self, boxes: list):
        """在画布上显示检测框"""
        det_frame_w, det_frame_h = 0, 0
        if self._frame is not None:
            det_frame_h, det_frame_w = self._frame.shape[:2]

        self._detection_boxes = []
        for i, box in enumerate(boxes):
            self._detection_boxes.append({
                "name": box["name"],
                "bbox": box["bbox"],
                "color": _BOX_COLORS[i % len(_BOX_COLORS)],
            })
            self.log_message.emit("info", f"检测框 {i+1}: {box['name']} bbox={box['bbox']} (帧{det_frame_w}x{det_frame_h})")
        self.preview_label.set_detection_boxes(self._detection_boxes, det_frame_w, det_frame_h)
        self._rebuild_detect_list()

    def _rebuild_detect_list(self):
        while self._detect_layout.count():
            item = self._detect_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._detection_boxes:
            self._detect_label = QLabel("未检测")
            self._detect_label.setStyleSheet("color: #6b7280;")
            self._detect_layout.addWidget(self._detect_label)
            self._detect_layout.addStretch()
            return

        for i, box in enumerate(self._detection_boxes):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)

            color_lbl = QLabel("■")
            color = box.get("color", _BOX_COLORS[0])
            color_lbl.setStyleSheet(f"color: {color.name()}; font-size: 14px; border: none;")
            row.addWidget(color_lbl)

            name_lbl = QLabel(f"[{i+1}] {box['name']}")
            name_lbl.setStyleSheet("font-weight: bold; border: none;")
            row.addWidget(name_lbl)

            btn = QPushButton("抓取")
            btn.setFixedWidth(50)
            btn.setStyleSheet(
                "QPushButton { background-color: #22c55e; color: white; font-weight: bold; padding: 2px 8px; border-radius: 3px; }"
                "QPushButton:hover { background-color: #16a34a; }"
            )
            btn.clicked.connect(lambda checked, idx=i: self._on_grasp_detected_box(idx))
            row.addWidget(btn)
            row.addStretch()

            container = QWidget()
            container.setStyleSheet("background: transparent;")
            container.setLayout(row)
            self._detect_layout.addWidget(container)

        self._detect_layout.addStretch()

    def _build_action_buttons(self):
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 4, 0, 0)

        self.btn_get_pos = QPushButton("获取位置")
        self.btn_get_pos.setStyleSheet(
            "QPushButton { background-color: #0ea5e9; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #0284c7; }"
        )
        self.btn_get_pos.clicked.connect(self._on_get_position)
        btn_layout.addWidget(self.btn_get_pos)

        self.btn_grasp = QPushButton("执行抓取")
        self.btn_grasp.setStyleSheet(
            "QPushButton { background-color: #22c55e; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #16a34a; }"
        )
        self.btn_grasp.clicked.connect(self._on_execute_grasp)
        btn_layout.addWidget(self.btn_grasp)

        self.btn_claw_close = QPushButton("夹爪闭合")
        self.btn_claw_close.setStyleSheet(
            "QPushButton { background-color: #f59e0b; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #d97706; }"
        )
        self.btn_claw_close.clicked.connect(self._on_claw_close)
        btn_layout.addWidget(self.btn_claw_close)

        self.btn_claw_open = QPushButton("夹爪松开")
        self.btn_claw_open.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
        self.btn_claw_open.clicked.connect(self._on_claw_open)
        btn_layout.addWidget(self.btn_claw_open)

        self.btn_emergency = QPushButton("紧急停止 (空格)")
        self.btn_emergency.setStyleSheet(
            "QPushButton { background-color: #ef4444; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #dc2626; }"
        )
        self.btn_emergency.clicked.connect(self._on_emergency_stop)
        btn_layout.addWidget(self.btn_emergency)

        btn_layout.addStretch()
        return btn_widget

    def _ensure_vision_sdk(self):
        if self._vision_manager and not self._vision_manager.is_initialized:
            cam_id = int(self.id_combo.currentText())
            ok = self._vision_manager.init_sdk(cam_id)
            if ok:
                self.log_message.emit("info", "视觉 SDK 初始化成功")
            else:
                self.log_message.emit("error", "视觉 SDK 初始化失败")
            return ok
        return self._vision_manager is not None and self._vision_manager.is_initialized

    # ---- 点击抓取 ----

    def _on_preview_click(self, label_x, label_y):
        if self._frame is None:
            return
        u, v = self._label_to_frame_coords(label_x, label_y)
        if u is None:
            return
        self._click_pixel = (u, v)
        self._pixel_info = None
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        self.lbl_pixel.setText(f"({u:.0f}, {v:.0f})")
        self.lbl_bbox.setText("--")
        self.lbl_rotation.setText("--")
        self.lbl_depth.setText("--")
        self.lbl_world.setText("--")
        self.log_message.emit("info", f"已选择像素: ({u:.0f}, {v:.0f})")

    # ---- 获取位置 ----
#important
    def _on_get_position(self):
        if self._click_pixel is None:
            QMessageBox.warning(self, "提示", "请先在画面上点击目标位置")
            return
        if not self._ensure_vision_sdk():
            return

        u, v = self._click_pixel
        info = self._vision_manager.compute_pixel_info(u, v)
        if info is None:
            self.log_message.emit("error", "计算位置信息失败")
            return

        self._pixel_info = info
        wx, wy, wz = info["world"]
        tx, ty, tz = info["tcp_offset"]
        yaw, pitch, roll = info["euler_angles"]

        self.lbl_depth.setText(info["depth_info"])
        self.lbl_world.setText(f"({wx:.1f}, {wy:.1f}, {wz:.1f}) mm")

        msg = (
            f"像素坐标: ({u:.0f}, {v:.0f})\n"
            f"世界坐标: ({wx:.1f}, {wy:.1f}, {wz:.1f}) mm\n"
            f"TCP偏移: ({tx:.1f}, {ty:.1f}, {tz:.1f}) mm\n"
            f"末端姿态: Yaw={yaw:.1f}° Pitch={pitch:.1f}° Roll={roll:.1f}°\n"
            f"深度: {info['depth_info']}"
        )
        QMessageBox.information(self, "位置信息", msg)
        self.log_message.emit("info", f"位置信息: 世界({wx:.1f}, {wy:.1f}, {wz:.1f})")

    # ---- 执行抓取 ----

    def _on_execute_grasp(self):
        if self._click_pixel is None:
            QMessageBox.warning(self, "提示", "请先在画面上点击目标位置")
            return
        if not self._ensure_vision_sdk():
            return
        if self._grasp_worker is not None:
            return

        u, v = self._click_pixel
        self.log_message.emit("info", f"执行抓取: 像素 ({u:.0f}, {v:.0f})")

        worker = self._vision_manager.grasp_at_pixel_async(u, v, speed=self._get_speed())
        if worker is None:
            self.log_message.emit("error", "抓取失败: 无法创建工作任务")
            return

        self._grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        self.btn_grasp.setEnabled(False)
        self.btn_grasp.setText("执行中...")
        worker.finished.connect(self._on_grasp_done)
        worker.error.connect(self._on_grasp_error)
        worker.start()

    def _on_grasp_done(self, ok):
        worker = self._grasp_worker
        self._grasp_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)
        self.btn_grasp.setEnabled(True)
        self.btn_grasp.setText("执行抓取")
        self._reopen_camera()
        if ok:
            if self._auto_grasp_enabled and self._claw and self._claw.is_connected:
                # 自动抓取模式：闭合夹爪 → 回拍照位置 → 完成后恢复语音
                angle = self._get_effective_angle()
                self._claw.set_angle(angle)
                self._current_grasp_box = None
                self.log_message.emit("info", f"自动闭合夹爪: {angle}°")
                QTimer.singleShot(1500, self._auto_return_photo)
            else:
                # 手动抓取模式：抓取后同样自动回拍照位。
                # 原因：像素→世界换算依赖拍照位姿，若停在桌面低位，
                # 下一次点击抓取会把目标投影到桌面以下（如 z=-614mm）而必然失败。
                self.log_message.emit("info", "抓取完成（仅6关节运动，夹爪不动），自动回到拍照位置...")
                self._auto_return_photo()
        else:
            reason = None
            if self._vision_manager is not None:
                reason = getattr(self._vision_manager, "last_error", None)
            msg = f"抓取失败: {reason}" if reason else "抓取失败"
            self.log_message.emit("error", msg)
            self._speak_if_voice("抓取失败")

    def _auto_return_photo(self):
        """自动回到拍照位置（夹爪不松开），完成后恢复语音监听"""
        if self._motor_manager and self._motor_manager.is_connected:
            self._motor_manager.go_to_photo_position(speed=self._get_speed())
            self.log_message.emit("info", "自动回到拍照位置")
            # 等待回到拍照位置后，通知语音会话恢复监听
            QTimer.singleShot(5000, lambda: self._speak_if_voice("好的，已完成"))

    def _on_toggle_auto_grasp(self):
        """切换自动抓取模式"""
        self._auto_grasp_enabled = not self._auto_grasp_enabled
        if self._auto_grasp_enabled:
            self.btn_auto_grasp.setText("自动抓取: 开")
            self.btn_auto_grasp.setStyleSheet(
                "QPushButton { background-color: #22c55e; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #16a34a; }"
            )
            self.log_message.emit("info", "自动抓取模式已开启")
        else:
            self.btn_auto_grasp.setText("自动抓取: 关")
            self.btn_auto_grasp.setStyleSheet(
                "QPushButton { background-color: #6b7280; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #4b5563; }"
            )
            self.log_message.emit("info", "自动抓取模式已关闭")

    def _on_grasp_error(self, msg):
        worker = self._grasp_worker
        self._grasp_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)
        self.btn_grasp.setEnabled(True)
        self.btn_grasp.setText("执行抓取")
        self._reopen_camera()
        self.log_message.emit("error", f"抓取异常: {msg}")

    def _reopen_camera(self):
        cam_id = int(self.id_combo.currentText())
        self._cap = open_capture(cam_id)
        if self._cap is not None:
            self._timer.start(33)
        else:
            self.log_message.emit("error", "摄像头恢复失败，请手动断开重连")
            self._cap = None

    def _on_emergency_stop(self):
        if self._motor_manager and self._motor_manager.is_connected:
            self._motor_manager.stop_all()
            self.log_message.emit("warn", "紧急停止: 所有关节已停止")

    # ---- AI 多模态检测 ----

    def _on_ai_detect(self):
        if self._frame is None:
            QMessageBox.warning(self, "提示", "请先连接摄像头")
            return
        if self._detect_worker is not None:
            return
        if not self._vision_manager:
            QMessageBox.warning(self, "提示", "视觉管理器未初始化")
            return

        mode_text = self.detect_mode_combo.currentText()
        mode = "yolo" if "YOLO" in mode_text else "llm"

        worker = self._vision_manager.detect_objects_async(self._frame.copy(), mode=mode)
        if worker is None:
            if mode == "yolo":
                self.log_message.emit("error", "YOLO 检测启动失败，请确认 onnxruntime 已安装且 yolov8n.onnx 存在")
            else:
                self.log_message.emit("error", "AI 检测启动失败，请检查 config/llm_config.json 配置")
            return

        self._detect_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        self.btn_ai_detect.setEnabled(False)
        self.btn_ai_detect.setText("检测中...")
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        worker.finished.connect(self._on_detect_done)
        worker.error.connect(self._on_detect_error)
        worker.start()

    def _on_detect_done(self, boxes):
        worker = self._detect_worker
        self._detect_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)
        self.btn_ai_detect.setEnabled(True)
        self.btn_ai_detect.setText("检测")

        det_frame_w = 0
        det_frame_h = 0
        if self._frame is not None:
            det_frame_h, det_frame_w = self._frame.shape[:2]

        self._reopen_camera()

        if not boxes:
            self.log_message.emit("info", "AI 检测完成，未发现物体")
            self._detection_boxes = []
            self.preview_label.clear_detection_boxes()
            self._rebuild_detect_list()
            return

        self._display_detection_boxes(boxes)
        self.log_message.emit("info", f"AI 检测完成，发现 {len(boxes)} 个物体")

    def _on_detect_error(self, msg):
        worker = self._detect_worker
        self._detect_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)
        self.btn_ai_detect.setEnabled(True)
        self.btn_ai_detect.setText("检测")
        self._reopen_camera()
        self.log_message.emit("error", f"AI 检测失败: {msg}")

    def _on_grasp_detected_box(self, index):
        if index >= len(self._detection_boxes):
            return
        if not self._ensure_vision_sdk():
            return
        if self._grasp_worker is not None:
            return

        box = self._detection_boxes[index]
        x1, y1, x2, y2 = box["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        self._current_grasp_box = box  # 用于自适应角度计算
        self._record_grasp_position(cx, cy)  # 记录原始位置
        self.log_message.emit("info", f"抓取检测目标: {box['name']} 中心({cx:.0f}, {cy:.0f})")

        worker = self._vision_manager.grasp_at_pixel_async(cx, cy, speed=self._get_speed())
        if worker is None:
            self.log_message.emit("error", "抓取失败: 无法创建工作任务")
            return

        self._grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        worker.finished.connect(self._on_grasp_done)
        worker.error.connect(self._on_grasp_error)
        worker.start()

    # ---- 夹爪控制 ----

    def _on_claw_close(self):
        if not self._claw or not self._claw.is_connected:
            QMessageBox.warning(self, "提示", "夹爪未连接，请先在机械臂控制页面连接夹爪")
            return
        angle = self._get_claw_angle()
        if self._claw.set_angle(angle):
            self.log_message.emit("info", f"夹爪闭合: {angle}°")
        else:
            self.log_message.emit("error", "夹爪控制失败")

    def _on_claw_open(self):
        if not self._claw or not self._claw.is_connected:
            QMessageBox.warning(self, "提示", "夹爪未连接，请先在机械臂控制页面连接夹爪")
            return
        if self._claw.set_angle(90):
            self.log_message.emit("info", "夹爪松开: 90°（完全张开）")
        else:
            self.log_message.emit("error", "夹爪控制失败")

    def _load_claw_close_angle(self) -> float:
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "claw_config.json",
            )
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    return float(json.load(f).get("close_angle", 65.0))
        except Exception:
            pass
        return 65.0

    def _save_claw_close_angle(self, angle: float):
        """保存夹爪闭合角度到配置文件（读-改-写，保留 servo_invert 等其他字段）"""
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "claw_config.json",
            )
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            data = {}
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
            data["close_angle"] = float(angle)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _on_claw_angle_changed(self, value: int):
        """工具栏角度输入框变化时保存"""
        self._save_claw_close_angle(float(value))

    def _on_adaptive_changed(self, state: int):
        """自适应复选框变化时保存"""
        self._adaptive_enabled = state == Qt.Checked
        self._save_adaptive_enabled(self._adaptive_enabled)
        if hasattr(self, 'claw_angle_spin'):
            self.claw_angle_spin.setEnabled(not self._adaptive_enabled)

    def _load_adaptive_enabled(self) -> bool:
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "claw_config.json",
            )
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    return bool(json.load(f).get("adaptive_enabled", True))
        except Exception:
            pass
        return True

    def _save_adaptive_enabled(self, enabled: bool):
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "claw_config.json",
            )
            data = {}
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    data = json.load(f)
            data["adaptive_enabled"] = enabled
            with open(config_path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _get_claw_config(self) -> dict:
        """读取完整夹爪配置"""
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "claw_config.json",
            )
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"close_angle": 25.0, "ref_area": 30000, "min_angle": 15.0, "max_angle": 45.0}

    def _get_claw_angle(self) -> float:
        """获取当前闭合角度（优先从工具栏输入框读取）"""
        if hasattr(self, 'claw_angle_spin'):
            return float(self.claw_angle_spin.value())
        return self._load_claw_close_angle()

    def _record_grasp_position(self, u: float, v: float):
        """记录抓取位置的像素坐标和世界坐标（用于放回原位）"""
        self._last_grasp_position = {"pixel": (u, v), "world": None}
        # 尝试计算世界坐标
        if self._vision_manager and self._vision_manager.is_initialized:
            info = self._vision_manager.compute_pixel_info(u, v)
            if info:
                self._last_grasp_position["world"] = info["world"]
                self.log_message.emit("info", f"记录抓取位置: 像素({u:.0f},{v:.0f}) 世界{info['world']}")

    def _compute_adaptive_angle(self, bbox: list) -> float:
        """根据检测框面积计算自适应闭合角度"""
        cfg = self._get_claw_config()
        ref_area = cfg.get("ref_area", 30000)
        min_angle = cfg.get("min_angle", 15.0)
        max_angle = cfg.get("max_angle", 45.0)

        x1, y1, x2, y2 = bbox
        area = max(1, (x2 - x1) * (y2 - y1))
        ratio = min(1.0, area / ref_area)
        angle = min_angle + ratio * (max_angle - min_angle)
        return round(angle, 1)

    def _get_effective_angle(self) -> float:
        """获取实际使用的闭合角度（自适应或固定）"""
        if self._adaptive_enabled and self._current_grasp_box:
            bbox = self._current_grasp_box.get("bbox")
            if bbox:
                angle = self._compute_adaptive_angle(bbox)
                self.log_message.emit("info", f"自适应角度: {angle}° (检测框面积={int((bbox[2]-bbox[0])*(bbox[3]-bbox[1]))})")
                return angle
        return self._get_claw_angle()

    # ---- 拍照位置 ----

    def _on_photo_position(self):
        if not self._motor_manager or not self._motor_manager.is_connected:
            self.log_message.emit("error", "电机未连接，无法移动到拍照位置")
            return
        try:
            # 示教式拍照位：优先走"记录拍照位"保存的姿态；未记录时回退为
            # 「其他关节 0° + 关节5 = j5_deg」（config/photo_position.json）。
            targets = self._motor_manager.go_to_photo_position(speed=self._get_speed())
            if targets is not None:
                if self._motor_manager.has_photo_position():
                    self.log_message.emit("info", f"运动到拍照位置(示教): {targets}")
                else:
                    self.log_message.emit("info", "运动到拍照位置(默认): 其他关节归零，关节5 → j5_deg")
        except Exception as e:
            self.log_message.emit("error", f"运动到拍照位置失败: {e}")

    def _on_record_photo_position(self):
        """示教：把当前姿态记录为拍照位"""
        if not self._motor_manager or not self._motor_manager.is_connected:
            self.log_message.emit("error", "电机未连接，无法记录拍照位")
            return
        try:
            joints = self._motor_manager.record_photo_position()
            self.log_message.emit("info", f"拍照位已记录: {joints}")
            QMessageBox.information(
                self, "拍照位已记录",
                "已把当前姿态记录为拍照位置。\n"
                "之后\"拍照位置\"按钮与抓取流程回位都会走到此姿态。",
            )
        except Exception as e:
            self.log_message.emit("error", f"记录拍照位失败: {e}")

    # ---- 自然语言指令 ----

    # 预设动作关键词 → (动作名, 执行方法名)
    _PRESET_ACTIONS = {
        "回零": "go_zero",
        "初始位置": "go_zero",
        "初始": "go_zero",
        "归零": "go_zero",
        "零点": "go_zero",
        "拍照位置": "go_photo",
        "拍照": "go_photo",
        "点头": "nod",
        "摇头": "shake",
        "起床": "wake",
        "闭合夹爪": "claw_close",
        "张开夹爪": "claw_open",
        "松开夹爪": "claw_open",
    }

    # ASR 容错同义词（模糊匹配）
    _ASR_FUZZY_MAP = {
        "假爪": "夹爪",
        "家爪": "夹爪",
        "甲找": "夹爪",
        "甲爪": "夹爪",
        "夹着": "夹爪",
        "夹子": "夹爪",
        "家长": "夹爪",
        "价爪": "夹爪",
    }

    def _is_vision_instruction(self, text: str) -> bool:
        """判断指令是否与视觉相关（描述类或动作类）"""
        return any(kw in text for kw in _VISION_KEYWORDS) or any(kw in text for kw in _ACTION_KEYWORDS)

    def _is_grasp_instruction(self, text: str) -> bool:
        """判断指令是否为抓取类动作"""
        return any(kw in text for kw in _ACTION_KEYWORDS)

    def _is_pure_grasp_instruction(self, text: str) -> bool:
        """判断是否为纯抓取指令（只有动作，没有视觉描述需求）"""
        normalized = self._normalize_asr_text(text)
        has_action = any(kw in normalized for kw in _ACTION_KEYWORDS)
        has_vision = any(kw in normalized for kw in _VISION_KEYWORDS if kw not in _ACTION_KEYWORDS)
        # 纯抓取：有动作关键词，没有视觉描述关键词
        return has_action and not has_vision

    def _normalize_asr_text(self, text: str) -> str:
        """ASR 容错：替换常见的误听词"""
        for wrong, correct in self._ASR_FUZZY_MAP.items():
            text = text.replace(wrong, correct)
        return text

    def _is_preset_action(self, text: str) -> Optional[str]:
        """检查指令是否包含预设动作关键词（含 ASR 容错）"""
        normalized = self._normalize_asr_text(text)
        for kw, action in self._PRESET_ACTIONS.items():
            if kw in normalized:
                return action
        return None

    def _parse_action_sequence(self, text: str) -> list:
        """从指令中解析连续动作序列，返回 action 列表"""
        import re
        normalized = self._normalize_asr_text(text)

        # 分隔符：然后、接着、再、之后、最后、和、，
        parts = re.split(r'[，,;；然后接着再之后最后]+', normalized)

        actions = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 去掉"先"、"然后"等前缀
            part = re.sub(r'^(先|然后|接着|再|之后|最后)\s*', '', part).strip()
            if not part:
                continue
            action = self._is_preset_action(part)
            if action:
                actions.append(action)

        return actions

    def _execute_action_sequence(self, instruction: str, actions: list):
        """执行连续动作序列"""
        if not self._motor_manager or not self._motor_manager.is_connected:
            self.log_message.emit("error", "电机未连接，无法执行动作")
            self._speak_if_voice("电机未连接，无法执行动作")
            return

        action_names = {
            "go_zero": "回零", "go_photo": "拍照位置",
            "nod": "点头", "shake": "摇头", "wake": "起床",
        }
        desc = " → ".join(action_names.get(a, a) for a in actions)
        self.log_message.emit("info", f"连续动作: {desc}")
        self._set_instruction_running(f"连续动作: {desc}")

        # 在线程中串行执行
        speed = self._get_speed()

        class _SequenceWorker(QThread):
            finished = pyqtSignal(str)
            error = pyqtSignal(str)

            def __init__(self, mm, actions, speed):
                super().__init__()
                self._mm = mm
                self._actions = actions
                self._speed = speed

            def run(self):
                try:
                    import time
                    for i, action in enumerate(self._actions):
                        if action == "go_zero":
                            self._mm.go_to_custom_zero_sequential(speed=self._speed, last_joint=5)
                            time.sleep(6)
                        elif action == "go_photo":
                            self._mm.go_to_photo_position(speed=self._speed)
                            time.sleep(6)
                        elif action in ("nod", "shake"):
                            presets = self._load_presets_static()
                            cn = {"nod": "点头", "shake": "摇头"}.get(action, action)
                            if cn in presets:
                                joints_list = presets[cn]["joints"]
                                dur = presets[cn].get("duration", 2.0)
                                if joints_list and isinstance(joints_list[0], (int, float)):
                                    joints_list = [joints_list]
                                for joints in joints_list:
                                    targets = {mid: angle for mid, angle in enumerate(joints, 1) if mid in self._mm.motors}
                                    if targets:
                                        self._mm.sync_move_positions(targets, speed=self._speed)
                                    time.sleep(dur)
                    self.finished.emit(f"连续动作完成: {desc}")
                except Exception as e:
                    self.error.emit(f"连续动作失败: {e}")

            @staticmethod
            def _load_presets_static():
                try:
                    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    path = os.path.join(base, "config", "embodied_config", "preset_actions.json")
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            return json.load(f)
                except Exception:
                    pass
                return {}

        worker = _SequenceWorker(self._motor_manager, actions, speed)
        worker.finished.connect(self._finish_preset)
        worker.error.connect(self._on_instruction_error)
        self._instruction_worker = worker
        worker.start()

    def _on_execute_instruction(self):
        raw = self.instruction_input.text().strip()
        if not raw:
            return
        # ASR 容错：替换常见误听词
        instruction = self._normalize_asr_text(raw)
        # 去掉开头的连接词（"再把..." → "把..."）
        import re as _re
        instruction = _re.sub(r'^(然后|接着|再|之后)\s*', '', instruction).strip()
        if instruction != raw:
            self.instruction_input.setText(instruction)
            self.log_message.emit("info", f"ASR 纠错: '{raw}' → '{instruction}'")
        # 安全清理：如果 worker 已完成但引用未清除，强制清理
        if self._instruction_worker is not None:
            if self._instruction_worker.isFinished():
                self._instruction_worker = None
            else:
                return  # 真正在执行中，跳过

        # 1. 放回原位指令（最高优先级，避免被误判为连续指令）
        put_back_keywords = ["放回原位", "放回去", "放回原来", "归位", "送回去", "放还原", "放回原位置"]
        if any(kw in instruction for kw in put_back_keywords):
            self._execute_put_back()
            return

        # 2. 连续抓取+放置指令（如"抓取苹果，然后放到盒子里"）
        if self._is_compound_grasp_instruction(instruction) and self._frame is not None:
            self._execute_compound_grasp(instruction)
            return

        # 3. 检查连续动作序列
        sequence = self._parse_action_sequence(instruction)
        if sequence and len(sequence) > 1:
            self._execute_action_sequence(instruction, sequence)
            return

        # 4. 检查单个预设动作
        preset = self._is_preset_action(instruction)
        if preset is not None:
            self._execute_preset_action(instruction, preset)
            return

        # 5. 纯抓取指令 → 直接检测+抓取，不走 LLM
        if self._is_pure_grasp_instruction(instruction) and self._frame is not None:
            self._execute_detect_and_grasp(instruction)
            return

        # 3. 视觉指令（含描述需求）→ LLM + 检测
        if self._is_vision_instruction(instruction) and self._frame is not None:
            self._execute_vision_instruction(instruction)
            return

        # 3. 普通对话 → LLM 回答
        self._execute_chat_instruction(instruction)

    def _execute_preset_action(self, instruction: str, action: str):
        """执行预设动作"""
        if not self._motor_manager or not self._motor_manager.is_connected:
            self.log_message.emit("error", "电机未连接，无法执行动作")
            self._speak_if_voice("电机未连接，无法执行动作")
            return

        self._set_instruction_running(instruction)
        self.log_message.emit("info", f"执行预设动作: {action}")

        try:
            if action == "go_zero":
                self._motor_manager.go_to_custom_zero_sequential(speed=self._get_speed(), last_joint=5)
                self.log_message.emit("info", "已回到零点位置")
                self._finish_preset("好的，已回到零点位置")

            elif action == "go_photo":
                self._motor_manager.go_to_photo_position(speed=self._get_speed())
                self.log_message.emit("info", "已回到拍照位置")
                self._finish_preset("好的，已回到拍照位置")

            elif action in ("nod", "shake", "wake"):
                presets = self._load_presets()
                name_map = {"nod": "点头", "shake": "摇头", "wake": "起床"}
                cn_name = name_map.get(action, action)
                if cn_name in presets:
                    self._execute_preset_sequence(presets[cn_name], cn_name)
                else:
                    self.log_message.emit("error", f"预设动作 '{cn_name}' 未找到")
                    self._finish_preset(f"抱歉，预设动作{cn_name}未找到")

            elif action in ("claw_close", "claw_open"):
                self._execute_claw_action(instruction, action)

        except Exception as e:
            self.log_message.emit("error", f"执行预设动作失败: {e}")
            self._finish_preset(f"动作执行失败: {e}")

    def _execute_preset_sequence(self, preset: dict, name: str):
        """执行预设动作序列（在线程中运行）"""
        joints_list = preset["joints"]
        duration = preset.get("duration", 2.0)

        # 标准化为列表格式
        if joints_list and isinstance(joints_list[0], (int, float)):
            joints_list = [joints_list]

        # 检查关节限位
        limits = {
            1: (-90, 90), 2: (-20, 90), 3: (-90, 35),
            4: (-90, 90), 5: (-90, 90), 6: (-90, 90),
        }
        for step_idx, joints in enumerate(joints_list):
            for i, angle in enumerate(joints):
                jid = i + 1
                if jid in limits:
                    lo, hi = limits[jid]
                    if not (lo <= angle <= hi):
                        self.log_message.emit("error", f"预设动作 '{name}' 第{step_idx+1}步: 关节{jid}={angle}° 超出限位[{lo},{hi}]")
                        self._finish_preset(f"动作{name}参数超限，已中止")
                        return

        # 在线程中执行
        worker = PresetActionWorker(self._motor_manager, joints_list, duration, speed=self._get_speed())
        worker.finished.connect(lambda: self._finish_preset(f"好的，{name}动作已完成"))
        worker.error.connect(lambda msg: self._on_instruction_error(msg))
        self._instruction_worker = worker
        worker.start()

    def _execute_claw_action(self, instruction: str, action: str):
        """执行夹爪动作（语音控制）"""
        if not self._claw or not self._claw.is_connected:
            self.log_message.emit("error", "夹爪未连接，请先在机械臂控制页面连接夹爪")
            self._finish_preset("夹爪未连接")
            return

        if action == "claw_close":
            angle = self._parse_claw_angle(instruction)
            if self._claw.set_angle(angle):
                self.log_message.emit("info", f"夹爪闭合: {angle}°")
                self._finish_preset(f"好的，夹爪已闭合到{angle}度")
            else:
                self._finish_preset("夹爪控制失败")
        elif action == "claw_open":
            if self._claw.set_angle(90):
                self.log_message.emit("info", "夹爪张开: 90°")
                self._finish_preset("好的，夹爪已张开")
            else:
                self._finish_preset("夹爪控制失败")

    def _parse_claw_angle(self, text: str) -> float:
        """从语音指令中解析夹爪角度，如'闭合夹爪到30度'→30，并同步更新输入框和配置"""
        import re
        match = re.search(r'到(\d+)度', text)
        if match:
            angle = float(match.group(1))
            angle = max(0, min(90, angle))
            # 同步更新工具栏输入框和配置文件
            if hasattr(self, 'claw_angle_spin'):
                self.claw_angle_spin.setValue(int(angle))
            self._save_claw_close_angle(angle)
            return angle
        return self._get_claw_angle()

    def _finish_preset(self, response: str):
        """预设动作完成"""
        self._instruction_worker = None
        self._reset_instruction_ui()
        self.log_message.emit("info", response)
        try:
            self._speak_if_voice(response)
        except Exception:
            pass
        QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _execute_chat_instruction(self, instruction: str):
        """普通对话 → LLM 回答"""
        llm_cfg = self._vision_manager._load_llm_config() if self._vision_manager else None
        if llm_cfg is None:
            self.log_message.emit("error", "AI 配置未加载")
            return

        self._set_instruction_running(instruction)
        self.log_message.emit("info", f"对话: {instruction}")

        worker = _ChatWorker(instruction, llm_cfg, self._frame)
        worker.finished.connect(self._on_chat_done)
        worker.error.connect(self._on_instruction_error)
        self._instruction_worker = worker
        worker.start()

    def _on_chat_done(self, response: str):
        """LLM 对话完成"""
        self._instruction_worker = None
        self._reset_instruction_ui()
        self.log_message.emit("info", f"AI 回复: {response}")
        try:
            self._speak_if_voice(response)
        except Exception:
            pass
        if not self._voice_active:
            QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _load_presets(self) -> dict:
        """加载预设动作配置"""
        try:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(base, "config", "embodied_config", "preset_actions.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _speak_if_voice(self, text: str):
        """语音模式下通过 TTS 播报"""
        if self._voice_manager and self._voice_active:
            self._voice_manager.provide_response(text)

    def _execute_put_back(self):
        """放回原位：移动到抓取时记录的位置，松开夹爪，回拍照位置"""
        if self._last_grasp_position is None:
            self.log_message.emit("error", "没有记录抓取位置，无法放回原位")
            self._speak_if_voice("没有记录抓取位置")
            self._reset_instruction_ui()
            return

        pixel = self._last_grasp_position.get("pixel")
        if pixel is None:
            self.log_message.emit("error", "抓取位置数据不完整")
            return

        self.log_message.emit("info", f"放回原位: 像素({pixel[0]:.0f}, {pixel[1]:.0f})")
        self._set_instruction_running("放回原位")

        # 使用视觉 SDK 移动到原位
        if not self._ensure_vision_sdk():
            self._reset_instruction_ui()
            return

        u, v = pixel
        worker = self._vision_manager.grasp_at_pixel_async(u, v, speed=self._get_speed())
        if worker is None:
            self._reset_instruction_ui()
            self.log_message.emit("error", "放回失败: 无法创建工作任务")
            return

        self._grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        worker.finished.connect(self._on_put_back_arrived)
        worker.error.connect(self._on_instruction_error)
        worker.start()

    def _on_put_back_arrived(self, ok):
        """到达原位 → 松开夹爪 → 回拍照位置"""
        self._grasp_worker = None
        if not ok:
            self._reset_instruction_ui()
            self.log_message.emit("error", "移动到原位失败")
            return

        self.log_message.emit("info", "已到达原位，松开夹爪...")

        # 松开夹爪
        if self._claw and self._claw.is_connected:
            self._claw.set_angle(90)

        # 延迟后回拍照位置
        QTimer.singleShot(1500, self._put_back_return_photo)

    def _put_back_return_photo(self):
        """放回后回到拍照位置"""
        self._reopen_camera()  # 恢复摄像头画面
        if self._motor_manager and self._motor_manager.is_connected:
            self._motor_manager.go_to_photo_position(speed=self._get_speed())
        self._last_grasp_position = None
        self._reset_instruction_ui()
        self.log_message.emit("info", "已放回原位")
        self._speak_if_voice("好的，已放回原位")
        QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _is_compound_grasp_instruction(self, text: str) -> bool:
        """判断是否为连续抓取+放置指令（如"抓取苹果，然后放回原位"）"""
        import re
        normalized = self._normalize_asr_text(text)

        # 查找连接词位置
        connector_match = re.search(r'然后|接着|再|之后', normalized)
        if not connector_match:
            return False

        before = normalized[:connector_match.start()]
        after = normalized[connector_match.end():]

        # 连接词前面必须有抓取关键词，后面必须有放置/放回关键词
        has_grasp_before = any(kw in before for kw in _ACTION_KEYWORDS)
        put_back_keywords = ["放回原位", "放回去", "放回原来", "归位", "送回去"]
        place_keywords = ["放到", "放进", "放入", "装进", "装入"]
        has_action_after = any(kw in after for kw in put_back_keywords + place_keywords)

        return has_grasp_before and has_action_after

    def _execute_compound_grasp(self, instruction: str):
        """执行连续抓取+放置指令"""
        # 确保视觉 SDK 已初始化
        if not self._ensure_vision_sdk():
            return

        import re
        normalized = self._normalize_asr_text(instruction)

        # 去掉开头的连接词（"再把..." → "把..."）
        normalized = re.sub(r'^(然后|接着|再|之后)\s*', '', normalized).strip()

        # 拆分指令
        parts = re.split(r'然后|接着|再|之后', normalized)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) < 2:
            self.log_message.emit("error", "无法解析连续指令")
            self._speak_if_voice("无法解析连续指令")
            self._reset_instruction_ui()
            return

        grasp_part = parts[0]
        action_part = parts[1]

        # 判断第二步是放回原位还是放到容器
        put_back_keywords = ["放回原位", "放回去", "放回原来", "归位", "送回去"]
        is_put_back = any(kw in action_part for kw in put_back_keywords)

        self.log_message.emit("info", f"连续指令: [{grasp_part}] → [{action_part}]")
        self._set_instruction_running(f"连续: {grasp_part} → {action_part}")

        # 提取抓取目标
        target_text = grasp_part
        for kw in _ACTION_KEYWORDS:
            target_text = target_text.replace(kw, "")
        target_text = target_text.strip()

        # 保存第二步信息
        self._compound_action = "put_back" if is_put_back else "place"
        self._compound_place_text = action_part if not is_put_back else ""

        # 运行 AI 检测
        worker = self._vision_manager.detect_objects_async(self._frame.copy(), mode="llm")
        if worker is None:
            self._reset_instruction_ui()
            self.log_message.emit("error", "检测启动失败")
            return

        # 保存第二步信息和当前帧（用于后续放回计算）
        self._pending_grasp_text = target_text
        self._compound_frame = self._frame.copy()  # 记录检测时的帧
        self._detect_then_grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        worker.finished.connect(self._on_compound_detect_done)
        worker.error.connect(self._on_instruction_error)
        worker.start()

    def _on_compound_detect_done(self, boxes):
        """连续指令：检测完成 → 抓取 → 等待 → 放置/放回"""
        worker = self._detect_then_grasp_worker
        self._detect_then_grasp_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)

        # 不重开摄像头，保持检测时的画面（避免拍照位置导致坐标偏移）

        if not boxes:
            self._reset_instruction_ui()
            self.log_message.emit("error", "未检测到物体")
            return

        self._display_detection_boxes(boxes)

        # 匹配目标
        target_text = getattr(self, '_pending_grasp_text', '')
        target_box = self._find_box_by_name(boxes, target_text)
        if not target_box and boxes:
            target_box = boxes[0]

        if not target_box:
            self._reset_instruction_ui()
            self.log_message.emit("error", f"未找到目标: {target_text}")
            return

        # 记录抓取位置
        x1, y1, x2, y2 = target_box["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        self._record_grasp_position(cx, cy)
        self._current_grasp_box = target_box

        # 如果是放置到容器，需要记录容器位置
        action = getattr(self, '_compound_action', 'put_back')
        if action == "place":
            place_text = getattr(self, '_compound_place_text', '')
            container_box = self._find_box_by_name(boxes, place_text)
            if container_box:
                self._compound_container_box = container_box
            else:
                self.log_message.emit("warn", f"未找到容器: {place_text}，改为放回原位")
                self._compound_action = "put_back"

        # 启动抓取
        worker = self._vision_manager.grasp_at_pixel_async(cx, cy, speed=self._get_speed())
        if worker is None:
            self._reset_instruction_ui()
            return

        self._grasp_worker = worker
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        worker.finished.connect(self._on_compound_grasp_done)
        worker.error.connect(self._on_instruction_error)
        worker.start()

    def _on_compound_grasp_done(self, ok):
        """连续指令：抓取完成 → 启动串行流程（闭合→回位→放回→松开→回位）"""
        self._grasp_worker = None
        if not ok:
            self._reopen_camera()
            self._reset_instruction_ui()
            self.log_message.emit("error", "抓取失败")
            return

        # 获取放回目标
        action = getattr(self, '_compound_action', 'put_back')
        if action == "put_back":
            target_world = self._last_grasp_position.get("world") if self._last_grasp_position else None
            if not target_world:
                self._reopen_camera()
                self._reset_instruction_ui()
                self.log_message.emit("error", "无放回位置记录")
                return
        else:
            container_box = getattr(self, '_compound_container_box', None)
            if container_box:
                cx1, cy1, cx2, cy2 = container_box["bbox"]
                ccx, ccy = (cx1 + cx2) / 2, (cy1 + cy2) / 2
                info = self._vision_manager.compute_pixel_info(ccx, ccy)
                target_world = info["world"] if info else None
            else:
                target_world = self._last_grasp_position.get("world") if self._last_grasp_position else None
            if not target_world:
                self._reopen_camera()
                self._reset_instruction_ui()
                self.log_message.emit("error", "无放置位置")
                return

        close_angle = self._get_effective_angle()

        # 启动串行流程 Worker
        worker = _CompoundSequenceWorker(
            self._motor_manager, self._claw,
            target_world, close_angle, self._get_speed(),
        )
        worker.finished.connect(self._on_compound_sequence_done)
        worker.error.connect(self._on_instruction_error)
        worker.progress.connect(lambda msg: self.inst_status.setText(msg[:50]))
        self._instruction_worker = worker
        worker.start()

    def _on_compound_sequence_done(self, msg):
        """串行流程完成"""
        self._instruction_worker = None
        self._reopen_camera()
        self._reset_instruction_ui()
        self._last_grasp_position = None
        self.log_message.emit("info", msg)
        self._speak_if_voice(msg)
        QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _execute_compound_step2(self):
        """连续指令的第二步：放回原位或放到容器"""
        action = getattr(self, '_compound_action', 'put_back')

        if action == "put_back":
            # 放回原位：使用记录的世界坐标（不重新计算，避免拍照位置导致 Z 偏移）
            if self._last_grasp_position and self._last_grasp_position.get("world"):
                world = self._last_grasp_position["world"]
                self.log_message.emit("info", f"第二步: 放回原位 {world}")
                self._move_to_world_and_release(world)
            elif self._last_grasp_position and self._last_grasp_position.get("pixel"):
                # 没有世界坐标，用像素坐标（兼容旧记录）
                pixel = self._last_grasp_position["pixel"]
                self.log_message.emit("info", "第二步: 放回原位（像素坐标）")
                worker = self._vision_manager.grasp_at_pixel_async(pixel[0], pixel[1], speed=self._get_speed())
                if worker:
                    self._grasp_worker = worker
                    worker.finished.connect(self._on_compound_place_done)
                    worker.error.connect(self._on_instruction_error)
                    worker.start()
                else:
                    self._compound_finish("放回失败")
            else:
                self._compound_finish("无抓取位置记录")

        elif action == "place":
            # 放到容器
            container_box = getattr(self, '_compound_container_box', None)
            if container_box:
                cx1, cy1, cx2, cy2 = container_box["bbox"]
                ccx, ccy = (cx1 + cx2) / 2, (cy1 + cy2) / 2
                self.log_message.emit("info", f"第二步: 移动到 {container_box['name']}")
                # 计算容器世界坐标并抬高
                container_info = self._vision_manager.compute_pixel_info(ccx, ccy)
                if container_info:
                    from Horizon_Core import gateway as horizon_gateway
                    embodied_internal = horizon_gateway.get_embodied_internal_module()
                    embodied_internal._set_motion_params(
                        max_speed=self._get_speed(),
                        acceleration=self._get_speed(),
                        deceleration=self._get_speed(),
                    )
                    wx, wy, wz = container_info["world"]
                    yaw, pitch, roll = container_info["euler_angles"]
                    safe_z = wz + 150.0
                    embodied_func = horizon_gateway.get_embodied_module()
                    ok = embodied_func.c_a_p(
                        [float(wx), float(wy), float(safe_z)],
                        [float(yaw), float(pitch), float(roll)]
                    )
                    if ok:
                        QTimer.singleShot(1000, self._compound_release_and_return)
                    else:
                        self._compound_finish("移动失败")
                else:
                    self._compound_finish("无法计算容器位置")
            else:
                self._compound_finish("未找到容器")

    def _on_compound_place_done(self, ok):
        """连续指令：到达放置位置 → 松开夹爪 → 回位"""
        self._grasp_worker = None
        if not ok:
            self._compound_finish("移动到放置位置失败")
            return
        self.log_message.emit("info", "已到达放置位置，松开夹爪...")
        if self._claw and self._claw.is_connected:
            self._claw.set_angle(90)
        QTimer.singleShot(1500, lambda: self._compound_finish("已完成"))

    def _move_to_world_and_release(self, world: tuple):
        """移动到世界坐标并松开夹爪（用于放回原位）"""
        try:
            from Horizon_Core import gateway as horizon_gateway
            embodied_internal = horizon_gateway.get_embodied_internal_module()
            embodied_internal._set_motion_params(
                max_speed=self._get_speed(),
                acceleration=self._get_speed(),
                deceleration=self._get_speed(),
            )

            # 获取当前姿态（用于保持末端朝向）
            current_pose = embodied_internal._get_current_arm_pose()
            yaw, pitch, roll = 0.0, 0.0, 180.0
            if current_pose and len(current_pose) >= 6:
                yaw, pitch, roll = current_pose[3], current_pose[4], current_pose[5]

            # 安全检查：Z 不能太极端
            x, y, z = world
            if z < 0 or z > 500:
                self.log_message.emit("warn", f"Z 值异常 ({z:.0f}mm)，跳过放回")
                self._compound_finish("Z 值异常，已跳过")
                return

            embodied_func = horizon_gateway.get_embodied_module()
            ok = embodied_func.c_a_p(
                [float(x), float(y), float(z)],
                [float(yaw), float(pitch), float(roll)]
            )
            if ok:
                QTimer.singleShot(1500, self._compound_release_and_return)
            else:
                self._compound_finish("移动失败")
        except Exception as e:
            self._compound_finish(f"移动失败: {e}")

    def _compound_release_and_return(self):
        """松开夹爪并回到拍照位置"""
        if self._claw and self._claw.is_connected:
            self._claw.set_angle(90)
        QTimer.singleShot(1500, lambda: self._compound_finish("已完成"))

    def _compound_finish(self, msg: str):
        """连续指令完成"""
        self._reopen_camera()  # 恢复摄像头
        if self._motor_manager and self._motor_manager.is_connected:
            self._motor_manager.go_to_photo_position(speed=self._get_speed())
        self._last_grasp_position = None
        self._reset_instruction_ui()
        self.log_message.emit("info", msg)
        self._speak_if_voice(msg)
        QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _execute_detect_and_grasp(self, instruction: str):
        """纯抓取指令：直接检测+抓取，不走 LLM（不阻塞语音会话）"""
        if not self._ensure_vision_sdk():
            return
        self.log_message.emit("info", f"抓取指令: {instruction}")

        # 从指令中提取目标关键词
        normalized = self._normalize_asr_text(instruction)
        # 去掉动作关键词，保留物体描述
        target_text = normalized
        for kw in _ACTION_KEYWORDS:
            target_text = target_text.replace(kw, "")
        target_text = target_text.strip()
        if not target_text:
            target_text = instruction  # fallback

        # 纯抓取指令强制用 AI 多模态检测（比 YOLO 更准确）
        worker = self._vision_manager.detect_objects_async(self._frame.copy(), mode="llm")
        if worker is None:
            self._reset_instruction_ui()
            self.log_message.emit("error", "检测启动失败")
            return

        # 存储目标关键词，检测完成后匹配
        self._pending_grasp_text = target_text
        self._detect_then_grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        worker.finished.connect(self._on_detect_for_grasp_done)
        worker.error.connect(self._on_instruction_error)
        worker.start()

    def _on_detect_for_grasp_done(self, boxes):
        """检测完成 → 匹配目标 → 抓取"""
        worker = self._detect_then_grasp_worker
        self._detect_then_grasp_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)

        self._reopen_camera()

        if not boxes:
            self.log_message.emit("error", "未检测到物体")
            self._speak_if_voice("没有检测到物体")
            return

        # 在检测结果中匹配目标
        target_text = getattr(self, '_pending_grasp_text', '')
        target_box = self._find_box_by_name(boxes, target_text)

        if target_box:
            self.log_message.emit("info", f"匹配到目标: {target_box['name']}")
            # 显示检测框
            self._display_detection_boxes(boxes)
            # 执行抓取
            self._current_grasp_box = target_box
            x1, y1, x2, y2 = target_box["bbox"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            self._record_grasp_position(cx, cy)  # 记录原始位置

            worker = self._vision_manager.grasp_at_pixel_async(cx, cy, speed=self._get_speed())
            if worker is None:
                self._reset_instruction_ui()
                self.log_message.emit("error", "抓取失败")
                return

            self._grasp_worker = worker
            self._detection_boxes = []
            self.preview_label.clear_detection_boxes()
            worker.finished.connect(self._on_grasp_done)
            worker.error.connect(self._on_grasp_error)
            worker.start()
        else:
            # 没有精确匹配，抓取第一个物体
            self.log_message.emit("info", f"未精确匹配'{target_text}'，抓取第一个物体")
            self._display_detection_boxes(boxes)
            self._auto_grasp_first(boxes)

    def _execute_vision_instruction(self, instruction: str):
        """执行视觉问答指令：LLM 描述 + 检测标框"""
        llm_cfg = self._vision_manager._load_llm_config() if self._vision_manager else None
        if llm_cfg is None:
            self.log_message.emit("error", "AI 配置未加载，请检查 config/llm_config.json")
            return

        self._set_instruction_running(instruction)
        self.log_message.emit("info", f"视觉指令: {instruction}")

        # 标记是否为抓取类指令，完成后自动执行抓取
        self._pending_grasp = self._is_grasp_instruction(instruction)

        worker = _VisionInstructionWorker(self._frame.copy(), instruction, llm_cfg)
        worker.finished.connect(self._on_vision_instruction_done)
        worker.error.connect(self._on_instruction_error)
        self._instruction_worker = worker
        worker.start()

    def _set_instruction_running(self, instruction: str):
        """设置指令执行中的 UI 状态"""
        self.btn_execute.setEnabled(False)
        self.btn_inst_stop.setEnabled(True)
        self.instruction_input.setEnabled(False)
        self.inst_status.setText(f"执行中: {instruction[:30]}…")
        self.inst_status.setStyleSheet("color: #3b82f6; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")
        self.log_message.emit("info", f"具身指令: {instruction}")

    def _reset_instruction_ui(self):
        """重置指令 UI 到可交互状态（安全清理）"""
        self.btn_execute.setEnabled(True)
        self.btn_inst_stop.setEnabled(False)
        self.instruction_input.setEnabled(True)
        self.instruction_input.clear()
        self.inst_status.setText("✓ 完成")
        self.inst_status.setStyleSheet("color: #22c55e; font-size: 12px; border: none; padding-left: 4px;")

    def _on_instruction_done(self, instruction):
        self._instruction_worker = None
        self._reset_instruction_ui()
        self.log_message.emit("info", f"指令完成: {instruction}")
        try:
            if self._voice_manager and self._voice_active:
                self._voice_manager.provide_response(f"指令已完成: {instruction}")
        except Exception:
            pass
        if not self._voice_active:
            QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _on_vision_instruction_done(self, llm_response: str, boxes: list):
        """视觉指令完成：显示 LLM 回复 + 检测标框 + 自动抓取/放置"""
        worker = self._instruction_worker
        self._instruction_worker = None
        if worker and worker.isRunning():
            worker.wait(3000)

        self.btn_execute.setEnabled(True)
        self.btn_inst_stop.setEnabled(False)
        self.instruction_input.setEnabled(True)
        self.instruction_input.clear()

        # 显示 LLM 的文字回复
        self.log_message.emit("info", f"AI 回复: {llm_response}")
        self.inst_status.setText("✓ 完成")
        self.inst_status.setStyleSheet("color: #22c55e; font-size: 12px; border: none; padding-left: 4px;")

        # 检查是否有动作计划（放置指令）
        action_plan = self._parse_action_plan(llm_response)
        if action_plan:
            if boxes:
                self._display_detection_boxes(boxes)
                self._execute_pick_and_place(action_plan, boxes)
            else:
                self.log_message.emit("error", "动作计划需要检测框，但 LLM 未返回检测结果")
            return

        # 检测标框
        if boxes:
            self._display_detection_boxes(boxes)

            # 如果是抓取指令，自动抓取第一个检测到的物体
            if getattr(self, '_pending_grasp', False):
                self._pending_grasp = False
                self._auto_grasp_first(boxes)
        else:
            self.log_message.emit("info", "未检测到物体")
            self._pending_grasp = False

        # 语音模式：提供 LLM 回复给语音会话（TTS 会播放）
        if self._voice_manager and self._voice_active:
            self._voice_manager.provide_response(llm_response)
        else:
            QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _auto_grasp_first(self, boxes: list):
        """自动抓取第一个检测到的物体"""
        if not boxes:
            return
        if not self._ensure_vision_sdk():
            return
        if self._grasp_worker is not None:
            self.log_message.emit("warn", "抓取任务正在执行中，跳过自动抓取")
            return

        box = boxes[0]
        x1, y1, x2, y2 = box["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        self._current_grasp_box = box  # 用于自适应角度计算
        self._record_grasp_position(cx, cy)  # 记录原始位置
        self.log_message.emit("info", f"自动抓取: {box['name']} 中心({cx:.0f}, {cy:.0f})")

        worker = self._vision_manager.grasp_at_pixel_async(cx, cy, speed=self._get_speed())
        if worker is None:
            self.log_message.emit("error", "自动抓取失败: 无法创建工作任务")
            return

        self._grasp_worker = worker
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        worker.finished.connect(self._on_grasp_done)
        worker.error.connect(self._on_grasp_error)
        worker.start()

    # ---- 放置（Pick & Place） ----

    def _parse_action_plan(self, llm_response: str) -> Optional[dict]:
        """从 LLM 回复中解析动作计划"""
        import re
        match = re.search(r'ACTION_PLAN:\s*(\{[^}]+\})', llm_response)
        if not match:
            return None
        try:
            plan = json.loads(match.group(1))
            target = plan.get("target")
            container = plan.get("container")
            if target and container:
                self.log_message.emit("info", f"动作计划: 抓取 [{target}] → 放入 [{container}]")
                return plan
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def _find_box_by_name(self, boxes: list, name: str) -> Optional[dict]:
        """在检测结果中按名称模糊匹配（忽略标点）"""
        import re
        if not name:
            return None
        # 去掉标点符号再比较
        clean = lambda s: re.sub(r'[，,。.、；;：:！!？?\s]+', '', s).lower()
        name_clean = clean(name)
        if not name_clean:
            return None

        # 精确匹配（含子串）
        for box in boxes:
            box_clean = clean(box["name"])
            if name_clean in box_clean or box_clean in name_clean:
                return box

        # 关键词匹配
        for box in boxes:
            box_words = set(re.findall(r'[一-鿿]+|[a-zA-Z]+', clean(box["name"])))
            query_words = set(re.findall(r'[一-鿿]+|[a-zA-Z]+', name_clean))
            if box_words & query_words:
                return box

        return None

    def _execute_pick_and_place(self, plan: dict, boxes: list):
        """执行抓取-放置流程"""
        target_name = plan.get("target", "")
        container_name = plan.get("container", "")

        # 确保视觉 SDK 已初始化
        if not self._ensure_vision_sdk():
            return

        # 查找目标物体
        target_box = self._find_box_by_name(boxes, target_name)
        if not target_box:
            self.log_message.emit("error", f"未找到目标物体: {target_name}")
            self._speak_if_voice(f"抱歉，没有找到{target_name}")
            return

        # 查找容器
        container_box = self._find_box_by_name(boxes, container_name)
        if not container_box:
            self.log_message.emit("error", f"未找到容器: {container_name}")
            self._speak_if_voice(f"抱歉，没有找到{container_name}")
            return

        self.log_message.emit("info",
            f"放置计划: {target_box['name']} → {container_box['name']}")

        # 启动多步执行线程
        self._set_instruction_running(f"放置: {target_name} → {container_name}")
        worker = _PickPlaceWorker(
            self._vision_manager, self._motor_manager, self._claw,
            target_box, container_box, self._get_speed(),
            self._get_effective_angle(),
        )
        worker.finished.connect(self._on_pick_place_done)
        worker.error.connect(self._on_instruction_error)
        worker.progress.connect(lambda msg: self.inst_status.setText(msg[:50]))
        self._instruction_worker = worker
        worker.start()

    def _on_pick_place_done(self, msg: str):
        """放置完成"""
        self._instruction_worker = None
        self._reset_instruction_ui()
        self.log_message.emit("info", msg)
        self._speak_if_voice(msg)
        if not self._voice_active:
            QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _on_instruction_error(self, msg):
        self._instruction_worker = None
        self._reset_instruction_ui()
        self.inst_status.setText(f"✗ 失败")
        self.inst_status.setStyleSheet("color: #ef4444; font-size: 12px; border: none; padding-left: 4px;")
        self.log_message.emit("error", f"指令失败: {msg}")
        try:
            if self._voice_manager and self._voice_active:
                self._voice_manager.provide_response(f"指令执行失败: {msg}")
        except Exception:
            pass
        QTimer.singleShot(3000, lambda: self.inst_status.setText(""))

    def _on_stop_instruction(self):
        if self._instruction_worker is not None:
            self._instruction_worker.terminate()
            self._instruction_worker.wait(3000)
            self._instruction_worker = None
        if self._embodied_sdk:
            try:
                self._embodied_sdk.emergency_stop()
            except Exception:
                pass
        self.btn_execute.setEnabled(True)
        self.btn_inst_stop.setEnabled(False)
        self.instruction_input.setEnabled(True)
        self.inst_status.setText("已停止")
        self.inst_status.setStyleSheet("color: #f59e0b; font-size: 12px; border: none; padding-left: 4px;")
        self.log_message.emit("warn", "指令已停止")

    # ---- 语音交互 ----

    def _init_voice_manager(self) -> bool:
        """懒加载语音管理器"""
        if self._voice_manager is not None:
            return True
        try:
            from gui.core.voice_manager import VoiceManager
            self._voice_manager = VoiceManager()
            self._voice_manager.asr_result.connect(self._on_voice_asr_result)
            self._voice_manager.asr_partial.connect(self._on_voice_asr_partial)
            self._voice_manager.tts_started.connect(self._on_voice_tts_started)
            self._voice_manager.tts_finished.connect(self._on_voice_tts_finished)
            self._voice_manager.error.connect(self._on_voice_error)
            self._voice_manager.state_changed.connect(self._on_voice_state_changed)
            self._voice_manager.session_started.connect(self._on_voice_session_started)
            self._voice_manager.session_ended.connect(self._on_voice_session_ended)
            self.log_message.emit("info", "语音模块初始化成功")
            return True
        except Exception as e:
            self.log_message.emit("error", f"语音模块初始化失败: {e}")
            return False

    def _on_mic_toggle(self):
        """切换语音模式（和喊'贾维斯'效果一样）"""
        if not self._init_voice_manager():
            return
        self._voice_manager.toggle()

    def _on_voice_state_changed(self, state: str):
        """根据 VoiceManager 状态更新 UI"""
        wake_word = self._voice_manager.config.wake_word if self._voice_manager else "贾维斯"

        if state == "idle":
            self._voice_active = False
            self.btn_mic.setText("🎤 语音")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #f59e0b; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #d97706; }"
            )
            self.inst_status.setText("")

        elif state == "waiting":
            self._voice_active = False
            self.btn_mic.setText("⏹ 停止监听")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #8b5cf6; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #7c3aed; }"
            )
            self.inst_status.setText(f"🎧 等待唤醒词「{wake_word}」...")
            self.inst_status.setStyleSheet("color: #8b5cf6; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")
            self.log_message.emit("info", f"语音待命中，喊「{wake_word}」唤醒")

        elif state == "active":
            self._voice_active = True
            self.btn_mic.setText("⏹ 停止监听")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #ef4444; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #dc2626; }"
            )
            self.inst_status.setText("🎙️ 请说话...")
            self.inst_status.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")

        elif state == "speaking":
            self._voice_active = True
            self.inst_status.setText("🔊 AI 正在说话...")
            self.inst_status.setStyleSheet("color: #3b82f6; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")

    def _on_voice_session_started(self):
        self.log_message.emit("info", "语音对话已激活")

    def _on_voice_session_ended(self):
        self.log_message.emit("info", "对话结束，回到待命状态")

    def _on_voice_asr_partial(self, text: str):
        """ASR 实时转写"""
        if self._instruction_worker is None:
            self.instruction_input.setText(text)
        self.inst_status.setText(f"🎙️ {text[:30]}...")
        self.inst_status.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")

    def _on_voice_asr_result(self, text: str):
        """ASR 最终识别结果 → 放入输入框并执行指令"""
        self.log_message.emit("info", f"语音识别: {text}")
        self.instruction_input.setText(text)
        self._on_execute_instruction()

    def _on_voice_tts_started(self):
        self.inst_status.setText("🔊 AI 正在说话...")
        self.inst_status.setStyleSheet("color: #3b82f6; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")

    def _on_voice_tts_finished(self):
        self.inst_status.setText("🎙️ 请说话...")
        self.inst_status.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold; border: none; padding-left: 4px;")

    def _on_voice_error(self, msg: str):
        self.log_message.emit("error", f"语音错误: {msg}")

    # ---- 坐标映射 ----
    #important  在预览窗口上点的那个位置，对应相机原始画面里的哪个像素
    #三层坐标：label:Qt 界面控件的大小，鼠标点击时 event.x()/y() 给的是这个
    #           pixmap:显示用的额缩放图，且居中放在label里-四周可能有留白
    #           frame：相机原始帧（self._frame的numpy数组），标定参数对应的坐标系
    def _label_to_frame_coords(self, label_x, label_y):
        if self._frame is None:
            return None, None
        pixmap = self.preview_label.pixmap()
        if pixmap is None:
            return None, None

        frame_h, frame_w = self._frame.shape[:2]
        pix_w = pixmap.width()
        pix_h = pixmap.height()
        label_w = self.preview_label.width()
        label_h = self.preview_label.height()

        offset_x = (label_w - pix_w) / 2
        offset_y = (label_h - pix_h) / 2

        x_in_pixmap = label_x - offset_x
        y_in_pixmap = label_y - offset_y

        if x_in_pixmap < 0 or y_in_pixmap < 0 or x_in_pixmap > pix_w or y_in_pixmap > pix_h:
            return None, None

        scale_x = frame_w / pix_w
        scale_y = frame_h / pix_h

        return x_in_pixmap * scale_x, y_in_pixmap * scale_y

    # ---- 摄像头连接/断开 ----

    def _on_connect(self):
        cam_id = int(self.id_combo.currentText())
        self._cap = open_capture(cam_id)
        if self._cap is None:
            self.log_message.emit("error", f"无法打开相机 {cam_id}")
            return

        self.log_message.emit("info", f"相机 {cam_id} 已连接")
        self.btn_connect.setEnabled(False)
        self.btn_disconnect.setEnabled(True)
        self.id_combo.setEnabled(False)
        self.status_label.setText(f"已连接 (相机{cam_id})")
        self.status_label.setStyleSheet("color: #22c55e; font-weight: bold;")
        self._timer.start(33)  # ~30fps

        # 自动启动语音待命
        self._auto_start_voice()

    def _auto_start_voice(self):
        """电机+相机都连接后，自动启动语音待命"""
        if self._motor_manager and self._motor_manager.is_connected and self._cap:
            if self._init_voice_manager():
                if self._voice_manager.state == "idle":
                    self._voice_manager.start_listening()

    def _on_disconnect(self):
        reply = QMessageBox.question(self, "断开相机", "确认断开相机连接？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._do_disconnect()

    def _do_disconnect(self):
        self._timer.stop()
        if self._grasp_worker is not None:
            try:
                self._grasp_worker.finished.disconnect()
                self._grasp_worker.error.disconnect()
            except TypeError:
                pass
            self._grasp_worker.terminate()
            self._grasp_worker.wait(3000)
            self._grasp_worker = None
        if self._detect_worker is not None:
            try:
                self._detect_worker.finished.disconnect()
                self._detect_worker.error.disconnect()
            except TypeError:
                pass
            self._detect_worker.terminate()
            self._detect_worker.wait(3000)
            self._detect_worker = None
        if self._instruction_worker is not None:
            self._instruction_worker.terminate()
            self._instruction_worker.wait(3000)
            self._instruction_worker = None
        if self._voice_manager is not None:
            self._voice_manager.cancel()
        if self._cap:
            self._cap.release()
            self._cap = None

        self._frame = None
        self._click_pixel = None
        self._pixel_info = None
        self._detection_boxes = []
        self.preview_label.clear_detection_boxes()
        self.preview_label.clear()
        self.preview_label.setText("相机未连接")
        self.preview_label.set_crosshair(None)
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.id_combo.setEnabled(True)
        self.status_label.setText("未连接")
        self.status_label.setStyleSheet("color: #6b7280; font-weight: bold;")
        self.log_message.emit("info", "相机已断开")

    def _on_frame(self):
        if not self._cap or not self._cap.isOpened():
            return
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return

        self._frame = frame.copy()

        # BGR -> RGB -> QImage -> QPixmap
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        scaled = pixmap.scaled(
            self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.preview_label.setPixmap(scaled)

    def clear(self):
        self._do_disconnect()
        if self._embodied_sdk:
            try:
                self._embodied_sdk.clear_history()
            except Exception:
                pass
