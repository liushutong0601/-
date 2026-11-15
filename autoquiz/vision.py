from __future__ import annotations

from pathlib import Path
from typing import Any


class YOLODetector:
    """可选 YOLO 适配器。

    当前项目尚未训练页面模型，因此主流程默认使用网页结构。
    后续提供训练权重后，可用本类输出区域框，辅助识别章节和控件状态。
    """

    def __init__(self, model_path: str | Path) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"YOLO 权重不存在：{self.model_path}")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("请安装 requirements-vision.txt 后再使用 YOLO。") from exc
        self.model = YOLO(str(self.model_path))

    def detect(self, image_path: str | Path, confidence: float = 0.5) -> list[dict[str, Any]]:
        results = self.model.predict(str(image_path), conf=confidence, verbose=False)
        detections: list[dict[str, Any]] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                class_id = int(box.cls.item())
                detections.append(
                    {
                        "label": names[class_id],
                        "confidence": float(box.conf.item()),
                        "box": [float(value) for value in box.xyxy[0].tolist()],
                    }
                )
        return detections
