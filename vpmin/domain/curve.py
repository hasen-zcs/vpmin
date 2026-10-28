"""灰度曲线点集：滚动保留反应前 preroll 秒，反应期间逐帧追加。"""

from collections import deque
from typing import List, Optional, Tuple

Point = Tuple[float, float]


class CurveBuffer:
    def __init__(self, preroll_sec: float = 5.0, max_points: int = 20000):
        self.preroll = float(preroll_sec)
        self.max_points = int(max_points)
        self._pre: deque = deque()
        self._active: Optional[List[Point]] = None
        self._last: List[Point] = []

    def push(self, elapsed: float, gray: float) -> None:
        if self._active is not None:
            self._active.append((elapsed, gray))
            if len(self._active) > self.max_points:      # 长反应：隔点抽稀，保首尾
                head, tail = self._active[0], self._active[-1]
                self._active = [head] + self._active[1:-1:2] + [tail]

        self._pre.append((elapsed, gray))
        while self._pre and self._pre[0][0] < elapsed - self.preroll:
            self._pre.popleft()

    def reset(self) -> None:
        self._pre.clear()
        self._active = None
        self._last = []

    def begin(self) -> None:
        """开始判到：把前置基线接在曲线前面。"""
        self._active = list(self._pre)

    def points(self) -> List[Point]:
        return list(self._active or [])

    def close(self) -> List[Point]:
        points = list(self._active or [])
        self._last = points
        self._active = None
        return points

    def view(self) -> List[Point]:
        """给页面看的曲线：进行中优先，结束后留在上一条。"""
        return list(self._active if self._active is not None else self._last)
