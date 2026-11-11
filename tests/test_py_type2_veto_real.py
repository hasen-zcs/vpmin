"""pytest 化包装（T-007a 第一批）：tests/test_type2_veto_real.py 的真实回放用例。

真实 PLC 数据（tests/data/plc_replay_all.jsonl，生产数据不随公开仓库发布）缺失时 skip。
慢速回放，统一标 @pytest.mark.slow。
"""
import os

import pytest

import test_type2_veto_real as orig  # noqa: F401

from vpmin.domain.heat import TYPE_FEED_BREAK, HeatConfig, HeatTracker

pytestmark = pytest.mark.slow


def _require_data():
    if not os.path.exists(orig.DATA):
        pytest.skip("缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）")


@pytest.fixture(scope="module")
def heats():
    _require_data()
    data = orig.load_long_heats()
    assert data, "真实数据里没有 >180s 的长进线炉，用例前提不成立"
    return data


def test_with_light_no_false_feed_break(heats):
    _require_data()
    _tr, closed = orig.replay(heats, with_light=True)
    bad = [h for h in closed if h.judged_type == TYPE_FEED_BREAK]
    assert not bad, "; ".join(h.reason for h in bad[:2])
    got = len([h for h in closed if h.feed_end_ts is not None])
    assert got == len(heats)


def test_without_light_all_feed_break(heats):
    _require_data()
    _tr, closed = orig.replay(heats, with_light=False)
    flagged = [h for h in closed if h.judged_type == TYPE_FEED_BREAK]
    assert len(flagged) == len(heats)


def test_light_stream_unavailable_no_judgement(heats):
    _require_data()
    tr = HeatTracker(HeatConfig())
    tr.light_stream_ok = False
    hs, he, _dur, w = heats[0]
    tr.on_event("arrival", hs - 30, weight=w)
    tr.on_event("feed_start", hs, weight=w)
    assert not tr.tick(hs + 500.0)
