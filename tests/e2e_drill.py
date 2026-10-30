#!/usr/bin/env python3
"""端到端演练：亮光检测 + PLC 事件 + 五类合一，全链路跑一遍（假时钟，无硬件、无网络、不装依赖）。

六炉脚本（T0 = 1_700_000_000 起算的相对秒）：
    A 正常        arrival 0     feed 5→95      亮光 18→98      期望 type=0
    B 超过标准    arrival 205   feed 210→340   亮光 223→333    期望 type=3
    C 低于标准    arrival 405   feed 410→510   亮光 423→473    期望 type=4
    D 进线中断    arrival 605   feed 610→无    亮光 623→683    期望 type=2
    E 进站未球化  arrival 900   无任何动作     无亮光          期望 type=1
    F 中间停顿    arrival 1100  feed 1105→1245 亮光 1118→1180 断 1210→1243  期望 type=5

约定：
    * feed_end 事件 ts = 物理结束时刻，raw_ts = 物理结束 + 24s；
      事件在 raw_ts 那个时刻才投递（结束位晚 24s 才跳变）。
    * 每个 0.1s 时间点：analysis.on_frame → PLC 事件 → plc.tick → fusion.settle。
    * 时间轴：analysis.clock 返回相对 t；wall_clock 返回 datetime.fromtimestamp(T0+t)；
      PLC 事件 ts 用 T0+t，两边对齐。

产出：/tmp/e2e-drill/heat_records.jsonl（应为 6 条，类型顺序 0,3,4,2,1,5）
      并在 stdout 打印每炉一行汇总。
"""

import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.adapters.plc import PlcEvent
from vpmin.app.analysis import AnalysisLoop
from vpmin.app.fusion_loop import FusionLog
from vpmin.app.plc_loop import PlcLoop
from vpmin.domain.curve import CurveBuffer
from vpmin.domain.fusion import LightResult
from vpmin.domain.heat import HeatConfig, HeatTracker
from vpmin.domain.models import FrameMetrics, Segment
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

T0 = 1_700_000_000
OUT_DIR = "/tmp/e2e-drill"
RECORDS = os.path.join(OUT_DIR, "heat_records.jsonl")
T_END = 1500.0            # 最后一炉结算点 1245+180=1425，留余量到 1500

# ---------------- 假时钟 ----------------
_now = {"t": 0.0}


def clock_rel():
    return _now["t"]


def wall_clock():
    return datetime.fromtimestamp(T0 + _now["t"])


# ---------------- 六炉亮光时间表 (start, end)，左闭右开 ----------------
LIGHT_SEGS = [(18, 98), (223, 333), (423, 473), (623, 683), (1118, 1180), (1210, 1243)]


def light_on(t):
    return any(s <= t < e for s, e in LIGHT_SEGS)


# ---------------- PLC 事件时间表：十分之一秒序号 -> [(kind, ts_offset, raw_offset)] ----------------
# feed_end：ts=物理结束，raw_ts=物理结束+24，事件在 raw 时刻投递
PLC_EVENTS = {
    0:     [("arrival", 0, None)],
    50:    [("feed_start", 5, None)],
    1190:  [("feed_end", 95, 119)],
    2050:  [("arrival", 205, None)],
    2100:  [("feed_start", 210, None)],
    3640:  [("feed_end", 340, 364)],
    4050:  [("arrival", 405, None)],
    4100:  [("feed_start", 410, None)],
    5340:  [("feed_end", 510, 534)],
    6050:  [("arrival", 605, None)],
    6100:  [("feed_start", 610, None)],
    9000:  [("arrival", 900, None)],
    11000: [("arrival", 1100, None)],
    11050: [("feed_start", 1105, None)],
    12690: [("feed_end", 1245, 1269)],
}

# ---------------- 桩 ----------------


class FakeProbe:
    """按亮光时间表出指标：亮 → start_pct=90/end_pct=5；灭 → 5/95。"""

    def __init__(self):
        self.last = None

    def measure(self, frame, elapsed):
        on = light_on(elapsed)
        self.last = FrameMetrics(elapsed, 60.0, 90.0 if on else 5.0, 5.0 if on else 95.0)
        return self.last


class FakePainter:
    def draw(self, *a, **k):
        return ""


class FakeSink:
    def build(self, *a, **k):
        return {}

    def write(self, record):
        pass


class FakeCounter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


class FakeSource:
    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {}


def main():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---------------- 组装检测侧 ----------------
    standard = StandardPolicy((60, 90), 5.0)
    standard.update({"standard_spheroidization_time_lower": 60,
                     "standard_spheroidization_time_upper": 90})

    probe, painter, sink, counter = FakeProbe(), FakePainter(), FakeSink(), FakeCounter()
    #    这是核心代码的设计问题（合并窗口应感知 PLC 一炉边界），只报告不改核心代码。
    runner = ReactionRunner(
        probe=probe, thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(0.2, 4.0),
        standard=standard, curve=CurveBuffer(5.0),
        painter=painter, sink=sink, counter=counter,
        merge_gap=120.0, pause_threshold=10.0)
    analysis = AnalysisLoop(probe=probe, runner=runner, painter=painter,
                            clock=clock_rel, wall_clock=wall_clock)

    # ---------------- 组装 PLC 侧与融合 ----------------
    tracker = HeatTracker(HeatConfig())
    # match_grace 必须 > merge_gap：亮光记录要等 merge_gap 合并窗口走完才进融合，
    # 若炉在「进线结束+30s」（默认）就先结算，会没配上亮光误判类型 1。
    # 亮光最大滞后 ≈ 4s 结束确认 + merge_gap，取 180s 覆盖（兼容 merge_gap 调到 120 的场景）。
    fusion = FusionLog(RECORDS, match_grace=180.0, clock=lambda: T0 + _now["t"])
    plc = PlcLoop(FakeSource(), tracker, event_log=None, on_heat=fusion.add_heat)

    analysis.on_light = lambda kind, el: plc.light(kind, T0 + el)

    def _on_record(record, verdict, start_el, end_el):
        if verdict is None or start_el is None:
            return
        fusion.add_light(LightResult(
            start_ts=T0 + start_el, end_ts=T0 + end_el,
            duration=float(verdict.duration),
            abnormal_type=verdict.abnormal_type,
            reason=verdict.abnormal_reason,
            ok=verdict.ok,
            segments=tuple(Segment(T0 + s.start, T0 + s.end, s.gap_before)
                           for s in (verdict.segments or ()))))

    analysis.on_record = _on_record

    # ---------------- 主循环：0.1s 步长推时间 ----------------
    analysis.start()
    for i in range(0, int(T_END * 10) + 1):
        _now["t"] = i / 10.0
        analysis.on_frame(None)                       # 1) 检测侧推进
        for kind, ts_off, raw_off in PLC_EVENTS.get(i, []):   # 2) 本时刻的 PLC 事件
            if kind == "arrival":            # 炉边界：先结算上一炉的亮光（同 serve.py 接线）
                analysis.flush_pending()
            plc.handle(PlcEvent(kind, T0 + ts_off,
                                raw_ts=(T0 + raw_off) if raw_off is not None else None))
        plc.tick(T0 + _now["t"])                      # 3) 一炉状态机超时判定
        fusion.settle(T0 + _now["t"])                 # 4) 五类合一结算

    pending = fusion.status().get("pending_heats", 0)
    if pending:
        print("[warn] 收尾时还有 %d 炉未结算，强制结算" % pending)
        fusion.flush(T0 + _now["t"])

    # ---------------- 验收与汇总 ----------------
    with open(RECORDS, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]

    expected = [0, 3, 4, 2, 1, 5]
    got = [r["abnormal_type"] for r in recs]
    problems = []
    if len(recs) != 6:
        problems.append("记录数 %d != 6" % len(recs))
    if got != expected:
        problems.append("类型顺序 %s != 期望 %s" % (got, expected))
    for r in recs:
        t, reason = r["abnormal_type"], r.get("abnormal_reason") or ""
        if t in (3, 4, 5) and not any(ch.isdigit() for ch in reason):
            problems.append("类型 %d 原因缺数字: %r" % (t, reason))
        if t in (1, 2) and not any("\u4e00" <= ch <= "\u9fff" for ch in reason):
            problems.append("类型 %d 原因缺中文说明: %r" % (t, reason))

    print("---- 六炉汇总（类型 / 原因 / 亮光时长 / 进线时长）----")
    for r in recs:
        feed = r.get("spheroidization_wire_entry_total_duration")
        print("%d  %s  亮光=%s  进线=%s" % (
            r["abnormal_type"], r.get("abnormal_reason") or "(无)",
            r.get("actual_spheroidization_time") or "-",
            ("%.0fs" % feed) if feed is not None else "-"))
    print("---- 融合状态 ----")
    print(json.dumps(fusion.status(), ensure_ascii=False))
    if recs:
        print("A 炉 actual_spheroidization_time = %s（物理亮光 80s；"
              "检测口径 = 物理 + 4.0s 结束确认 - 0.3s 开始确认，约 83.7s 属正常）"
              % recs[0].get("actual_spheroidization_time"))

    if problems:
        print("---- 与期望不一致 ----")
        for p in problems:
            print("  !", p)
        return 1
    print("---- 与期望一致：6 条记录，类型 0,3,4,2,1,5 ----")
    return 0


if __name__ == "__main__":
    sys.exit(main())
