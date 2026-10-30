"""回归：停止分析 → 再开始，记录时间戳必须跟着走，不能倒回服务启动时刻。

这是接线 PLC 融合采集时发现的真 bug：elapsed 的 0 点每次开始都会重置，
但 runner.base（墙钟基准）原来只在建服务时设一次 → 停止再开始后所有墙钟
时间会倒回，合并器按时间窗配对就会错配。

用法：
    python3 tests/test_analysis_restart.py
"""

import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.app.analysis import AnalysisLoop
from vpmin.adapters.formats import wall

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


class _Runner:
    def __init__(self):
        self.base = datetime(2020, 1, 1, 0, 0, 0)
        self.reaction_start = None
        self.reaction_end = None
        self.curve = None

    def reset(self):
        self.reaction_start = None


def main():
    now = [1000.0]
    base_wall = datetime(2026, 10, 29, 18, 0, 0)
    loop = AnalysisLoop(probe=None, runner=_Runner(), painter=None,
                        clock=lambda: now[0],
                        wall_clock=lambda: base_wall + timedelta(seconds=now[0] - 1000.0))

    print("1. 第一次开始：base 对齐到当下")
    loop.start()
    b1 = loop.runner.base
    check("base 不再是 2020 年", b1.year >= 2025, str(b1))

    print("\n2. 停止 → 10 分钟后重新开始")
    loop.stop()
    now[0] += 600
    loop.start()
    b2 = loop.runner.base
    drift = (b2 - b1).total_seconds()
    check("base 跟着重新对齐（间隔≈600s）", 590 <= drift <= 610, f"{drift:.1f}s")

    print("\n3. 时间戳单调：第二次的 elapsed=0 对应的墙钟晚于第一次")
    w1 = wall(b1, 0.0)
    w2 = wall(b2, 0.0)
    check("墙钟没有倒流", w2 > w1, f"{w1} → {w2}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
