"""pytest 化包装（T-007a 第一批）：tests/test_merge_gap.py 的 gap 合并用例。

原脚本保留不动，直接复用其 build_series() / run() 驱动函数。
"""
import test_merge_gap as orig  # noqa: F401


def test_gap_zero_keeps_two_records():
    out0, _ = orig.run(0.0, orig.build_series())
    assert len(out0) == 2
    d0 = out0[0][0]["duration"]
    d1 = out0[1][0]["duration"]
    assert 9.0 <= d0 <= 10.5
    assert 22.5 <= d1 <= 25.0


def test_gap_10_merges_to_one_record():
    out1, _ = orig.run(10.0, orig.build_series())
    assert len(out1) == 1


def test_gap_10_merged_duration():
    out1, _ = orig.run(10.0, orig.build_series())
    assert 34.0 <= out1[0][0]["duration"] <= 37.0


def test_gap_10_counter_not_over_consumed():
    out1, _ = orig.run(10.0, orig.build_series())
    assert out1[0][0]["reaction_count"] == 1


def test_gap_10_no_new_json_fields():
    out1, _ = orig.run(10.0, orig.build_series())
    assert out1[0][0].get("merged_segments") is None


def test_gap_10_curve_points_complete():
    out1, painter1 = orig.run(10.0, orig.build_series())
    assert out1
    assert painter1.draws
    pts = painter1.draws[-1]
    assert len(pts) > 700
    span = pts[-1][0] - pts[0][0]
    assert span > 28.0


def test_big_gap_still_two_records():
    series2 = orig.build_series(seg_a=6.0, gap=30.0, seg_b=20.0)
    out2, _ = orig.run(10.0, series2)
    assert len(out2) == 2
