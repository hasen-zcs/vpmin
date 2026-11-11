"""pytest 化包装（T-007b 第二批）：tests/test_median_filter.py 的中值滤波用例。

原脚本保留不动，本文件复用其 load_frames() / run_runner() 驱动，逐条改写为
pytest 断言（口径与原脚本完全一致）。真实数据缺失时 skip。
"""
import os

import pytest

import test_median_filter as orig  # noqa: F401  (同时完成 sys.path 初始化)

from vpmin.domain.filter import MedianFilter


# ---------- 1-4. 单元 ----------

def test_frames_zero_passthrough():
    assert MedianFilter(0).apply(12.0, 88.0) == (12.0, 88.0)


def test_frames_one_passthrough():
    assert MedianFilter(1).apply(12.0, 88.0) == (12.0, 88.0)


def test_single_frame_spike_suppressed():
    f = MedianFilter(5)
    for v in (5, 5, 5, 5):
        f.apply(v, 100 - v)
    s, e = f.apply(95.0, 5.0)  # 突然来一帧高亮
    assert s <= 5.0 and e >= 95.0, f"单帧毛刺不应改变中值: {s}/{e}"


def _lag_values():
    f = MedianFilter(5)
    for _ in range(5):
        f.apply(5, 95)
    vals = []
    for _ in range(5):
        s, _e = f.apply(95, 5)
        vals.append(s)
    return vals


def test_lag_first_two_frames_still_low():
    vals = _lag_values()
    assert vals[1] <= 40, f"前 2 帧仍是低值（滞后）: {vals}"


def test_lag_third_frame_flips_up():
    vals = _lag_values()
    assert vals[2] > 40, f"第 3 帧应翻上来: {vals}"


def test_reset_clears_cache():
    f = MedianFilter(5)
    f.apply(95, 5)
    f.reset()
    assert f.apply(5, 95) == (5.0, 95.0)


# ---------- 5. 真实逐帧回归（data/real_r51_frames.csv） ----------

@pytest.fixture(scope="module")
def real_runner_results():
    if not os.path.exists(orig.DATA):
        pytest.skip("缺少 tests/data/real_r51_frames.csv（生产实测逐帧数据，不随公开仓库发布）")
    rows = orig.load_frames()
    assert rows, "real_r51_frames.csv 为空"
    return (orig.run_runner(rows, 0),
            orig.run_runner(rows, 3),
            orig.run_runner(rows, 5))


def test_real_data_all_settings_one_record(real_runner_results):
    off, on3, on5 = real_runner_results
    assert len(off) == len(on3) == len(on5) == 1, \
        f"三种设置都应出 1 条记录: {len(off)}/{len(on3)}/{len(on5)}"


def test_real_data_median5_duration_deviation_le_03s(real_runner_results):
    off, _on3, on5 = real_runner_results
    d0, d5 = off[0]["duration"], on5[0]["duration"]
    assert abs(d5 - d0) <= 0.3, f"中值5 时长偏差应 ≤ 0.3s, 差 {d5 - d0:+.2f}s"


def test_real_data_judgement_unchanged_by_median5(real_runner_results):
    off, _on3, on5 = real_runner_results
    assert off[0]["type"] == on5[0]["type"], \
        f"开中值滤波不应改变判定: {off[0]['type']} vs {on5[0]['type']}"


# ---------- 6. 毛刺抑制 ----------

@pytest.fixture(scope="module")
def glitch_results():
    # 亮 100 帧 → 熄 120 帧（中间一帧毛刺高亮）→ 再熄 40 帧
    series = []
    for i in range(100):
        series.append((i * 0.04, 95.0))
    for i in range(100, 220):
        series.append((i * 0.04, 5.0))
    series[150] = (series[150][0], 95.0)
    for i in range(220, 260):
        series.append((i * 0.04, 5.0))
    return orig.run_runner(series, 0), orig.run_runner(series, 5)


def test_glitch_both_settings_reach_end(glitch_results):
    off, on = glitch_results
    assert off, "关滤波应判结束"
    assert on, "开滤波应判结束"


def test_glitch_filtered_end_not_later_than_unfiltered(glitch_results):
    off, on = glitch_results
    d_off, d_on = off[0]["duration"], on[0]["duration"]
    assert d_on <= d_off + 1e-9, \
        f"开滤波的结束时刻不晚于关滤波: {d_on:.2f} vs {d_off:.2f}"
