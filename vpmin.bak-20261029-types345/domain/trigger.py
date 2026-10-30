"""触发条件：只做阈值比较。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    """开始/结束条件（百分比阈值）。"""

    start_pct: float = 40.0
    end_pct: float = 70.0

    def start_ok(self, start_pct: float) -> bool:
        return start_pct >= self.start_pct

    def end_ok(self, end_pct: float) -> bool:
        return end_pct >= self.end_pct
