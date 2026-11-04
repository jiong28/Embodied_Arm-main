"""
独立 YOLOv8 ONNX 目标检测器
============================
不依赖 Horizon_Core 编译模块，使用 onnxruntime 直接加载 yolov8n.onnx。
当 LLM 多模态检测效果不好时，可切换到此检测器获得更稳定的像素级定位。
"""

import os
import logging
from typing import List, Dict, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# COCO 80 类名称（YOLOv8 默认训练集）
_COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# 中文名称映射
_COCO_NAMES_CN = {
    "person": "人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
    "airplane": "飞机", "bus": "公交车", "train": "火车", "truck": "卡车",
    "boat": "船", "traffic light": "红绿灯", "fire hydrant": "消防栓",
    "stop sign": "停车标志", "bench": "长椅", "bird": "鸟", "cat": "猫",
    "dog": "狗", "horse": "马", "sheep": "羊", "cow": "牛", "elephant": "大象",
    "bear": "熊", "zebra": "斑马", "giraffe": "长颈鹿", "backpack": "背包",
    "umbrella": "雨伞", "handbag": "手提包", "tie": "领带", "suitcase": "行李箱",
    "frisbee": "飞盘", "bottle": "瓶子", "wine glass": "酒杯", "cup": "杯子",
    "fork": "叉子", "knife": "刀", "spoon": "勺子", "bowl": "碗",
    "banana": "香蕉", "apple": "苹果", "sandwich": "三明治", "orange": "橙子",
    "broccoli": "西兰花", "carrot": "胡萝卜", "hot dog": "热狗", "pizza": "披萨",
    "donut": "甜甜圈", "cake": "蛋糕", "chair": "椅子", "couch": "沙发",
    "potted plant": "盆栽", "bed": "床", "dining table": "餐桌", "toilet": "马桶",
    "tv": "电视", "laptop": "笔记本电脑", "mouse": "鼠标", "remote": "遥控器",
    "keyboard": "键盘", "cell phone": "手机", "microwave": "微波炉",
    "oven": "烤箱", "toaster": "烤面包机", "sink": "水槽", "refrigerator": "冰箱",
    "book": "书", "clock": "时钟", "vase": "花瓶", "scissors": "剪刀",
    "teddy bear": "泰迪熊", "hair drier": "吹风机", "toothbrush": "牙刷",
    "sports ball": "球", "skateboard": "滑板", "surfboard": "冲浪板",
    "tennis racket": "网球拍", "baseball bat": "棒球棒", "baseball glove": "棒球手套",
    "kite": "风筝", "skis": "滑雪板", "snowboard": "滑雪橇",
}


def _letterbox(img: np.ndarray, new_shape: int = 640) -> tuple:
    """Letterbox 缩放，保持宽高比，填充灰色边框"""
    h, w = img.shape[:2]
    r = min(new_shape / h, new_shape / w)
    new_w, new_h = int(w * r), int(h * r)
    img_resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    dw = (new_shape - new_w) / 2
    dh = (new_shape - new_h) / 2
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img_padded = cv2.copyMakeBorder(
        img_resized, top, bottom, left, right,
        cv2.BORDER_CONSTANT, value=(114, 114, 114)
    )
    return img_padded, r, (dw, dh)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    """非极大值抑制"""
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)

    order = scores.argsort()[::-1]
    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        inter_w = np.maximum(0.0, xx2 - xx1)
        inter_h = np.maximum(0.0, yy2 - yy1)
        inter = inter_w * inter_h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-7)

        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return np.array(keep, dtype=np.intp)


class YOLODetector:
    """
    独立的 YOLOv8 ONNX 目标检测器。

    使用 onnxruntime 加载 yolov8n.onnx 模型，无需依赖 Horizon_Core 编译模块。

    用法：
        detector = YOLODetector("config/yolov8n.onnx")
        detections = detector.detect(frame, conf_thres=0.35)
        for det in detections:
            print(det["name"], det["bbox"], det["conf"])
    """

    def __init__(self, model_path: str = "", input_size: int = 640):
        self._model_path = model_path
        self._input_size = input_size
        self._session = None
        self._input_name = None
        self._output_names = None

    def _ensure_model(self) -> bool:
        """懒加载 ONNX 模型"""
        if self._session is not None:
            return True

        if not self._model_path or not os.path.exists(self._model_path):
            logger.error(f"YOLO 模型文件不存在: {self._model_path}")
            return False

        try:
            import onnxruntime as ort
            providers = ["CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")

            self._session = ort.InferenceSession(
                self._model_path,
                providers=providers,
            )
            self._input_name = self._session.get_inputs()[0].name
            self._output_names = [o.name for o in self._session.get_outputs()]
            logger.info(f"YOLO 模型加载成功: {self._model_path} (providers={providers})")
            return True
        except ImportError:
            logger.error("onnxruntime 未安装，请运行: pip install onnxruntime")
            return False
        except Exception as e:
            logger.error(f"YOLO 模型加载失败: {e}")
            return False

    def detect(
        self,
        frame: np.ndarray,
        conf_thres: float = 0.35,
        iou_thres: float = 0.45,
        target_class: Optional[str] = None,
    ) -> List[Dict]:
        """
        检测一帧图像中的物体。

        Args:
            frame: BGR 格式的 OpenCV 图像
            conf_thres: 置信度阈值
            iou_thres: NMS IoU 阈值
            target_class: 可选，只返回指定类别（如 "apple"、"cup"）

        Returns:
            检测结果列表，每项包含 name, bbox, conf, class_id
        """
        if not self._ensure_model():
            return []

        h, w = frame.shape[:2]

        # 1. 预处理：letterbox + 归一化
        img_padded, ratio, (dw, dh) = _letterbox(frame, self._input_size)
        img_rgb = cv2.cvtColor(img_padded, cv2.COLOR_BGR2RGB)
        img_norm = img_rgb.astype(np.float32) / 255.0
        img_transposed = np.transpose(img_norm, (2, 0, 1))  # HWC -> CHW
        blob = np.expand_dims(img_transposed, axis=0)  # 添加 batch 维度

        # 2. 推理
        try:
            outputs = self._session.run(self._output_names, {self._input_name: blob})
        except Exception as e:
            logger.error(f"YOLO 推理失败: {e}")
            return []

        # 3. 后处理
        # YOLOv8 输出格式: [1, 84, 8400] (4 bbox + 80 class scores)
        predictions = outputs[0]
        if predictions.ndim == 3:
            predictions = predictions[0]  # 去掉 batch 维度 -> [84, 8400]

        # 转置为 [8400, 84]
        predictions = predictions.T  # [8400, 84]

        # 提取 bbox 和 class scores
        boxes_raw = predictions[:, :4]  # cx, cy, w, h
        class_scores = predictions[:, 4:]  # 80 类置信度

        # 每个框的最大类别置信度
        max_scores = np.max(class_scores, axis=1)
        class_ids = np.argmax(class_scores, axis=1)

        # 置信度过滤
        mask = max_scores >= conf_thres
        boxes_raw = boxes_raw[mask]
        max_scores = max_scores[mask]
        class_ids = class_ids[mask]

        if len(boxes_raw) == 0:
            return []

        # cx,cy,w,h -> x1,y1,x2,y2
        cx, cy, bw, bh = boxes_raw[:, 0], boxes_raw[:, 1], boxes_raw[:, 2], boxes_raw[:, 3]
        x1 = cx - bw / 2
        y1 = cy - bh / 2
        x2 = cx + bw / 2
        y2 = cy + bh / 2
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # 从 letterbox 坐标还原到原始图像坐标
        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - dw) / ratio
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - dh) / ratio

        # 裁剪到图像范围
        boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]], 0, w)
        boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]], 0, h)

        # NMS
        keep = _nms(boxes_xyxy, max_scores, iou_thres)
        boxes_xyxy = boxes_xyxy[keep]
        max_scores = max_scores[keep]
        class_ids = class_ids[keep]

        # 4. 组装结果
        results = []
        for i in range(len(boxes_xyxy)):
            cls_id = int(class_ids[i])
            cls_name = _COCO_NAMES[cls_id] if cls_id < len(_COCO_NAMES) else f"class_{cls_id}"

            if target_class and cls_name != target_class:
                continue

            cls_name_cn = _COCO_NAMES_CN.get(cls_name, cls_name)

            x1, y1, x2, y2 = boxes_xyxy[i].tolist()
            results.append({
                "name": f"{cls_name_cn}({cls_name})",
                "bbox": [x1, y1, x2, y2],
                "conf": float(max_scores[i]),
                "class_id": cls_id,
                "class_name": cls_name,
            })

        return results

    def get_available_classes(self) -> List[str]:
        """返回模型支持的所有类别名称"""
        return _COCO_NAMES.copy()
