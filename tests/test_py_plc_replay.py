"""pytest 化包装（T-007b 第二批）：tests/test_plc_replay.py 的 1452 炉真实 PLC 回放。

原脚本保留不动，本文件复用其 load_heats() 与 A/B/D/E 分节驱动：
  - A/B/D/E 节直接调用原脚本 part 函数，把原 check() 记录的失败转为 pytest 断言
    （每个用例前清空 orig.FAILS，跑完断言为空）
  - C 节故障注入逐条改写为独立 pytest 用例（口径与原脚本完全一致）

1452 炉真实数据回放，统一标 @pytest.mark.slow（默认跑法仍执行）。
原脚本在数据缺失时模块级 sys.exit(0)（已知小瑕疵），包装侧先查数据再 import。
"""
import os

import pytest

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                     "plc_replay_all.jsonl")
if not os.path.exists(_DATA):
    pytest.skip("缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）",
                allow_module_level=True)

import test_plc_replay as orig  # noqa: E402,F401  (同时完成 sys.path 初始化)

from vpmin.domain.heat import (TYPE_FEED_BREAK, TYPE_NO_LIGHT,  # noqa: E402
                               HeatConfig, HeatTracker)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def heats():
    data = orig.load_heats()
    assert data, "plc_replay_all.jsonl 里没有可回放的炉"
    return data


def _run_part(fn, heats):
    """跑原脚本分节驱动，check() 记录的失败 → pytest 失败。"""
    orig.FAILS.clear()
    fn(heats)
    assert orig.FAILS == [], "原脚本 check 项未通过: " + "; ".join(orig.FAILS)


# ---------- A. 位级回放（最近 60 炉） ----------

def test_a_bit_replay_no_false_positive(heats):
    _run_part(orig.part_a_bit_replay, heats)


# ---------- B. 事件级回放（全部 1452 炉） ----------

def test_b_event_replay_counts_and_types(heats):
    _run_part(orig.part_b_event_replay, heats)


# ---------- C. 故障注入（逐条） ----------

@pytest.fixture(scope="module")
def base(heats):
    return heats[-1]


def test_c1_no_end_no_light_reports_type2_at_180s(base):
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    assert not tr.tick(base.start_ts + 179), "179s 阈值没到，不应报"
    out = tr.tick(base.start_ts + 181)
    assert out and out[0].judged_type == TYPE_FEED_BREAK, \
        f"181s 应报类型2（无结束且无亮光）: {out[0].reason if out else '无输出'}"


def test_c1b_light_on_veto_type2(base):
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    tr.on_event("light_start", base.start_ts + 13)
    assert not tr.tick(base.start_ts + 200), "亮着的时候 200s 不报"
    assert not tr.tick(base.start_ts + 400), "亮着的时候 400s 仍不报（长炉放过）"


def test_c1c_silence_window_then_report(base):
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    tr.on_event("light_start", base.start_ts + 13)
    tr.on_event("light_end", base.start_ts + 200)
    assert not tr.tick(base.start_ts + 210), "刚熄 10s（<静默 60s）不报"
    out = tr.tick(base.start_ts + 300)
    assert out and out[0].judged_type == TYPE_FEED_BREAK, \
        f"熄了 100s 仍无结束位应报类型2: {out[0].reason if out else '无输出'}"


def test_c2_short_feed_40s_reports_immediately(base):
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    out = tr.on_event("feed_end", base.start_ts + 40, raw_ts=base.start_ts + 64)
    assert out and out[0].judged_type == TYPE_FEED_BREAK, \
        f"40s 进线应立刻报类型2: {out[0].reason if out else '无输出'}"


def test_c3_no_action_after_arrival_reports_type1(base):
    tr = HeatTracker(HeatConfig(no_action_sec=120))
    tr.on_event("arrival", base.start_ts, weight=base.weight)
    assert not tr.tick(base.start_ts + 119), "119s 不报"
    out = tr.tick(base.start_ts + 121)
    assert out and out[0].judged_type == TYPE_NO_LIGHT, \
        f"121s 应报类型1: {out[0].reason if out else '无输出'}"


def test_c4_light_vetoes_type1(base):
    tr = HeatTracker(HeatConfig(no_action_sec=120))
    tr.on_event("arrival", base.start_ts, weight=base.weight)
    tr.on_event("light_start", base.start_ts + 40)
    assert not tr.tick(base.start_ts + 200), "有亮光就不报类型1"


# ---------- D. 重量启发式可靠性 ----------

def test_d_weight_heuristic_reliability(heats):
    _run_part(orig.part_d_weight_heuristic, heats)


# ---------- E. PlcLoop 回路 ----------

def test_e_plcloop_callback_and_persist(heats):
    _run_part(orig.part_e_plcloop, heats)
