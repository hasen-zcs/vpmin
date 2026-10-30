"""真实数据回归：用今天 50 炉的亮光时长 + R51 逐帧数据，验证 3/4/5 判定。

数据来源：
  ① tests/data/real_50_furnaces_1029.csv —— 2026-10-29 影子 × 生产 DB 逐炉对比（亮光时长/生产原判定）
  ② tests/data/real_r51_frames.csv      —— 生产实测逐帧（25fps，那一炉亮光 90.47s）

用法：
    python3 tests/test_real_types_345.py
"""

import csv
import os
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.curve import CurveBuffer
from vpmin.domain.judge import (TYPE_NORMAL, TYPE_OVER, TYPE_PAUSE, TYPE_UNDER,
                               judge)
from vpmin.domain.models import FrameMetrics, Window
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

DATA50 = os.path.join(HERE, "tests", "data", "real_50_furnaces_1029.csv")
DATA_R51 = os.path.join(HERE, "tests", "data", "real_r51_frames.csv")
if not (os.path.exists(DATA50) and os.path.exists(DATA_R51)):
    print("SKIP: 缺少生产真实数据 real_50_furnaces_1029.csv / real_r51_frames.csv（不随公开仓库发布）")
    sys.exit(0)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def part1_50_furnaces():
    print("1. 今天 50 炉真实亮光时长 × 标准 60~90（口径：亮光时长，无分段）")
    rows = list(csv.DictReader(open(DATA50, encoding="utf-8-sig")))
    w = Window(60.0, 90.0, "后端上下限")
    agree = 0
    types = Counter()
    for r in rows:
        dur = _num(r["亮光时长(s)"])
        v = judge(dur, w)
        types[v.abnormal_type] += 1
        if v.text == r["生产原判定"]:
            agree += 1
    total = len(rows)
    print(f"     炉数 {total}；判定分布 {dict(types)}")
    check(f"与生产原判定一致 {agree}/{total}", agree == total, f"{agree}/{total}")

    # 边界附近抽查：成功那几炉必须落在 60~90 内
    ok_rows = [r for r in rows if r["生产原判定"] == "成功"]
    bad = [r for r in ok_rows if not (60.0 <= _num(r["亮光时长(s)"]) <= 90.0)]
    check(f"成功的 {len(ok_rows)} 炉时长都在 60~90 内", not bad,
          "; ".join(f"{r['DB记录ID']}:{r['亮光时长(s)']}" for r in bad))

    overs = [r for r in rows if _num(r["亮光时长(s)"]) > 90.0]
    unders = [r for r in rows if _num(r["亮光时长(s)"]) < 60.0]
    print(f"     超过标准 {len(overs)} 炉；低于标准 {len(unders)} 炉")
    if overs:
        r = overs[0]
        v = judge(_num(r["亮光时长(s)"]), w)
        check("类型3 原因带实际值和上限", "超过标准值" in v.abnormal_reason
              and "> 上限 90" in v.abnormal_reason, v.abnormal_reason)


class _Probe:
    def __init__(self, rows):
        self.rows = rows
        self.i = 0
        self.last = None

    def measure(self, frame, elapsed):
        # 逐帧推进（elapsed 由 runner 传入，这里按顺序取）
        t, pct = self.rows[self.i]
        self.i = min(self.i + 1, len(self.rows) - 1)
        m = FrameMetrics(t, 60.0, pct, 100.0 - pct)
        self.last = m
        return m


class _Painter:
    def draw(self, points, start, end, start_pixel=None):
        return ""


class _Sink:
    def build(self, count, start_wall, end_wall, verdict, image, extras=None):
        rec = {"duration": verdict.duration, "text": verdict.text,
               "type": verdict.abnormal_type, "reason": verdict.abnormal_reason}
        rec.update(extras or {})
        return rec

    def write(self, record):
        pass


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n

    def rollback(self):
        self.n = max(0, self.n - 1)
        return self.n


def part2_r51_real_frames():
    print("\n2. R51 真实逐帧（4130 帧，25fps）× merge_gap=120 / 停顿阈值 10s")
    rows = []
    with open(DATA_R51, encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for x in rd:
            if len(x) >= 2 and x[0]:
                rows.append((float(x[0]), float(x[1])))
    standard = StandardPolicy((60.0, 90.0), 5.0)
    standard.update({"standard_spheroidization_time_lower": 60,
                     "standard_spheroidization_time_upper": 90})
    runner = ReactionRunner(
        probe=_Probe(rows), thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(0.2, 4.0), standard=standard,
        curve=CurveBuffer(5.0), painter=_Painter(), sink=_Sink(),
        counter=_Counter(), base=datetime(2026, 10, 29, 10, 34, 0),
        merge_gap=120.0, pause_threshold=10.0)
    out = []
    for t, _pct in rows:
        runner.process(None, t)
        out.extend(rec for rec, _e, _m in runner.drain())
    runner.flush_pending()
    out.extend(rec for rec, _e, _m in runner.drain())

    check("出 1 条记录", len(out) == 1, f"实际 {len(out)} 条")
    if out:
        r = out[0]
        print(f"     时长 {r['duration']:.2f}s，判定 {r['text']}，类型 {r['type']}")
        if r["reason"]:
            print(f"     原因 {r['reason']}")
        check("时长≈90.47s（与生产实测一致）", 90.0 <= r["duration"] <= 91.0,
              f"{r['duration']:.2f}s")
        check("单段（这一炉没有真停顿）", "spheroidization_segments" not in r)
        check("类型与时长自洽", (r["type"] == TYPE_OVER) == (r["duration"] > 90.0),
              f"type={r['type']} dur={r['duration']:.2f}")


def main():
    part1_50_furnaces()
    part2_r51_real_frames()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
