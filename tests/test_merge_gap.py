"""断点续接（merge_gap）自测：同一炉中间短暂熄灭，不该被拆成两炉。

验证四件事：
  1. merge_gap=0   → 仍然是两条记录（与旧行为一致，不改变历史口径）
  2. merge_gap=10  → 合并成一条，时长 = 两段之和 + 中间间隙
  3. 曲线也是连着的：合并后曲线点数 = 两段之和（图不会只剩后半截）
  4. 间隙超过 merge_gap 时仍然分成两条（不能无限吞）

用法：/home/hasen/miniconda3/bin/python tests/test_merge_gap.py
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


class FakeMetrics:
    def __init__(self, elapsed, start_pct, end_pct, mean_gray=150.0):
        self.elapsed = elapsed
        self.start_pct = start_pct
        self.end_pct = end_pct
        self.mean_gray = mean_gray


class FakeProbe:
    def __init__(self, series):
        self.series = series          # [(start_pct, end_pct), ...] 每帧
        self.i = 0
        self.last = None
        self.start_pixel = 180
        self.end_pixel = 180

    def measure(self, frame, elapsed):
        if self.i >= len(self.series):
            return None
        sp, ep = self.series[self.i]
        self.i += 1
        self.last = FakeMetrics(elapsed, sp, ep)
        return self.last


class FakePainter:
    def __init__(self):
        self.draws = []

    def draw(self, points, start_t=None, end_t=None, start_pixel=None):
        self.draws.append(list(points))
        return "data:image/jpeg;base64,FAKE"


class FakeSink:
    def __init__(self):
        self.rows = []

    def build(self, count, start, end, verdict, image):
        row = {"reaction_count": count, "start": start, "end": end,
               "duration": verdict.duration, "status": verdict.text, "image": image}
        return row

    def write(self, record):
        self.rows.append(record)


class FakeCounter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def make_runner(merge_gap):
    painter = FakePainter()
    return ReactionRunner(
        probe=None,
        thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(start_confirm=0.2, end_confirm=4.0),
        standard=StandardPolicy((60.0, 90.0), 5.0),
        curve=CurveBuffer(preroll_sec=5.0),
        painter=painter,
        sink=FakeSink(),
        counter=FakeCounter(),
        base=datetime(2026, 10, 29, 12, 0, 0),
        merge_gap=merge_gap,
    ), painter


def build_series(seg_a=6.0, gap=6.0, seg_b=20.0, fps=25.0):
    """造一串帧：静默 3s → 亮 6s → 熄 6s → 亮 20s → 静默 12s

    间隙必须 > end_confirm(4s)，否则状态机不会判结束，测不到断点场景。
    """
    frames = []
    def add(sec, sp, ep):
        for _ in range(int(sec * fps)):
            frames.append((sp, ep))
    add(3.0, 0.0, 100.0)          # 静默
    add(seg_a, 95.0, 5.0)         # 第一段亮光
    add(gap, 0.0, 100.0)          # 中间熄灭（> 结束确认 4s，会判结束）
    add(seg_b, 95.0, 5.0)         # 第二段亮光
    add(12.0, 0.0, 100.0)         # 静默，让合并窗口过期
    return frames


def run(merge_gap, series):
    runner, painter = make_runner(merge_gap)
    runner.probe = FakeProbe(series)
    fps = 25.0
    for i in range(len(series)):
        runner.process(frame=None, elapsed=i / fps)
    runner.flush_pending()
    return runner.drain(), painter


def main():
    series = build_series()

    print("1. merge_gap=0（关闭）应保持旧行为：两段各记一条")
    out0, _ = run(0.0, series)
    check("记录条数 = 2", len(out0) == 2, f"实测 {len(out0)}")
    if len(out0) == 2:
        d0 = out0[0][0]["duration"]
        d1 = out0[1][0]["duration"]
        # 起点 3.2s；第一段结束确认在 9+4=13s；第二段起判 15.2s，结束确认 35+4=39s
        check("第一段约 9.8s（6s亮 + 4s结束确认）", 9.0 <= d0 <= 10.5, f"{d0:.2f}s")
        check("第二段约 23.8s（20s亮 + 4s结束确认）", 22.5 <= d1 <= 25.0, f"{d1:.2f}s")

    print()
    print("2. merge_gap=10 应合并成一条")
    out1, painter1 = run(10.0, series)
    check("记录条数 = 1", len(out1) == 1, f"实测 {len(out1)}")
    if out1:
        rec = out1[0][0]
        check("合并后时长 ≈ 35.8s（3.2→39，含间隙）", 34.0 <= rec["duration"] <= 37.0,
              f"{rec['duration']:.2f}s")
        check("序号仍是 1（没有多占号）", rec["reaction_count"] == 1, str(rec["reaction_count"]))
        check("落盘 JSON 不加新字段（保持 7 字段兼容）",
              out1[0][0].get("merged_segments") is None, "ok")

    print()
    print("3. 曲线要接上：合并后的曲线点数应约为两段之和")
    if out1 and painter1.draws:
        pts = painter1.draws[-1]
        # 首段含 5s 前置基线 + 6s + 间隙 + 20s ≈ 31s，25fps ≈ 775 点
        check("曲线点数 > 700（后半段没丢）", len(pts) > 700, f"{len(pts)} 点")
        span = pts[-1][0] - pts[0][0]
        check("曲线时间跨度 > 28s（中间间隙也接上了）", span > 28.0, f"{span:.1f}s")

    print()
    print("4. 间隙很大时不该合并")
    series2 = build_series(seg_a=6.0, gap=30.0, seg_b=20.0)
    out2, _ = run(10.0, series2)
    check("间隙 30s > merge_gap 10s → 仍是 2 条", len(out2) == 2, f"实测 {len(out2)}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项: {FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
