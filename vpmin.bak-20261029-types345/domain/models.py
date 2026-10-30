"""领域数据：只有字段，没有行为以外的依赖。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FrameMetrics:
    """一帧的测量结果（与 engine.process 同口径）。"""

    elapsed: float
    mean_gray: float
    start_pct: float    # 灰度 >= start_pixel 的占比（%）
    end_pct: float      # 灰度 <= end_pixel   的占比（%）


@dataclass(frozen=True)
class Transition:
    """状态机推进一帧后的结果。"""

    state: str
    started: bool = False
    ended: bool = False


@dataclass(frozen=True)
class Window:
    """判定区间及来源。"""

    low: Optional[float] = None
    high: Optional[float] = None
    kind: str = "无"


@dataclass(frozen=True)
class Verdict:
    """一条反应的判定结果（冻结值）。"""

    duration: float
    window: Window
    ok: Optional[bool]

    @property
    def text(self) -> str:
        if self.ok is None:
            return "未判定"
        return "成功" if self.ok else "失败"
