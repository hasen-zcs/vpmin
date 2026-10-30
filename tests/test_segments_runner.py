"""端到端测试：分段（类型5）与类型3/4 在 runner 里的实际行为。

用合成帧流驱动真实 ReactionRunner（只把 probe/painter/sink/counter 换成桩），
验证三件事：
  1. merge_gap=0 时，中间断一下 = 两条独立记录（各自判失败）—— 现在生产的病
  2. merge_gap>0 时，同一炉续段 = 一条记录 + 分段明细 + 类型5
  3. 类型3/4 端到端判定正确，落盘字段老 7 列不动、新字段追加

用法：
    python3 tests/test_segments_runner.py
"""

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.curve import CurveBuffer
from vpmin.domain.judge import TYPE_OVER, TYPE_PAUSE, TYPE_UNDER
from vpmin.domain.models import FrameMetrics
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


class StubProbe:
    """按时间脚本给占比：on 区间 start_pct=90/end_pct=5，off 区间反过来。"""

    def __init__(self, windows):
        self.windows = windows          # [(t0, t1), ...] 亮光区间
        self.last = None

    def measure(self, frame, elapsed):
        on = any(t0 <= elapsed < t1 for t0, t1 in self.windows)
        m = FrameMetrics(elapsed, 60.0, 90.0 if on else 5.0, 5.0 if on else 95.0)
        self.last = m
        return m


class StubPainter:
    def draw(self, points, start, end, start_pixel=None):
        return ""


class StubSink:
    def __init__(self):
        self.records = []

    def build(self, count, start_wall, end_wall, verdict, image, extras=None):
        rec = {"reaction_count": count,
               "detection_time": start_wall,
               "spheroidization_start_time": start_wall,
               "spheroidization_end_time": end_wall,
               "actual_spheroidization_time": verdict.text,
               "spheroidization_abnormal": verdict.text,
               "spheroidization_image": image}
        for k, v in (extras or {}).items():
            if k not in rec:
                rec[k] = v
        return rec

    def write(self, record):
        self.records.append(record)


class StubCounter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n

    def rollback(self):
        self.n = max(0, self.n - 1)
        return self.n


def run(windows, merge_gap, pause_threshold=10.0, until=220.0, flush_at=None):
    """按 25fps 驱动一轮，返回 runner 吐出的记录（落盘由 AnalysisLoop 做，这里直接取 drain）。"""
    standard = StandardPolicy((60.0, 90.0), 5.0)
    standard.update({"standard_spheroidization_time_lower": 60,
                     "standard_spheroidization_time_upper": 90})
    sink = StubSink()
    runner = ReactionRunner(
        probe=StubProbe(windows),
        thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(0.2, 4.0),
        standard=standard,
        curve=CurveBuffer(5.0),
        painter=StubPainter(),
        sink=sink,
        counter=StubCounter(),
        base=datetime(2026, 10, 29, 12, 0, 0),
        merge_gap=merge_gap,
        pause_threshold=pause_threshold,
    )
    t = 0.0
    out = []
    flushed = False
    while t < until:
        if flush_at is not None and not flushed and t >= flush_at:
            runner.flush_pending()                 # 模拟 PLC 到位：炉边界强制结算
            out.extend(rec for rec, _e, _m in runner.drain())
            flushed = True
        runner.process(None, t)
        out.extend(rec for rec, _end, _m in runner.drain())
        t = round(t + 0.04, 3)
    runner.flush_pending()
    out.extend(rec for rec, _end, _m in runner.drain())
    return out


def main():
    print("1. 断一下（亮 10~40，停 15s，再亮 55~100），merge_gap=0")
    recs = run([(10, 40), (55, 100)], merge_gap=0.0)
    check("拆成 2 条独立记录（这是现在生产的病）", len(recs) == 2, f"实际 {len(recs)} 条")
    if len(recs) == 2:
        check("第一条判失败（时长太短）", recs[0]["spheroidization_abnormal"] == "失败",
              recs[0]["spheroidization_abnormal"])
        check("两条都没有分段明细", all("spheroidization_segments" not in r for r in recs))

    print("\n2. 同一场景，merge_gap=120，暂停阈值 10s")
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=10.0)
    check("合并成 1 条记录", len(recs) == 1, f"实际 {len(recs)} 条")
    if len(recs) == 1:
        r = recs[0]
        check("判类型5", r.get("abnormal_type") == TYPE_PAUSE, f"type={r.get('abnormal_type')}")
        check("原因写清第 2 段前停顿", "第 2 段" in r.get("abnormal_reason", "")
              and "停顿" in r.get("abnormal_reason", ""), r.get("abnormal_reason"))
        segs = r.get("spheroidization_segments") or []
        check("分段明细 2 段", len(segs) == 2, f"{len(segs)} 段")
        if len(segs) == 2:
            check("第 1 段 gap 为空", segs[0]["gap_before_sec"] is None)
            check("第 2 段 gap≈11.2s（40+4 → 55+0.2）",
                  segs[1]["gap_before_sec"] is not None and 10.9 <= segs[1]["gap_before_sec"] <= 11.5,
                  f"gap={segs[1]['gap_before_sec']}")
            check("每段带起止墙钟", bool(segs[0]["start"]) and bool(segs[1]["end"]))
        check("老 7 字段都还在", all(k in r for k in (
            "reaction_count", "detection_time", "spheroidization_start_time",
            "spheroidization_end_time", "actual_spheroidization_time",
            "spheroidization_abnormal", "spheroidization_image")))

    print("\n3. 连续亮 10~100（约 93.8s）→ 类型3 超上限")
    recs = run([(10, 100)], merge_gap=120.0, pause_threshold=10.0)
    check("1 条记录", len(recs) == 1, f"实际 {len(recs)} 条")
    if recs:
        r = recs[0]
        check("判类型3", r.get("abnormal_type") == TYPE_OVER, f"type={r.get('abnormal_type')}")
        check("原因带实际值和上限", "> 上限 90" in r.get("abnormal_reason", ""),
              r.get("abnormal_reason"))
        check("单段不输出分段明细", "spheroidization_segments" not in r)

    print("\n4. 亮 10~60（约 53.8s）→ 类型4 低于下限")
    recs = run([(10, 60)], merge_gap=120.0, pause_threshold=10.0)
    if recs:
        check("判类型4", recs[0].get("abnormal_type") == TYPE_UNDER,
              f"type={recs[0].get('abnormal_type')}")

    print("\n5. 正常炉（亮 10~80，约 73.8s）→ 0 正常")
    recs = run([(10, 80)], merge_gap=120.0, pause_threshold=10.0)
    if recs:
        check("判正常", recs[0].get("abnormal_type") == 0, f"type={recs[0].get('abnormal_type')}")
        check("成功", recs[0]["spheroidization_abnormal"] == "成功")

    print("\n6. 停顿阈值抬高到 20s → 同样场景不再算停顿（落回类型3）")
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=20.0)
    if recs:
        check("gap≈11.2 < 20 → 不是类型5", recs[0].get("abnormal_type") != TYPE_PAUSE,
              f"type={recs[0].get('abnormal_type')}")

    print("\n7. 炉边界强制结算：两炉亮光间隔 86s（< merge_gap 120）")
    recs = run([(10, 100), (190, 280)], merge_gap=120.0, pause_threshold=10.0, until=400.0)
    check("不结算时会被合成 1 条（跨炉合并的病）", len(recs) == 1, f"{len(recs)} 条")
    recs = run([(10, 100), (190, 280)], merge_gap=120.0, pause_threshold=10.0,
               until=400.0, flush_at=150.0)
    check("到位事件强制结算 → 拆成 2 条", len(recs) == 2, f"{len(recs)} 条")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
