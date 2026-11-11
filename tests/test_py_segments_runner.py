"""pytest 化包装（T-007a 第一批）：tests/test_segments_runner.py 的分段不变量用例。

原脚本保留不动，直接复用其 run() 驱动函数（合成帧流 + 真实 ReactionRunner）。
"""
import test_segments_runner as orig  # noqa: F401

from vpmin.domain.judge import TYPE_OVER, TYPE_PAUSE, TYPE_UNDER

run = orig.run

LEGACY_KEYS = ("reaction_count", "detection_time", "spheroidization_start_time",
               "spheroidization_end_time", "actual_spheroidization_time",
               "spheroidization_abnormal", "spheroidization_image")


def test_merge_gap_zero_splits_two_records():
    recs = run([(10, 40), (55, 100)], merge_gap=0.0)
    assert len(recs) == 2
    assert recs[0]["spheroidization_abnormal"] == "失败"
    assert all("spheroidization_segments" not in r for r in recs)


def test_merge_gap_120_merges_to_one_record():
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=10.0)
    assert len(recs) == 1


def test_merged_record_judged_type5():
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=10.0)
    r = recs[0]
    assert r.get("abnormal_type") == TYPE_PAUSE
    assert "第 2 段" in r.get("abnormal_reason", "")
    assert "停顿" in r.get("abnormal_reason", "")


def test_merged_record_segment_detail():
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=10.0)
    segs = recs[0].get("spheroidization_segments") or []
    assert len(segs) == 2
    assert segs[0]["gap_before_sec"] is None
    assert segs[1]["gap_before_sec"] is not None
    assert 10.9 <= segs[1]["gap_before_sec"] <= 11.5
    assert segs[0]["start"] and segs[1]["end"]


def test_merged_record_keeps_legacy_fields():
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=10.0)
    assert all(k in recs[0] for k in LEGACY_KEYS)


def test_continuous_long_light_type3():
    recs = run([(10, 100)], merge_gap=120.0, pause_threshold=10.0)
    assert len(recs) == 1
    r = recs[0]
    assert r.get("abnormal_type") == TYPE_OVER
    assert "> 上限 90" in r.get("abnormal_reason", "")
    assert "spheroidization_segments" not in r


def test_short_light_type4():
    recs = run([(10, 60)], merge_gap=120.0, pause_threshold=10.0)
    assert recs
    assert recs[0].get("abnormal_type") == TYPE_UNDER


def test_normal_heat_type0():
    recs = run([(10, 80)], merge_gap=120.0, pause_threshold=10.0)
    assert recs
    assert recs[0].get("abnormal_type") == 0
    assert recs[0]["spheroidization_abnormal"] == "成功"


def test_higher_pause_threshold_no_pause():
    recs = run([(10, 40), (55, 100)], merge_gap=120.0, pause_threshold=20.0)
    assert recs
    assert recs[0].get("abnormal_type") != TYPE_PAUSE


def test_cross_furnace_merge_without_flush():
    recs = run([(10, 100), (190, 280)], merge_gap=120.0, pause_threshold=10.0,
               until=400.0)
    assert len(recs) == 1


def test_furnace_boundary_flush_splits():
    recs = run([(10, 100), (190, 280)], merge_gap=120.0, pause_threshold=10.0,
               until=400.0, flush_at=150.0)
    assert len(recs) == 2
