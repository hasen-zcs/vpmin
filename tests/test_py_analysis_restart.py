"""pytest 化包装（T-007b 第二批）：tests/test_analysis_restart.py 的 base 墙钟重置回归。

原脚本保留不动，本文件复用其 _Runner 与相同场景序列，逐条改写为 pytest 断言
（口径与原脚本完全一致：start→stop→+600s→start，base 必须跟着重新对齐）。
"""
import test_analysis_restart as orig  # noqa: F401  (同时完成 sys.path 初始化)

from datetime import datetime, timedelta

import pytest

from vpmin.adapters.formats import wall
from vpmin.app.analysis import AnalysisLoop


@pytest.fixture(scope="module")
def scenario():
    """复刻原脚本 main() 的时序：第一次 start → stop → 600s 后再 start。"""
    now = [1000.0]
    base_wall = datetime(2026, 10, 29, 18, 0, 0)
    loop = AnalysisLoop(
        probe=None, runner=orig._Runner(), painter=None,
        clock=lambda: now[0],
        wall_clock=lambda: base_wall + timedelta(seconds=now[0] - 1000.0))
    loop.start()
    b1 = loop.runner.base
    loop.stop()
    now[0] += 600
    loop.start()
    b2 = loop.runner.base
    return b1, b2


def test_base_aligned_to_now_on_first_start(scenario):
    b1, _b2 = scenario
    assert b1.year >= 2025, f"base 应跟当下对齐，实际 {b1}"


def test_base_realigned_after_stop_restart(scenario):
    b1, b2 = scenario
    drift = (b2 - b1).total_seconds()
    assert 590 <= drift <= 610, f"base 重新对齐间隔应≈600s，实际 {drift:.1f}s"


def test_wall_clock_monotonic_across_restart(scenario):
    b1, b2 = scenario
    w1, w2 = wall(b1, 0.0), wall(b2, 0.0)
    assert w2 > w1, f"墙钟倒流：{w1} → {w2}"
