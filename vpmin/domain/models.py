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
class Segment:
    """一段连续亮光（elapsed 秒）。gap_before = 与上一段的停顿，第一段为 None。"""

    start: float
    end: float
    gap_before: Optional[float] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Verdict:
    """一条反应的判定结果（冻结值）。

    abnormal_type / abnormal_reason 与后端 SpheroidizationRecord 同口径：
        0 正常 / 3 超过标准 / 4 低于标准 / 5 中间停顿 / 6 未判定
    （1 进站未球化、2 进线中断 需要 PLC 数据，不在单机位检测里判）
    """

    duration: float
    window: Window
    ok: Optional[bool]
    abnormal_type: int = 0
    abnormal_reason: str = ""
    segments: tuple = ()

    @property
    def text(self) -> str:
        if self.ok is None:
            return "未判定"
        return "成功" if self.ok else "失败"

    @property
    def segmented(self) -> bool:
        return len(self.segments) > 1
