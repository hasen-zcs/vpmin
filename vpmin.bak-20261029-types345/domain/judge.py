"""判定：时长是否落在闭区间内。"""

from typing import Optional

from .models import Verdict, Window


def judge(duration: Optional[float], window: Window) -> Verdict:
    """任一缺失 → 未判定（None），与原版 judge_duration 一致。"""
    if duration is None or window.low is None or window.high is None:
        return Verdict(0.0 if duration is None else float(duration), window, None)
    return Verdict(float(duration), window, bool(window.low <= duration <= window.high))
