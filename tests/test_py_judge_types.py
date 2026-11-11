"""pytest 化包装（T-007a 第一批）：tests/test_judge_types.py 的判定 0/3/4/5/6 用例。

原脚本保留不动，本文件只做 pytest 可收集的断言重写（口径与原脚本完全一致）。
"""
import test_judge_types as orig  # noqa: F401  (同时完成 sys.path 初始化)

from vpmin.domain.judge import (TYPE_NORMAL, TYPE_OVER, TYPE_PAUSE, TYPE_UNDER,
                                TYPE_UNJUDGED, judge)
from vpmin.domain.models import Segment, Window

W = Window(60.0, 90.0, "后端上下限")
SEG2 = (Segment(0.0, 30.0), Segment(48.0, 100.0, gap_before=18.0))


def test_normal_75s_in_range():
    v = judge(75.0, W)
    assert v.ok is True
    assert v.abnormal_type == TYPE_NORMAL
    assert v.abnormal_reason == ""


def test_type3_over_standard():
    v = judge(95.2, W)
    assert v.ok is False
    assert v.abnormal_type == TYPE_OVER
    assert "95.2" in v.abnormal_reason
    assert "90" in v.abnormal_reason


def test_type4_under_standard():
    v = judge(58.3, W)
    assert v.ok is False
    assert v.abnormal_type == TYPE_UNDER
    assert "58.3" in v.abnormal_reason
    assert "60" in v.abnormal_reason


def test_type5_middle_pause():
    v = judge(100.0, W, SEG2, pause_threshold=10.0)
    assert v.ok is False
    assert v.abnormal_type == TYPE_PAUSE
    assert "第 2 段" in v.abnormal_reason
    assert "18" in v.abnormal_reason
    assert len(v.segments) == 2
    assert v.segmented


def test_priority_pause_beats_over():
    v = judge(120.0, W, SEG2, pause_threshold=10.0)
    assert v.abnormal_type == TYPE_PAUSE


def test_priority_pause_beats_under():
    v = judge(40.0, W, SEG2, pause_threshold=10.0)
    assert v.abnormal_type == TYPE_PAUSE


def test_pause_boundary_equal_threshold_not_pause():
    v = judge(100.0, W, (Segment(0, 30), Segment(40, 100, gap_before=10.0)),
              pause_threshold=10.0)
    assert v.abnormal_type != TYPE_PAUSE


def test_pause_boundary_above_threshold_is_pause():
    v = judge(100.0, W, (Segment(0, 30), Segment(40.001, 100, gap_before=10.001)),
              pause_threshold=10.0)
    assert v.abnormal_type == TYPE_PAUSE


def test_pause_threshold_zero_disables():
    v = judge(100.0, W, (Segment(0, 30), Segment(40, 100, gap_before=10.0)),
              pause_threshold=0.0)
    assert v.abnormal_type != TYPE_PAUSE


def test_single_segment_never_pause():
    v = judge(100.0, W, (Segment(0.0, 100.0),), pause_threshold=10.0)
    assert v.abnormal_type == TYPE_OVER


def test_closed_interval_upper_ok():
    assert judge(90.0, W).ok is True


def test_closed_interval_lower_ok():
    assert judge(60.0, W).ok is True


def test_just_over_upper():
    assert judge(90.001, W).abnormal_type == TYPE_OVER


def test_just_under_lower():
    assert judge(59.999, W).abnormal_type == TYPE_UNDER


def test_float_noise_reason_truncated():
    v = judge(102.47999999999999, W)
    assert "102.48" in v.abnormal_reason


def test_duration_rounded_to_3():
    v = judge(102.47999999999999, W)
    assert v.duration == 102.48


def test_float_noise_rounds_to_upper_bound_ok():
    assert judge(90.0000004, W).ok is True


def test_missing_duration_unjudged():
    v = judge(None, W)
    assert v.abnormal_type == TYPE_UNJUDGED
    assert v.ok is None


def test_missing_window_unjudged():
    assert judge(75.0, Window()).abnormal_type == TYPE_UNJUDGED
