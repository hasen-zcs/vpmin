"""像素统计：OpenCV 取灰 → FrameMetrics。唯一 import cv2 的统计类。"""

import cv2
import numpy as np

from ..domain.models import FrameMetrics
from .roi import Roi


class RoiProbe:
    """与 engine.process 同口径：均值灰度 + 两个占比（%）。"""

    def __init__(self, roi: Roi, start_pixel: int = 240, end_pixel: int = 240):
        self.roi = roi
        self.start_pixel = int(start_pixel)
        self.end_pixel = int(end_pixel)
        self._mask = None
        self._mask_shape = None
        self.last = None            # 最近一帧的测量结果（给日志/状态页用）

    def measure(self, frame, elapsed: float):
        height, width = frame.shape[:2]
        roi = self.roi.clamp(width, height)
        x, y, w, h = roi.box

        gray = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)

        if roi.kind == "polygon":
            if self._mask_shape != gray.shape:
                self._mask = roi.mask(h, w)
                self._mask_shape = gray.shape
            pixels = gray[self._mask]
        else:
            pixels = gray.ravel()

        if pixels.size == 0:            # ROI 落在画面外：本帧跳过（与原版一致）
            return None

        total = pixels.size
        self.last = FrameMetrics(
            elapsed=float(elapsed),
            mean_gray=float(np.mean(pixels)),
            start_pct=float(np.count_nonzero(pixels >= self.start_pixel)) / total * 100.0,
            end_pct=float(np.count_nonzero(pixels <= self.end_pixel)) / total * 100.0,
        )
        return self.last

    def set_roi(self, roi: Roi) -> None:
        self.roi = roi
        self._mask = None
        self._mask_shape = None
