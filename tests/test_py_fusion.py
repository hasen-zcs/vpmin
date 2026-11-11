"""pytest 化包装（T-007a 第一批）：tests/test_fusion.py 的五类合并优先级用例。

原脚本保留不动，复用其 heat() 构造辅助；FusionLog 用例沿用 tempfile 落盘。
"""
import json
import os
import tempfile

import pytest

import test_fusion as orig  # noqa: F401

from vpmin.app.fusion_loop import FusionLog
from vpmin.domain.fusion import (LightResult, build_final, matches, merge_types,
                                 shift_segments)
from vpmin.domain.heat import TYPE_FEED_BREAK, TYPE_NO_LIGHT
from vpmin.domain.models import Segment

heat = orig.heat


PRIORITY_CASES = [((2, "中断原因"), (3, "超标准原因"), 2),
                  ((1, "未球化原因"), (5, "停顿原因"), 1),
                  ((0, ""), (5, "停顿原因"), 5),
                  ((0, ""), (3, "超标准原因"), 3),
                  ((0, ""), (4, "低于原因"), 4)]


@pytest.mark.parametrize("plc,light,want", PRIORITY_CASES)
def test_priority(plc, light, want):
    t, r = merge_types(plc[0], plc[1], light[0], light[1])
    assert t == want


def test_secondary_reason_appended():
    t, r = merge_types(2, "进线未正常完成：进线时长 40.0s < 最短 60s",
                       3, "球化时间超过标准值：实际 120.0s > 上限 90s")
    assert t == 2
    assert "另：" in r
    assert "超过标准值" in r
    assert len(r) <= 200


def test_action_without_light_type1():
    rec = build_final(heat(), None, reaction_count=1)
    assert rec.abnormal_type == TYPE_NO_LIGHT
    assert "未检测到亮光" in rec.abnormal_reason
    assert rec.ok is False


def test_feed_break_wins_over_light_over_standard():
    h = heat(start=1000.0, end=1040.0, judged=TYPE_FEED_BREAK,
             reason="进线未正常完成：进线时长 40.0s < 最短 60s", light=True)
    lr = LightResult(1013.0, 1050.0, 37.0, abnormal_type=4,
                     reason="球化时间低于标准值：实际 37.0s < 下限 60s", ok=False)
    rec = build_final(h, lr, reaction_count=2)
    assert rec.abnormal_type == TYPE_FEED_BREAK
    assert rec.light_duration == 37.0
    assert "另：" in rec.abnormal_reason


def test_matches_inside_window():
    assert matches(heat(), LightResult(1013.0, 1103.0, 90.0))


def test_matches_too_early():
    assert not matches(heat(), LightResult(900.0, 1000.0, 100.0))


def test_matches_too_late():
    assert not matches(heat(), LightResult(1200.0, 1300.0, 100.0))


@pytest.mark.parametrize("order", ["heat_first", "light_first"])
def test_fusion_log_late_pairing(order, tmp_path):
    path = os.path.join(str(tmp_path), "heat_records.jsonl")
    now = [1000.0]
    log = FusionLog(path, clock=lambda: now[0])
    h = heat(light=True)
    lr = LightResult(1013.0, 1103.0, 90.0, abnormal_type=3,
                     reason="球化时间超过标准值：实际 90.0s > 上限 90s", ok=False)
    if order == "heat_first":
        log.add_heat(h)
        log.add_light(lr)
    else:
        log.add_light(lr)
        log.add_heat(h)
    now[0] = 1200.0
    out = log.settle()
    assert len(out) == 1
    assert out[0]["actual_spheroidization_time"] == "00:01:30.00"
    assert out[0]["abnormal_type"] == 3
    lines = open(path, encoding="utf-8").read().strip().split("\n")
    assert len(lines) == 1 and json.loads(lines[0])


def test_record_fields_complete(tmp_path):
    path = os.path.join(str(tmp_path), "r.jsonl")
    log = FusionLog(path, clock=lambda: 1300.0)
    log.add_heat(heat(light=True))
    log.add_light(LightResult(1013.0, 1103.0, 90.0, 0, "", True))
    rec = log.flush()[0]
    want = ("reaction_count", "detection_time", "spheroidization_start_time",
            "spheroidization_end_time", "actual_spheroidization_time",
            "spheroidization_abnormal", "abnormal_type", "abnormal_reason",
            "spheroidization_wire_entry_start_time", "spheroidization_wire_entry_end_time",
            "spheroidization_wire_entry_total_duration", "spheroidization_weight",
            "plc_button_time", "plc_arrival_time")
    assert not [k for k in want if k not in rec]
    assert rec["spheroidization_wire_entry_total_duration"] == 100.0
    assert rec["spheroidization_abnormal"] == 0


def test_shift_segments_to_calendar_seconds():
    segs = (Segment(1000.0, 1060.0), Segment(1090.0, 1150.0, gap_before=30.0))
    shifted = shift_segments(segs, 1_700_000_000.0)
    assert shifted[0].start == 1_700_001_000.0
    assert shifted[1].end == 1_700_001_150.0
    assert shifted[1].gap_before == 30.0
    assert abs(shifted[0].duration - 60.0) < 1e-9


def test_segments_not_1970_regression(tmp_path):
    segs = (Segment(1000.0, 1060.0), Segment(1090.0, 1150.0, gap_before=30.0))
    shifted = shift_segments(segs, 1_700_000_000.0)
    h = heat(start=1_700_000_013.0, end=1_700_000_103.0)
    lr = LightResult(1_700_000_013.0, 1_700_000_113.0, 100.0, 5,
                     "球化过程中间亮光停顿：第 2 段前停顿 30s", False, shifted)
    rec = build_final(h, lr, reaction_count=9).to_dict()
    seg0 = rec["spheroidization_segments"][0]["start"]
    assert not seg0.startswith("1970")
    assert seg0[:10] == rec["detection_time"][:10]
