"""ROI 几何：矩形 / 多边形。只描述「取哪些像素」，不做统计。"""

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Roi:
    kind: str                                   # "rect" | "polygon"
    box: Tuple[int, int, int, int]              # 外接框 x, y, w, h
    points: Tuple[Tuple[int, int], ...] = ()    # 多边形顶点（rect 为空）

    @classmethod
    def rect(cls, x: int, y: int, w: int, h: int) -> "Roi":
        return cls("rect", (int(x), int(y), int(w), int(h)))

    @classmethod
    def polygon(cls, points: Sequence[Sequence[int]]) -> "Roi":
        pts = tuple((int(p[0]), int(p[1])) for p in points)
        if len(pts) < 3:
            raise ValueError("多边形 ROI 至少需要 3 个顶点")
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        return cls("polygon", box, pts)

    def clamp(self, frame_w: int, frame_h: int) -> "Roi":
        """把外接框裁进画面（与原版一致的边界处理）。"""
        x, y, w, h = self.box
        x = max(0, min(int(x), max(0, frame_w - 1)))
        y = max(0, min(int(y), max(0, frame_h - 1)))
        w = max(1, min(int(w), frame_w - x))
        h = max(1, min(int(h), frame_h - y))
        return Roi(self.kind, (x, y, w, h), self.points)

    def mask(self, height: int, width: int) -> np.ndarray:
        """多边形在裁剪块内的布尔掩码。"""
        from cv2 import fillPoly

        mask = np.zeros((height, width), np.uint8)
        pts = np.array(self.points, np.int32)
        pts[:, 0] -= self.box[0]
        pts[:, 1] -= self.box[1]
        fillPoly(mask, [pts], 1)
        return mask.astype(bool)

    def summary(self) -> str:
        """人可读描述（文案与参考实现 core/roi.py 一致）。"""
        x, y, w, h = self.box
        if self.kind == "rect":
            return f"矩形 ROI (x={x}, y={y}, w={w}, h={h})"
        area = _polygon_area(self.points)
        return (f"多边形 ROI：{len(self.points)} 个顶点，"
                f"面积≈{area:.0f} px²，外接框 {w}×{h}")


def _polygon_area(points) -> float:
    """鞋带公式求面积（原版同算法）。"""
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0
