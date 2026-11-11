"""pytest 化包装（T-007a 第一批）：tests/test_heat_tracker.py 的类型 1/2 状态机用例。

原脚本保留不动，口径与原脚本逐条一致。
"""
import test_heat_tracker as orig  # noqa: F401

from vpmin.domain.heat import TYPE_FEED_BREAK, TYPE_NO_LIGHT, HeatConfig, HeatTracker

CFG = HeatConfig(no_action_sec=120, feed_end_timeout=240, feed_min_duration=60)


def test_normal_heat_no_abnormal():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    tr.on_event("feed_end", 100.0, raw_ts=124.0)
    closed = tr.on_event("arrival", 700.0, weight=1810)
    assert len(closed) == 1
    assert closed[0].judged_type == 0
    assert abs(closed[0].feed_duration - 95.0) < 1e-6


def test_type1_no_action_after_arrival():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    assert not tr.tick(119.0)
    out = tr.tick(121.0)
    assert out and out[0].judged_type == TYPE_NO_LIGHT


def test_type1_not_fired_when_light_present():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("light_start", 20.0)
    assert not tr.tick(130.0)


def test_type1_not_fired_when_button_present():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("button", 30.0)
    assert not tr.tick(130.0)


def test_type2a_feed_end_timeout():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    assert not tr.tick(244.0)
    out = tr.tick(246.0)
    assert out and out[0].judged_type == TYPE_FEED_BREAK


def test_type2b_feed_too_short():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    out = tr.on_event("feed_end", 45.0, raw_ts=69.0)
    assert out and out[0].judged_type == TYPE_FEED_BREAK
    assert "40.0s" in out[0].reason


def test_type2_priority_over_type1():
    tr = HeatTracker(CFG)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    out = tr.tick(246.0)
    assert out and out[0].judged_type == TYPE_FEED_BREAK


def test_next_arrival_mode_deferred_judgement():
    cfg2 = HeatConfig(no_action_mode="next_arrival", no_action_sec=120)
    tr = HeatTracker(cfg2)
    tr.on_event("arrival", 0.0, weight=1800)
    assert not tr.tick(200.0)
    out = tr.on_event("arrival", 700.0, weight=1810)
    assert out and out[0].judged_type == TYPE_NO_LIGHT


def test_close_idle_timeout_no_abnormal():
    tr = HeatTracker(HeatConfig(close_idle_sec=600))
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    tr.on_event("feed_end", 100.0, raw_ts=124.0)
    out = tr.tick(800.0)
    assert out and out[0].judged_type == 0


def test_open_heat_without_arrival():
    tr = HeatTracker(CFG)
    tr.on_event("feed_start", 5.0, weight=1800)
    assert tr.current is not None
    assert tr.current.weight == 1800
    out = tr.on_event("feed_end", 100.0, raw_ts=124.0)
    assert not out


def test_button_is_anchor():
    tr = HeatTracker(CFG)
    tr.on_event("button", 50.0, weight=1800)
    assert tr.current is not None
    assert tr.current.anchor == "button"
    assert tr.current.anchor_ts == 50.0
    tr.on_event("feed_start", 80.0)
    assert tr.current.anchor_ts == 50.0
    out = tr.on_event("feed_end", 180.0, raw_ts=204.0)
    assert not out or out[0].judged_type == 0


def test_anchor_is_earliest_event():
    tr = HeatTracker(CFG)
    tr.on_event("feed_start", 10.0, weight=1800)
    tr.on_event("button", 25.0)
    assert tr.current.anchor == "feed_start"
    assert tr.current.anchor_ts == 10.0
    assert tr.current.button_ts == 25.0


def test_button_debounce():
    tr = HeatTracker(CFG)
    tr.on_event("button", 100.0, weight=1800)
    tr.on_event("button", 103.0)
    tr.on_event("button", 107.0)
    assert tr.current.button_ts == 100.0


def test_button_without_light_type1():
    tr = HeatTracker(CFG)
    tr.on_event("button", 100.0, weight=1800)
    assert not tr.tick(219.0)
    out = tr.tick(221.0)
    assert out and out[0].judged_type == TYPE_NO_LIGHT


def test_button_with_light_no_judgement():
    tr = HeatTracker(CFG)
    tr.on_event("button", 100.0, weight=1800)
    tr.on_event("light_start", 130.0)
    assert not tr.tick(260.0)
