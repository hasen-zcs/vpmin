"""五类合一：PLC 侧（1/2）+ 亮光侧（3/4/5）→ 一条记录、一个类型码。

为什么要有这一层：
    类型 1/2 只有 PLC 时序能判，类型 3/4/5 只有亮光能判；
    一炉最终只能落一个 abnormal_type（数据库就一列），所以必须在这一层收敛。

优先级（老板 2026-10-29 定）：**2 进线中断 > 1 进站未球化 > 5 中间停顿 > 3 超过 > 4 低于**。
次级异常不丢：拼进 reason 的「另：…」（数据库 abnormal_reason 限 200 字）。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence

from .heat import Heat
from .models import Segment, Verdict

PRIORITY = {2: 0, 1: 1, 5: 2, 3: 3, 4: 4, 0: 9, 6: 9}

TYPE_TEXT = {0: "正常", 1: "进站未球化", 2: "进线中断", 3: "超过标准",
             4: "低于标准", 5: "中间停顿", 6: "未判定"}

REASON_MAX = 200


def _w(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _hms(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    s = max(0.0, float(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return "%02d:%02d:%05.2f" % (int(h), int(m), sec)


@dataclass
class LightResult:
    """亮光侧一次判定结果（时间用 epoch 秒，与 PLC 同一条时间轴）。"""

    start_ts: float
    end_ts: float
    duration: float
    abnormal_type: int = 0
    reason: str = ""
    ok: Optional[bool] = True
    segments: Sequence[Segment] = ()


@dataclass
class FinalRecord:
    """一炉最终记录（字段尽量对齐后端 SpheroidizationRecord，方便以后直接推）。"""

    reaction_count: int = 0
    detection_time: Optional[float] = None
    light_start: Optional[float] = None
    light_end: Optional[float] = None
    light_duration: Optional[float] = None
    feed_start: Optional[float] = None
    feed_end: Optional[float] = None
    button: Optional[float] = None
    arrival: Optional[float] = None
    weight: Optional[float] = None
    anchor: str = ""
    abnormal_type: int = 0
    abnormal_reason: str = ""
    ok: Optional[bool] = True
    segments: Sequence[Segment] = ()
    secondary: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        rec = {
            "reaction_count": self.reaction_count,
            "detection_time": _w(self.detection_time),
            "spheroidization_start_time": _w(self.light_start),
            "spheroidization_end_time": _w(self.light_end),
            "actual_spheroidization_time": _hms(self.light_duration),
            "spheroidization_abnormal": (None if self.ok is None
                                         else (0 if self.ok else 1)),
            "abnormal_type": self.abnormal_type,
            "abnormal_reason": self.abnormal_reason,
            "spheroidization_wire_entry_start_time": _w(self.feed_start),
            "spheroidization_wire_entry_end_time": _w(self.feed_end),
            "spheroidization_wire_entry_total_duration": (
                None if (self.feed_start is None or self.feed_end is None)
                else round(self.feed_end - self.feed_start, 3)),
            "spheroidization_weight": self.weight,
            "plc_button_time": _w(self.button),
            "plc_arrival_time": _w(self.arrival),
            "plc_anchor": self.anchor or None,
        }
        if len(self.segments) > 1:
            rec["spheroidization_segments"] = [
                {"seq": i, "start": _w(s.start), "end": _w(s.end),
                 "duration_sec": round(s.duration, 3),
                 "gap_before_sec": (None if s.gap_before is None
                                    else round(s.gap_before, 3))}
                for i, s in enumerate(self.segments, start=1)
            ]
        return rec


def shift_segments(segments: Sequence[Segment], offset: float) -> tuple:
    """把「秒表秒」的分段平移成「日历秒」（只在接线处做一次，别在别处再换）。"""
    return tuple(Segment(s.start + offset, s.end + offset, s.gap_before)
                 for s in segments)


def merge_types(plc_type: int, plc_reason: str,
                light_type: int, light_reason: str) -> tuple:
    """按优先级挑主类型；次级异常拼进原因。返回 (type, reason)。"""
    cands = []
    if plc_type:
        cands.append((PRIORITY.get(plc_type, 9), plc_type, plc_reason))
    if light_type:
        cands.append((PRIORITY.get(light_type, 9), light_type, light_reason))
    if not cands:
        return 0, ""

    cands.sort(key=lambda c: c[0])
    main_type, main_reason = cands[0][1], cands[0][2]
    others = [c[2] for c in cands[1:] if c[2]]
    reason = main_reason
    for extra in others:
        tail = "；另：" + extra
        if len(reason) + len(tail) > REASON_MAX:
            break
        reason += tail
    return main_type, reason[:REASON_MAX]


def build_final(heat: Heat, light: Optional[LightResult], reaction_count: int = 0,
                no_light_type: int = 1) -> FinalRecord:
    """一炉 PLC 证据 + （可能有的）亮光结果 → 最终记录。

    no_light_type：有动作但整炉没亮光算哪一类（默认 1 进站未球化）。
    """
    rec = FinalRecord(
        reaction_count=reaction_count,
        arrival=heat.arrival_ts,
        weight=heat.weight,
        button=heat.button_ts,
        feed_start=heat.feed_start_ts,
        feed_end=heat.feed_end_ts,
        anchor=heat.anchor,
    )
    # 判定开始时刻 = 锚点时刻（谁先来谁是锚点：按钮 > 喂丝开始 > 亮光 > 到位）
    rec.detection_time = (heat.anchor_ts or heat.button_ts or heat.feed_start_ts
                          or heat.arrival_ts)

    if light is not None:
        rec.light_start, rec.light_end = light.start_ts, light.end_ts
        rec.light_duration = light.duration
        rec.segments = light.segments
        rec.ok = light.ok
        rec.abnormal_type, rec.abnormal_reason = merge_types(
            heat.judged_type, heat.reason, light.abnormal_type, light.reason)
    else:
        # 没有亮光：PLC 已经判 1/2 就用它的；否则「有动作无亮光」按 no_light_type
        if heat.judged_type:
            rec.abnormal_type, rec.abnormal_reason = heat.judged_type, heat.reason
        elif heat.started_treating or heat.has_light:
            rec.abnormal_type = no_light_type
            rec.abnormal_reason = "有球化动作但整炉未检测到亮光"
        else:
            rec.abnormal_type, rec.abnormal_reason = 0, ""
        rec.ok = None if rec.abnormal_type in (0, 6) else False

    if rec.abnormal_type in (0, 6) and rec.light_duration is None and rec.ok is None:
        rec.ok = None
    return rec


def matches(heat: Heat, light: LightResult, tolerance: float = 5.0,
            light_grace: float = 30.0) -> bool:
    """亮光是否属于这一炉：亮光起点落在 [炉起 - 容差, 炉止 + 宽限]。"""
    start = heat.arrival_ts or heat.feed_start_ts or heat.button_ts
    end = heat.feed_end_ts or heat.light_end_ts or heat.arrival_ts
    if start is None or end is None:
        return heat.arrival_ts is None and heat.feed_start_ts is None
    return (start - tolerance) <= light.start_ts <= (end + light_grace)
