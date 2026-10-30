"""一炉的生命周期状态机：只判 R5 的 1（进站未球化）和 2（进线中断）。

为什么要它：类型 1/2 靠 PLC 的**事件时序**判，不能靠"一炉一行"的事后汇总。
把事件喂进来，到点没动作/没结束就判，并在超时那一刻立刻出结论（实时性）。

输入事件（由 adapters/plc.py 产出）：
    arrival     铁水到位（重量由无效变有效 / 或专用位）
    button      按钮上升沿
    feed_start  喂丝开始上升沿
    feed_end    喂丝结束上升沿（ts 已回推 24s，raw_ts 是位真正跳变时刻）
    light_start / light_end   亮光（来自检测侧，只用来排除误判）

判定规则（老板 2026-10-29 定稿的方向）：
    1 进站未球化：到位后 W1 秒内无按钮、无喂丝开始、无亮光
    2 进线中断：  (a) 喂丝开始后按 raw 计 N 秒仍无喂丝结束
                  (b) 喂丝结束来了但进线时长 < M 秒
优先级由调用方合并（2 > 1 > 5 > 3 > 4）。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

TYPE_NONE = 0
TYPE_NO_LIGHT = 1        # 进站未球化
TYPE_FEED_BREAK = 2      # 进线中断

KIND_ARRIVAL = "arrival"
KIND_BUTTON = "button"
KIND_FEED_START = "feed_start"
KIND_FEED_END = "feed_end"
KIND_LIGHT_START = "light_start"
KIND_LIGHT_END = "light_end"


@dataclass
class Heat:
    """一炉的原始证据（全部是时刻，秒）。"""

    arrival_ts: Optional[float] = None
    anchor: str = ""                       # 锚点类型：button / feed_start / light / arrival
    anchor_ts: Optional[float] = None       # 锚点时刻（=这一炉从何时开始记录/判定）
    weight: Optional[float] = None
    button_ts: Optional[float] = None
    feed_start_ts: Optional[float] = None
    feed_end_ts: Optional[float] = None
    feed_end_raw_ts: Optional[float] = None
    light_start_ts: Optional[float] = None
    light_end_ts: Optional[float] = None
    feed_raw_pending: bool = False        # 喂丝结束位还未跳变（用于超时判定）
    light_active: bool = False            # 此刻是否还在亮（亮光开始后、结束前）
    last_light_ts: Optional[float] = None  # 最近一次亮光事件时刻
    judged_type: int = TYPE_NONE
    reason: str = ""

    @property
    def started_treating(self) -> bool:
        return self.button_ts is not None or self.feed_start_ts is not None

    @property
    def has_light(self) -> bool:
        return self.light_start_ts is not None

    @property
    def feed_duration(self) -> Optional[float]:
        if self.feed_start_ts is None or self.feed_end_ts is None:
            return None
        return max(0.0, self.feed_end_ts - self.feed_start_ts)


@dataclass
class HeatConfig:
    """可调阈值（出厂建议值，现场实测后再定）。"""

    no_action_sec: float = 120.0        # 到位后多久没动作算未球化
    no_action_mode: str = "window"      # window=到点即判；next_arrival=等下一炉到位才判
    feed_end_timeout: float = 180.0     # 喂丝开始后多久没有结束算中断（按 raw 计，**有亮光否决**）
    light_veto: bool = True             # True：还在亮 / 刚熄 <= light_silence_sec 就不判中断
    light_silence_sec: float = 60.0     # 「刚熄」的判定窗口（必须 > 结束位延迟 24s）
    button_mode: str = "momentary"      # momentary=点动（上升沿即按一次）；latched=自锁（要等回落）
    button_dedupe_sec: float = 10.0     # 同一炉内这段时间的重复按键只算第一次（防抖/连按）
    button_no_light_sec: float = 120.0  # 按了按钮这么久还没亮光 → 判未球化（0=关）
    feed_min_duration: float = 60.0     # 进线短于这个算中断
    light_grace: float = 30.0           # 喂丝结束后再等这么久收亮光（之后关炉）
    close_idle_sec: float = 600.0       # 太久没动静就关炉（炉间隔 8~12 分钟）


class HeatTracker:
    """事件进、判定出。纯领域逻辑，不碰 IO、不看墙钟（时间全由调用方传入）。"""

    def __init__(self, cfg: Optional[HeatConfig] = None):
        self.cfg = cfg or HeatConfig()
        self.current: Optional[Heat] = None
        self.closed: List[Heat] = []
        self._last_event_ts: Optional[float] = None
        self.light_seen = False         # 见过亮光事件吗（排障用）
        # 亮光流当前可用吗：False（相机没开/检测停了）就不判 2a，宁可不判也不误报。
        # 由应用层按「分析是否在跑」设置。
        self.light_stream_ok = True

    # ---------------- 事件入口 ----------------
    def on_event(self, kind: str, ts: float, weight: Optional[float] = None,
                 raw_ts: Optional[float] = None) -> List[Heat]:
        """喂一个事件；返回本次因此关掉的炉（可能 0 或 1 条）。"""
        out: List[Heat] = []
        self._last_event_ts = ts if self._last_event_ts is None else max(self._last_event_ts, ts)

        if kind == KIND_ARRIVAL:
            # 🔴 一炉进行中（还在喂丝 / 还在亮）时，到位事件一律不当新炉：
            #    重量抖动、重复到位都不能把这一炉切断（2026-10-29 现场影子实测教训）
            cur = self.current
            if cur is not None and (cur.feed_raw_pending or cur.light_active
                                    or (cur.feed_start_ts is not None
                                        and cur.feed_end_ts is None)):
                return out
            out += self._close_current(ts, "新一炉到位")
            self.current = Heat(arrival_ts=ts, weight=weight,
                                anchor=KIND_ARRIVAL, anchor_ts=ts)

        elif kind == KIND_BUTTON:
            # 按钮 = 判定开始条件（老板 2026-10-29：只当记录/判定的起点，亮光照旧）
            cur = self.current
            if cur is not None and cur.button_ts is not None:
                if ts - cur.button_ts < self.cfg.button_dedupe_sec:
                    return out                  # 同一炉的连按/抖动，忽略
                # 上一炉已经有实际动作 → 这次按下算「新一炉开始」（按钮兼作炉边界）
                if (cur.feed_start_ts is not None or cur.light_start_ts is not None
                        or cur.has_light):
                    out += self._close_current(ts, "新一炉按钮按下")
            heat = self._ensure(ts, weight, anchor=KIND_BUTTON)
            if heat.button_ts is None:
                heat.button_ts = ts
                if heat.anchor_ts is None or ts < heat.anchor_ts:
                    heat.anchor, heat.anchor_ts = KIND_BUTTON, ts

        elif kind == KIND_FEED_START:
            heat = self._ensure(ts, weight, anchor=KIND_FEED_START)
            heat.feed_start_ts = heat.feed_start_ts or ts
            heat.feed_raw_pending = True

        elif kind == KIND_FEED_END:
            heat = self._ensure(ts, weight)
            heat.feed_end_ts = ts
            heat.feed_end_raw_ts = raw_ts if raw_ts is not None else ts
            heat.feed_raw_pending = False
            dur = heat.feed_duration
            if dur is not None and dur < self.cfg.feed_min_duration:
                heat.judged_type = TYPE_FEED_BREAK
                heat.reason = ("进线未正常完成：进线时长 %.1fs < 最短 %.0fs"
                               % (dur, self.cfg.feed_min_duration))
                out += self._close_current(ts, "进线过短")
            else:
                out += self._maybe_close(ts)

        elif kind == KIND_LIGHT_START:
            heat = self._ensure(ts, weight, anchor=KIND_LIGHT_START)
            heat.light_start_ts = heat.light_start_ts or ts
            heat.light_active = True
            heat.last_light_ts = ts
            self.light_seen = True

        elif kind == KIND_LIGHT_END:
            self.light_seen = True
            if self.current is not None and self.current.light_start_ts is not None:
                self.current.light_end_ts = ts
                self.current.light_active = False
                self.current.last_light_ts = ts
                out += self._maybe_close(ts)

        return out

    def tick(self, now: float) -> List[Heat]:
        """定时调用（比如 1 秒一次）：判超时、判未球化、清理长空闲。"""
        out: List[Heat] = []
        heat = self.current
        if heat is None:
            return out

        # 2a 进线中断：喂丝开始后 raw 计时过了 N 秒仍没等到结束位
        #    🔴 亮光否决：真实有 7 炉进线 240~887s（正常长炉），光看超时会误报；
        #       还在亮 / 刚熄 <= light_silence_sec 就说明还在正常球化，不判中断。
        if heat.feed_raw_pending and heat.feed_start_ts is not None:
            waited = now - heat.feed_start_ts
            limit = self.cfg.feed_end_timeout
            if not self.light_stream_ok:
                return out                          # 没有亮光信息 → 不判中断（避免误报）
            if waited >= limit:
                glowed = bool(heat.light_active) or (
                    heat.last_light_ts is not None
                    and now - heat.last_light_ts <= self.cfg.light_silence_sec)
                if self.cfg.light_veto and glowed:
                    return out                     # 还在亮 → 只是长炉，不判
                heat.judged_type = TYPE_FEED_BREAK
                heat.reason = ("进线未正常完成：喂丝开始后 %.0fs 未收到喂丝结束且无亮光"
                               % limit)
                out += self._close_current(now, "进线超时")
                return out

        # 1a 按了按钮但一直没亮光 → 未球化（不等整炉结束，X 秒就判）
        if (self.cfg.button_no_light_sec > 0 and heat.button_ts is not None
                and not heat.has_light and not heat.light_active):
            waited = now - heat.button_ts
            if waited >= self.cfg.button_no_light_sec:
                heat.judged_type = TYPE_NO_LIGHT
                heat.reason = ("按钮按下 %.0fs 后仍未检测到球化亮光" % waited)
                out += self._close_current(now, "按钮后无亮光")
                return out

        # 1 进站未球化：到位后 W1 秒内没有任何动作、也没有亮光
        if (self.cfg.no_action_mode == "window" and heat.arrival_ts is not None
                and not heat.started_treating and not heat.has_light):
            if now - heat.arrival_ts >= self.cfg.no_action_sec:
                heat.judged_type = TYPE_NO_LIGHT
                heat.reason = ("铁水到位 %.1fs 后仍未检测到球化动作与亮光（进线/按钮均无）"
                               % (now - heat.arrival_ts))
                out += self._close_current(now, "未球化窗口到期")
                return out

        # 喂丝结束 + 亮光结束两样齐了才关炉；没亮光的炉等 light_grace
        out += self._maybe_close(now)

        # 长空闲关炉：别让一条炉永远挂着
        if (not out and self._last_event_ts is not None
                and now - self._last_event_ts >= self.cfg.close_idle_sec):
            out += self._close_current(now, "长时间无事件")
        return out

    # ---------------- 内部 ----------------
    def _ensure(self, ts: float, weight: Optional[float], anchor: str = "") -> Heat:
        """没有当前炉就补一个（PLC 到位事件缺失时也不丢数据）。"""
        if self.current is None:
            self.current = Heat(arrival_ts=ts if anchor == KIND_ARRIVAL else None,
                                weight=weight, anchor=anchor, anchor_ts=ts)
        elif weight is not None and self.current.weight is None:
            self.current.weight = weight
        return self.current

    def _maybe_close(self, now: float) -> List[Heat]:
        """够条件才关炉：喂丝结束**且**（亮光结束 或 整炉没亮光且过了宽限）。

        为什么不能见到亮光结束就关：喂丝结束位比亮光结束还晚约 24 秒到，
        提前关炉会把进线结束/时长丢掉（融合后就拿不到进线数据了）。
        """
        heat = self.current
        if heat is None or heat.feed_end_ts is None:
            return []
        if heat.light_end_ts is not None:
            return self._close_current(now, "亮光与进线都结束")
        if not heat.has_light:
            if now - heat.feed_end_ts >= self.cfg.light_grace:
                return self._close_current(now, "无亮光宽限到期")
        return []

    def _close_current(self, ts: float, why: str) -> List[Heat]:
        heat = self.current
        if heat is None:
            return []
        # next_arrival 模式：到位后一直没动作，到下一炉才追判
        if (self.cfg.no_action_mode == "next_arrival" and heat.judged_type == TYPE_NONE
                and heat.arrival_ts is not None and not heat.started_treating
                and not heat.has_light):
            heat.judged_type = TYPE_NO_LIGHT
            heat.reason = "铁水到位后整炉未检测到球化动作与亮光（进线/按钮均无）"
        heat.close_reason = why
        self.closed.append(heat)
        self.current = None
        return [heat]
