"""pytest 化包装（T-007b 第二批）：tests/test_spurious_filter.py 的 guard 窗口杂光过滤用例。

原脚本保留不动，本文件复用其 build() / run() 驱动，逐条改写为 pytest 断言
（口径与原脚本完全一致：guard_window=3.0 / guard_peak=80.0 / min_duration=20.0）。
"""
import test_spurious_filter as orig  # noqa: F401  (同时完成 sys.path 初始化)


def test_normal_reaction_kept():
    r, rows = orig.run(orig.build((3, 0), (100, 92), (6, 5)))
    assert len(rows) == 1, f"正常反应应记录 1 条: {len(rows)}"
    assert rows[0]["n"] == 1, f"序号应为 1: {rows[0]['n']}"
    assert r.dropped == 0, f"不应有丢弃: dropped={r.dropped}"


def test_spurious_door_light_dropped():
    r, rows = orig.run(orig.build((3, 0), (20, 42), (6, 5)))
    assert len(rows) == 0, f"开门杂光不应成记录: {len(rows)}"
    assert r.dropped >= 1, f"丢弃计数应 ≥1: dropped={r.dropped}"


def test_quick_drop_back_discarded():
    # 起判 90% 但 1 秒就掉回 5% → 结束太快（<min_duration），丢弃
    r, rows = orig.run(orig.build((3, 0), (1, 90), (10, 5), (10, 0)))
    assert len(rows) == 0, f"秒闪不应成记录: {len(rows)}"
    assert r.dropped >= 1, f"丢弃计数应 ≥1: dropped={r.dropped}"


def test_no_sequence_gap_after_spurious_drop():
    r, rows = orig.run(orig.build((3, 0), (20, 42), (5, 0), (100, 92), (6, 5)))
    assert len(rows) == 1, f"杂光后真反应应记录 1 条: {len(rows)}"
    assert rows[0]["n"] == 1, f"序号应是 1（没被杂光占掉）: {rows[0]['n']}"


def test_guard_disabled_keeps_old_behavior():
    r, rows = orig.run(orig.build((3, 0), (20, 42), (6, 5)),
                       window=0.0, min_dur=0.0)
    assert len(rows) == 1, f"guard 关闭时杂光应照旧成记录（旧行为）: {len(rows)}"


def test_below_peak_threshold_dropped():
    r, rows = orig.run(orig.build((3, 0), (20, 75), (6, 5)))
    assert len(rows) == 0, f"起判 75% 低于 80 阈值应丢弃: {len(rows)}"


def test_above_peak_threshold_kept():
    r, rows = orig.run(orig.build((3, 0), (100, 85), (6, 5)))
    assert len(rows) == 1, f"起判 85% 过阈值应保留: {len(rows)}"
