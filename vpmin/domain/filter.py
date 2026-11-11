"""因果中值滤波：只用**过去** N 帧（实时可实现，不看未来）。

为什么加它（2026-10-29 模拟结论）：
    亮光占比在阈值附近高频抖动，单帧毛刺会让状态机「一掉就清零」。
    5 帧中值能把这类毛刺压掉：20% 丢帧时起判最坏推迟从 19.2s 降到 6.7s，
    30% 丢帧从 40.3s 降到 19.2s；而真实数据上起止只偏 0.09s / 0.16s（可忽略）。

注意：滑窗比例法已实测**负收益**（参数敏感，窗口一长就晚 20~30s），不要用。
"""

import statistics
from typing import List, Tuple


class MedianFilter:
    """N 帧中值（因果）。frames <= 1 时原样透传。"""

    def __init__(self, frames: int = 0):
        self.frames = max(0, int(frames))
        self._buf: List[Tuple[float, float]] = []

    def apply(self, start_pct: float, end_pct: float) -> Tuple[float, float]:
        if self.frames <= 1:
            return start_pct, end_pct
        self._buf.append((float(start_pct), float(end_pct)))
        if len(self._buf) > self.frames:
            self._buf.pop(0)
        if len(self._buf) == 1:
            return self._buf[0]
        return (statistics.median([b[0] for b in self._buf]),
                statistics.median([b[1] for b in self._buf]))

    def reset(self) -> None:
        self._buf.clear()
