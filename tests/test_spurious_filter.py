"""杂光过滤（guard）自测：开门杂光不该被当成一炉反应。

规则：起判后 guard_window 秒内，亮光占比峰值必须 ≥ guard_peak，否则丢弃。

用法：/home/hasen/miniconda3/bin/python tests/test_spurious_filter.py
"""

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.curve import CurveBuffer
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


class Probe:
    def __init__(self, series):
        self.series, self.i, self.last = series, 0, None
        self.start_pixel = self.end_pixel = 180

    def measure(self, frame, elapsed):
        if self.i >= len(self.series):
            return None
        sp, ep = self.series[self.i]
        self.i += 1
        self.last = type("M", (), {"elapsed": elapsed, "start_pct": sp, "end_pct": ep, "mean_gray": 150.0})()
        return self.last


class Painter:
    def __init__(self):
        self.n = 0

    def draw(self, points, start_t=None, end_t=None, start_pixel=None):
        self.n += 1
        return "img"


class Sink:
    def __init__(self):
        self.rows = []

    def build(self, count, start, end, verdict, image):
        return {"n": count, "start": start, "end": end, "dur": verdict.duration}

    def write(self, r):
        self.rows.append(r)


class Counter:
    def __init__(self):
        self.value = 0

    def next(self):
        self.value += 1
        return self.value

    def rollback(self):
        if self.value > 0:
            self.value -= 1
        return self.value


def build(*segs, fps=25.0):
    out = []
    for sec, sp in segs:
        out += [(sp, 100.0 - sp)] * int(sec * fps)
    return out


def run(series, window=3.0, peak=80.0, min_dur=20.0):
    sink, painter = Sink(), Painter()
    r = ReactionRunner(
        probe=Probe(series), thresholds=Thresholds(40, 70),
        machine=ReactionStateMachine(0.2, 4.0), standard=StandardPolicy((60, 90), 5.0),
        curve=CurveBuffer(5.0), painter=painter, sink=sink, counter=Counter(),
        base=datetime(2026, 10, 29, 15, 0, 0), merge_gap=0.0,
        guard_window=window, guard_peak=peak, min_duration=min_dur)
    for i in range(len(series)):
        r.process(None, i / 25.0)
    r.flush_pending()
    for rec, t, m in r.drain():
        sink.write(rec)
    return r, sink.rows


def main():
    print("1. 正常反应（起判 90%，持续 100s）应保留")
    s = build((3, 0), (100, 92), (6, 5))
    r, rows = run(s)
    check("记录 1 条", len(rows) == 1, f"{len(rows)}")
    check("序号 1", rows and rows[0]["n"] == 1, str(rows[0]["n"]) if rows else "-")
    check("未丢弃", r.dropped == 0, f"dropped={r.dropped}")

    print()
    print("2. 开门杂光（起判 42%，持续 20s）应丢弃")
    s = build((3, 0), (20, 42), (6, 5))
    r, rows = run(s)
    check("记录 0 条", len(rows) == 0, f"{len(rows)}")
    check("丢弃计数 ≥1", r.dropped >= 1, f"dropped={r.dropped}")

    print()
    print("3. 起判 90% 但 1 秒就掉回 5% → 结束太快，丢弃")
    s = build((3, 0), (1, 90), (10, 5), (10, 0))
    r, rows = run(s)
    check("记录 0 条", len(rows) == 0, f"{len(rows)}")
    check("丢弃计数 ≥1", r.dropped >= 1, f"dropped={r.dropped}")

    print()
    print("4. 杂光丢弃后紧跟真反应：序号不能跳号")
    s = build((3, 0), (20, 42), (5, 0), (100, 92), (6, 5))
    r, rows = run(s)
    check("记录 1 条", len(rows) == 1, f"{len(rows)}")
    check("序号是 1（没被杂光占掉）", rows and rows[0]["n"] == 1,
          str(rows[0]["n"]) if rows else "-")

    print()
    print("5. guard 关闭（window=0）时行为不变：杂光照样成记录")
    s = build((3, 0), (20, 42), (6, 5))
    r, rows = run(s, window=0.0, min_dur=0.0)
    check("记录 1 条（旧行为）", len(rows) == 1, f"{len(rows)}")

    print()
    print("6. 起判 75% 持续（低于 80 阈值）→ 丢弃")
    s = build((3, 0), (20, 75), (6, 5))
    r, rows = run(s)
    check("记录 0 条", len(rows) == 0, f"{len(rows)}")

    print()
    print("7. 起判 85% 持续（刚好过阈值）→ 保留")
    s = build((3, 0), (100, 85), (6, 5))
    r, rows = run(s)
    check("记录 1 条", len(rows) == 1, f"{len(rows)}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项: {FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
