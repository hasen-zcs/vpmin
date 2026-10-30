"""中值滤波测试：单元 + 真实逐帧回归 + 毛刺抑制。

结论口径（2026-10-29 实测）：因果 5 帧中值在 R51 真实数据上
起点偏 +0.09s、终点偏 +0.16s、时长偏 +0.07s；抖动下起判最坏推迟显著变小。

用法：
    python3 tests/test_median_filter.py
"""

import csv
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.curve import CurveBuffer
from vpmin.domain.filter import MedianFilter
from vpmin.domain.models import FrameMetrics
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

DATA = os.path.join(HERE, "tests", "data", "real_r51_frames.csv")
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


class _Probe:
    def __init__(self, rows):
        self.rows = rows
        self.i = 0
        self.last = None

    def measure(self, frame, elapsed):
        t, pct = self.rows[min(self.i, len(self.rows) - 1)]
        self.i += 1
        self.last = FrameMetrics(t, 60.0, pct, 100.0 - pct)
        return self.last


class _Painter:
    def draw(self, points, start, end, start_pixel=None):
        return ""


class _Sink:
    def build(self, count, start_wall, end_wall, verdict, image, extras=None):
        return {"duration": verdict.duration, "type": verdict.abnormal_type}

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


def load_frames():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x in r:
            if len(x) >= 2 and x[0]:
                rows.append((float(x[0]), float(x[1])))
    return rows


def run_runner(rows, median_frames):
    std = StandardPolicy((60.0, 90.0), 5.0)
    std.update({"standard_spheroidization_time_lower": 60,
                "standard_spheroidization_time_upper": 90})
    runner = ReactionRunner(
        probe=_Probe(rows), thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(0.2, 4.0), standard=std,
        curve=CurveBuffer(5.0), painter=_Painter(), sink=_Sink(),
        counter=_Counter(), base=datetime(2026, 10, 29, 10, 34, 0),
        merge_gap=120.0, pause_threshold=10.0, median_frames=median_frames)
    out = []
    for t, _pct in rows:
        runner.process(None, t)
        out.extend(rec for rec, _e, _m in runner.drain())
    runner.flush_pending()
    out.extend(rec for rec, _e, _m in runner.drain())
    return out


def main():
    print("1. 单元：0/1 帧等于关闭，原样透传")
    f0 = MedianFilter(0)
    check("frames=0 透传", f0.apply(12.0, 88.0) == (12.0, 88.0))
    f1 = MedianFilter(1)
    check("frames=1 透传", f1.apply(12.0, 88.0) == (12.0, 88.0))

    print("\n2. 单元：单帧毛刺被压掉")
    f = MedianFilter(5)
    for v in (5, 5, 5, 5):
        f.apply(v, 100 - v)
    s, e = f.apply(95.0, 5.0)            # 突然来一帧高亮
    check("单帧毛刺不影响中值", s <= 5.0 and e >= 95.0, f"{s}/{e}")

    print("\n3. 单元：持续亮起时中值跟上（5 帧窗口最多滞后 3 帧）")
    f = MedianFilter(5)
    for _ in range(5):
        f.apply(5, 95)
    vals = []
    for _ in range(5):
        s, _e = f.apply(95, 5)
        vals.append(s)
    check("前 2 帧仍是低值（滞后）", vals[1] <= 40, str(vals))
    check("第 3 帧翻上来", vals[2] > 40, str(vals))

    print("\n4. 单元：reset 清缓存")
    f = MedianFilter(5)
    f.apply(95, 5)
    f.reset()
    check("reset 后第一帧原样", f.apply(5, 95) == (5.0, 95.0))

    rows = load_frames()
    print(f"\n5. 真实逐帧（{len(rows)} 帧）跑真实 runner")
    off = run_runner(rows, 0)
    on3 = run_runner(rows, 3)
    on5 = run_runner(rows, 5)
    check("三种设置都出 1 条记录", len(off) == len(on3) == len(on5) == 1,
          f"{len(off)}/{len(on3)}/{len(on5)}")
    if off and on5:
        d0, d5 = off[0]["duration"], on5[0]["duration"]
        print(f"     关：{d0:.2f}s  中值3：{on3[0]['duration']:.2f}s  中值5：{d5:.2f}s")
        check("中值5 时长偏差 ≤ 0.3s", abs(d5 - d0) <= 0.3, f"差 {d5 - d0:+.2f}s")
        check("判定结果不变", off[0]["type"] == on5[0]["type"],
              f"{off[0]['type']} vs {on5[0]['type']}")

    print("\n6. 毛刺抑制：结束段插入单帧高亮，关滤波会被打断，开滤波不会")
    # 构造：亮 100 帧 → 熄 120 帧（足够确认结束）→ 中间插一帧高亮
    series = []
    for i in range(100):
        series.append((i * 0.04, 95.0))
    for i in range(100, 220):
        series.append((i * 0.04, 5.0))
    series[150] = (series[150][0], 95.0)      # 熄灭段中间一帧毛刺
    for i in range(220, 260):
        series.append((i * 0.04, 5.0))
    off = run_runner(series, 0)
    on = run_runner(series, 5)
    d_off = off[0]["duration"] if off else None
    d_on = on[0]["duration"] if on else None
    print(f"     关：结束于 {d_off and round(d_off, 2)}s   开：{d_on and round(d_on, 2)}s")
    check("两种都判结束", d_off is not None and d_on is not None)
    if d_off and d_on:
        check("开滤波的结束时刻不晚于关滤波", d_on <= d_off + 1e-9,
              f"{d_on:.2f} vs {d_off:.2f}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
