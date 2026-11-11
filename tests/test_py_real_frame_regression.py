"""pytest 化包装（T-007b 第二批）：tests/test_real_frame_regression.py 的 start_confirm 起判时刻回归。

原脚本保留不动，本文件复用其 load_frames() / first_start() 驱动，逐条改写为
pytest 断言（口径与原脚本完全一致：0.2→33109.2~33109.5，1.5→33185.0~33185.5）。
真实数据缺失时 skip。
"""
import os

import pytest

import test_real_frame_regression as orig  # noqa: F401  (同时完成 sys.path 初始化)


@pytest.fixture(scope="module")
def rows():
    if not os.path.exists(orig.DATA):
        pytest.skip("缺少 tests/data/real_r51_frames.csv（生产实测逐帧数据，不随公开仓库发布）")
    data = orig.load_frames(orig.DATA)
    assert data, "real_r51_frames.csv 为空"
    return data


@pytest.fixture(scope="module")
def starts(rows):
    return {sc: orig.first_start(rows, sc) for sc in (0.0, 0.2, 0.4, 0.6, 1.0, 1.5)}


def test_production_start_confirm_0p2(rows, starts):
    t02 = starts[0.2]
    assert t02 is not None, "应判到开始"
    assert 33109.2 <= t02 <= 33109.5, \
        f"起判应在 33109.2~33109.5（生产实测 33109.2）, 实测 {t02}"


def test_wrong_start_confirm_1p5(rows, starts):
    t15 = starts[1.5]
    assert t15 is not None, "应判到开始"
    assert 33185.0 <= t15 <= 33185.5, \
        f"起判应在 33185.0~33185.5（影子实测 33185.262）, 实测 {t15}"


def test_larger_confirm_delays_start_by_70s_plus(starts):
    t02, t15 = starts[0.2], starts[1.5]
    assert t02 is not None and t15 is not None
    gap = t15 - t02
    assert gap > 70, f"参数改大导致起点滞后应 >70 秒（实测 76 秒）, 实际 {gap:.2f}s"


def test_start_monotonic_non_decreasing_in_confirm(starts):
    seq = [(sc, starts[sc]) for sc in (0.0, 0.2, 0.4, 0.6, 1.0, 1.5)]
    for a, b in zip(seq, seq[1:]):
        assert a[1] is not None and b[1] is not None, f"确认时长 {a[0]}/{b[0]} 没判到开始"
        assert a[1] <= b[1], \
            f"起判随确认时长增大只可能变晚: {a[0]}→{a[1]:.2f} > {b[0]}→{b[1]:.2f}"


def test_threshold_40_hit_ratio(rows):
    ratio = sum(1 for _, p in rows if p >= 40.0) / len(rows) * 100
    assert 19 <= ratio <= 22, f"阈值 40% 命中率应约 20.5%（19~22）, 实测 {ratio:.1f}%"
