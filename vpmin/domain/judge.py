"""判定：五类里的 3/4/5（超过标准 / 低于标准 / 中间亮光长时间停顿）。

口径（老板 2026-10-29 定）：
    **判定时长 = 亮光时长**（首段起 → 末段止，含中间停顿）。
    标准区间照旧取后端上下限；不加任何 PLC 换算。

优先级（单列只能写一个）：**停顿 5 > 超过标准 3 > 低于标准 4**。
舍入：统一 round(x, 3)（10-27 铁律：判定与显示同一套舍入）。
"""

from typing import Optional, Sequence

from .models import Segment, Verdict, Window

TYPE_NORMAL = 0
TYPE_OVER = 3
TYPE_UNDER = 4
TYPE_PAUSE = 5
TYPE_UNJUDGED = 6

DIGITS = 3


def _r(x: float) -> float:
    return round(float(x), DIGITS)


def _fmt(x: float) -> str:
    """95.20 → '95.2'；不输出多余 0，便于原因文案阅读。"""
    text = f"{_r(x):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def longest_gap(segments: Sequence[Segment]):
    """最大停顿：(段序号, 秒数)。段序号从 1 起，指「停顿之后的那一段」。"""
    best_i, best_gap = 0, None
    for i, seg in enumerate(segments, start=1):
        gap = seg.gap_before
        if gap is None:
            continue
        if best_gap is None or gap > best_gap:
            best_i, best_gap = i, gap
    return best_i, best_gap


def judge(duration: Optional[float], window: Window,
          segments: Optional[Sequence[Segment]] = None,
          pause_threshold: float = 0.0) -> Verdict:
    """时长落在闭区间 → 成功；超上/低下/中间停顿各归一类。

    pause_threshold <= 0 时不做停顿判定（与旧行为一致）。
    任一缺失（时长或标准区间）→ 未判定（type=6）。
    """
    segs = tuple(segments or ())
    if duration is None or window.low is None or window.high is None:
        return Verdict(_r(duration or 0.0), window, None, TYPE_UNJUDGED,
                       "未判定：缺少亮光时长或标准区间", segs)

    dur = _r(duration)
    low, high = _r(window.low), _r(window.high)

    if pause_threshold > 0 and len(segs) >= 2:
        idx, gap = longest_gap(segs)
        if gap is not None and _r(gap) > _r(pause_threshold):
            return Verdict(dur, window, False, TYPE_PAUSE,
                           "球化过程中间亮光停顿：第 %d 段前停顿 %ss" % (idx, _fmt(gap)),
                           segs)

    if dur > high:
        return Verdict(dur, window, False, TYPE_OVER,
                       "球化时间超过标准值：实际 %ss > 上限 %ss" % (_fmt(dur), _fmt(high)),
                       segs)

    if dur < low:
        return Verdict(dur, window, False, TYPE_UNDER,
                       "球化时间低于标准值：实际 %ss < 下限 %ss" % (_fmt(dur), _fmt(low)),
                       segs)

    return Verdict(dur, window, True, TYPE_NORMAL, "", segs)
