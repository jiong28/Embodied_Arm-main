import os
import re
import json
import logging
import base64
import tempfile
from typing import Optional, Dict, Any, List

import requests
import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from gui.core.motor_manager import MotorManager

_LLM_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "llm_config.json",
)

_DETECT_PROMPT_TEMPLATE = (
    "你是一个目标检测助手。请检测这张 {w}x{h} 像素的图片中所有可抓取的物体。\n"
    "坐标系：左上角(0,0)，右下角({w},{h})，x向右增大，y向下增大。\n"
    "bbox格式：[x1,y1,x2,y2]，分别是边界框左上角和右下角的像素坐标。\n"
    "要求：\n"
    "1. 只检测明确可见的物体，不要猜测\n"
    "2. bbox要紧贴物体轮廓\n"
    "3. 只返回JSON数组，不要任何其他文字\n"
    "格式：[{{\"name\":\"物体名称\",\"bbox\":[x1,y1,x2,y2]}}]\n"
    "如果没有检测到物体，返回空数组：[]"
)


class LLMDetectWorker(QThread):
    """后台线程调用多模态大模型进行物体检测"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, frame: np.ndarray, config: dict):
        super().__init__()
        self._frame = frame
        self._config = config

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
            prompt = _DETECT_PROMPT_TEMPLATE.format(w=frame_w, h=frame_h)

            api_base = self._config["api_base"]
            api_key = self._config["api_key"]
            model = self._config.get("model", "mimo-v2.5")
            max_tokens = self._config.get("max_tokens", 2048)
            temperature = self._config.get("temperature", 0.1)

            if "/anthropic" in api_base:
                text = self._call_anthropic(api_base, api_key, model, b64, prompt, max_tokens, temperature)
            else:
                text = self._call_openai(api_base, api_key, model, b64, prompt, max_tokens, temperature)

            logging.getLogger(__name__).info(f"LLM 原始响应: {text[:500]}")
            boxes = self._parse_response(text)
            self.finished.emit(boxes)

        except Exception as e:
            self.error.emit(str(e))

    @staticmethod
    def _call_openai(api_base, api_key, model, b64, prompt, max_tokens, temperature):
        url = api_base.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _call_anthropic(api_base, api_key, model, b64, prompt, max_tokens, temperature):
        url = api_base.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()

    @staticmethod
    def _parse_response(text: str) -> list:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return []
        try:
            raw = match.group()
            # 修复常见 JSON 格式错误：bbox 缺少括号
            # "bbox": 261, 213, 316, 278 → "bbox": [261, 213, 316, 278]
            raw = re.sub(
                r'"bbox"\s*:\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)',
                r'"bbox": [\1, \2, \3, \4]',
                raw
            )
            items = json.loads(raw)
            result = []
            for item in items:
                name = str(item.get("name", "未知"))
                bbox = item.get("bbox", [])
                if len(bbox) == 4:
                    result.append({"name": name, "bbox": [float(x) for x in bbox]})
            return result
        except (json.JSONDecodeError, ValueError, TypeError):
            return []


logger = logging.getLogger(__name__)


class GraspWorker(QThread):
    """后台线程执行抓取运动，避免阻塞 GUI 主线程"""
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, vision_sdk, u, v, speed=500, claw=None, open_angle=90.0):
        super().__init__()
        self._sdk = vision_sdk
        self._u = u
        self._v = v
        self._speed = speed
        self._claw = claw
        self._open_angle = open_angle

    def run(self):
        try:
            import time
            from Horizon_Core import gateway as horizon_gateway
            embodied_internal = horizon_gateway.get_embodied_internal_module()
            embodied_internal._set_motion_params(
                max_speed=self._speed,
                acceleration=self._speed,
                deceleration=self._speed,
            )
            # 抓取前先把夹爪复位到张开，避免沿用上一次的闭合状态导致抓空/顶撞
            if self._claw is not None and getattr(self._claw, "is_connected", False):
                self._claw.set_angle(self._open_angle)
                time.sleep(0.8)
            ok = self._sdk.grasp_at_pixel(self._u, self._v)
            self.finished.emit(bool(ok))
        except Exception as e:
            self.error.emit(str(e))


class VisionManager:
    """视觉抓取管理器 — 桥接 MotorManager 与 Embodied_SDK 视觉抓取接口"""

    def __init__(self, motor_manager: MotorManager):
        self.motor_manager = motor_manager
        self._vision_sdk = None
        self._initialized = False
        self._camera_id = 0
        self._grasp_params: Dict[str, Any] = {}
        # 夹爪控制器（可选）。设置后，每次发起抓取前会自动把夹爪张开。
        self.claw = None

    def set_claw(self, claw):
        """注入夹爪控制器，用于抓取前自动复位到张开状态"""
        self.claw = claw

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def last_error(self) -> Optional[str]:
        """最近一次抓取失败的原因（来自 VisualGraspSDK.last_error），供界面展示"""
        if self._vision_sdk is None:
            return None
        return getattr(self._vision_sdk, "last_error", None)

    def init_sdk(self, camera_id: int = 0) -> bool:
        """初始化视觉抓取 SDK，绑定电机。电机未连接时也可初始化（仅坐标计算）。"""
        try:
            from Embodied_SDK.visual_grasp import VisualGraspSDK

            self._camera_id = camera_id
            self._vision_sdk = VisualGraspSDK(camera_id=camera_id)

            if self.motor_manager.is_connected:
                self._vision_sdk.bind_motors(
                    self.motor_manager.motors,
                    custom_zero=self.motor_manager.custom_zero,
                )
                logger.info("视觉 SDK 已绑定电机")
            else:
                logger.warning("电机未连接，视觉 SDK 仅用于坐标计算")

            self._load_grasp_params()
            self._initialized = True
            logger.info(f"视觉 SDK 初始化成功 (相机 {camera_id})")
            return True

        except Exception as e:
            logger.error(f"视觉 SDK 初始化失败: {e}")
            self._initialized = False
            return False

    def _load_grasp_params(self):
        """从 all_parameter_config.json 加载抓取参数"""
        try:
            config_dir = os.environ.get("HORIZONARM_CONFIG_DIR", "").strip()
            if not config_dir:
                config_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "config",
                )
            config_path = os.path.join(config_dir, "all_parameter_config.json")
            if not os.path.exists(config_path):
                return

            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            gp = cfg.get("vision_grasp", {}).get("grasp_params", {})
            if not gp:
                return

            ea = gp.get("euler_angles", [0, 0, 180])
            tcp = gp.get("tcp_offset", [-15, 25, 85])
            depth = gp.get(
                "grasp_depth",
                cfg.get("vision_grasp", {}).get("camera", {}).get("fixed_depth_value", 300.0),
            )

            self._grasp_params = {
                "yaw": ea[0], "pitch": ea[1], "roll": ea[2],
                "tcp_offset_x": tcp[0], "tcp_offset_y": tcp[1], "tcp_offset_z": tcp[2],
                "grasp_depth": float(depth),
                "motion_speed": gp.get("motion_speed", 500),
                "motion_acceleration": gp.get("motion_acceleration", 300),
            }

            if self._vision_sdk:
                self._vision_sdk.set_grasp_params(**self._grasp_params)
            logger.info(f"抓取参数已加载: euler={ea}, tcp={tcp}, depth={depth}, "
                        f"speed={self._grasp_params['motion_speed']}")

        except Exception as e:
            logger.warning(f"加载抓取参数失败: {e}")

    def _load_calibration_fallback(self) -> Optional[dict]:
        """备用标定参数加载：手动从 calibration_parameter.json 读取"""
        try:
            config_path = ""
            cfg_dir = os.environ.get("HORIZONARM_CONFIG_DIR", "").strip()
            if cfg_dir:
                candidate = os.path.join(cfg_dir, "calibration_parameter.json")
                if os.path.exists(candidate):
                    config_path = candidate
            if not config_path:
                candidate = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "config", "calibration_parameter.json",
                )
                if os.path.exists(candidate):
                    config_path = candidate
            if config_path:
                with open(config_path, "r", encoding="utf-8") as f:
                    calib = json.load(f)
                logger.info(f"备用方案加载标定参数成功: {config_path}")
                return calib
        except Exception as e:
            logger.warning(f"备用方案加载标定参数失败: {e}")
        return None

    def compute_pixel_info(self, u: float, v: float) -> Optional[Dict[str, Any]]:
        """计算像素坐标对应的抓取信息，不执行运动。返回信息字典或 None。"""
        if not self._vision_sdk:
            logger.error("视觉 SDK 未初始化")
            return None

        try:
            from Horizon_Core import gateway as horizon_gateway
            embodied_internal = horizon_gateway.get_embodied_internal_module()

            current_pose = embodied_internal._get_current_arm_pose()
            if current_pose is None:
                logger.error("无法获取当前末端位姿")
                return None

            calib = None
            # 优先使用编译模块自带的 getter（旧代码探测的 _load_calibration_params 实际不存在）
            for attr in ("_get_calibration_params", "_load_calibration_params"):
                fn = getattr(embodied_internal, attr, None)
                if callable(fn):
                    try:
                        calib = fn()
                    except Exception:
                        calib = None
                    if calib:
                        break
            if not calib:
                calib = self._load_calibration_fallback()
            if not calib:
                logger.error("未找到标定参数")
                return None

            grasp = embodied_internal._get_grasp_params()
            tcp_x = grasp.get("tcp_offset_x", self._grasp_params.get("tcp_offset_x", 0.0))
            tcp_y = grasp.get("tcp_offset_y", self._grasp_params.get("tcp_offset_y", 0.0))
            tcp_z = grasp.get("tcp_offset_z", self._grasp_params.get("tcp_offset_z", 0.0))

            world = embodied_internal._convert_pixel_to_world_coords(
                u, v, calib, current_pose,
                tcp_x=tcp_x, tcp_y=tcp_y, tcp_z=tcp_z,
            )
            if world is None:
                logger.error("像素坐标转换失败")
                return None

            # 打印换算原始值：以前直接钳制，把 -795mm 这类异常值掩盖了
            print(f" [PixelInfo] 原始换算 world={world}, pose={current_pose}")

            x_w, y_w, z_w = world
            min_z = float(grasp.get("min_z", 30.0))
            max_z = float(grasp.get("max_z", 600.0))
            z_clamped = max(min_z, min(max_z, float(z_w)))
            if abs(z_clamped - float(z_w)) > 1e-6:
                print(f" [PixelInfo] ⚠️ z={z_w:.1f}mm 超出 [{min_z},{max_z}]，已钳制为 {z_clamped:.1f}mm（换算本身可疑）")
            z_w = z_clamped

            yaw = grasp.get("yaw", self._grasp_params.get("yaw", 0.0))
            pitch = grasp.get("pitch", self._grasp_params.get("pitch", 0.0))
            roll = grasp.get("roll", self._grasp_params.get("roll", 180.0))

            return {
                "pixel": (u, v),
                "world": (float(x_w), float(y_w), float(z_w)),
                "tcp_offset": (tcp_x, tcp_y, tcp_z),
                "euler_angles": (yaw, pitch, roll),
                "depth_info": f"固定深度 (Z={z_w:.1f}mm)",
            }

        except Exception as e:
            logger.error(f"计算像素信息失败: {e}")
            return None

    def grasp_at_pixel_async(self, u: float, v: float, speed: int = 200) -> Optional[GraspWorker]:
        """异步执行像素抓取，返回 GraspWorker（由调用方启动和连接信号）"""
        if not self._check_ready("像素抓取"):
            return None
        worker = GraspWorker(self._vision_sdk, u, v, speed=speed, claw=self.claw)
        return worker

    def _check_ready(self, action: str) -> bool:
        if not self._vision_sdk:
            logger.error(f"{action}: 视觉 SDK 未初始化")
            return False
        if not self.motor_manager.is_connected:
            logger.warning(f"{action}: 电机未连接，无法执行运动")
            return False
        return True

    # ---- AI 多模态检测 ----

    def _load_llm_config(self) -> Optional[dict]:
        try:
            if not os.path.exists(_LLM_CONFIG_PATH):
                logger.error(f"LLM 配置文件不存在: {_LLM_CONFIG_PATH}")
                return None
            with open(_LLM_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not cfg.get("api_key"):
                logger.error("LLM API Key 未配置，请在 config/llm_config.json 中填写")
                return None
            if not cfg.get("api_base"):
                logger.error("LLM API Base 未配置")
                return None
            return cfg
        except Exception as e:
            logger.error(f"加载 LLM 配置失败: {e}")
            return None

    def detect_objects_async(self, frame: np.ndarray, mode: str = "llm") -> Optional[QThread]:
        """
        异步物体检测，支持两种模式：
        - "llm": 多模态大模型检测（千问 VL Plus 等）
        - "yolo": YOLOv8 ONNX 本地检测（更稳定、更快）
        """
        if mode == "yolo":
            return self._detect_yolo_async(frame)
        else:
            return self._detect_llm_async(frame)

    def _detect_llm_async(self, frame: np.ndarray) -> Optional[LLMDetectWorker]:
        cfg = self._load_llm_config()
        if cfg is None:
            return None
        return LLMDetectWorker(frame, cfg)

    def _detect_yolo_async(self, frame: np.ndarray) -> Optional["YOLODetectWorker"]:
        model_path = self._find_yolo_model()
        if not model_path:
            logger.error("YOLO 模型文件 yolov8n.onnx 未找到")
            return None
        return YOLODetectWorker(frame, model_path)

    @staticmethod
    def _find_yolo_model() -> str:
        """查找 yolov8n.onnx 模型文件"""
        candidates = []
        cfg_dir = os.environ.get("HORIZONARM_CONFIG_DIR", "").strip()
        if cfg_dir:
            candidates.append(os.path.join(cfg_dir, "yolov8n.onnx"))
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates.append(os.path.join(base, "config", "yolov8n.onnx"))
        candidates.append(os.path.join("config", "yolov8n.onnx"))
        for p in candidates:
            if os.path.exists(p):
                return p
        return ""


class YOLODetectWorker(QThread):
    """后台线程使用 YOLOv8 ONNX 进行本地物体检测"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, frame: np.ndarray, model_path: str, conf_thres: float = 0.35):
        super().__init__()
        self._frame = frame
        self._model_path = model_path
        self._conf_thres = conf_thres

    def run(self):
        try:
            from gui.core.yolo_detector import YOLODetector
            detector = YOLODetector(self._model_path)
            results = detector.detect(self._frame, conf_thres=self._conf_thres)
            boxes = []
            for r in results:
                boxes.append({
                    "name": r["name"],
                    "bbox": r["bbox"],
                    "conf": r["conf"],
                })
            self.finished.emit(boxes)
        except ImportError:
            self.error.emit("onnxruntime 未安装，请运行: pip install onnxruntime")
        except Exception as e:
            self.error.emit(str(e))
