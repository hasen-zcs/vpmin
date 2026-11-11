"""pytest 化包装（T-007b 第二批）：tests/test_real_types_345.py 的真实 50 炉 + R51 判定 3/4/5 回归。

原脚本保留不动，本文件复用其 _num() / _Probe / _Painter / _Sink / _Counter 及数据常量，
逐条改写为 pytest 断言（口径与原脚本完全一致）。

注意：原脚本在数据缺失时会在模块级 sys.exit(0)（已知小瑕疵，INVENTORY 有记录），
所以包装侧先查数据文件、缺失即 skip，再 import 原模块。
"""
import csv
import os
from datetime import datetime

import pytest

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_MISSING = [n for n in ("real_50_furnaces_1029.csv", "real_r51_frames.csv")
            if not os.path.exists(os.path.join(_DATA_DIR, n))]
if _MISSING:
    pytest.skip(f"缺少生产真实数据 {_MISSING}（不随公开仓库发布）", allow_module_level=True)

import test_real_types_345 as orig  # noqa: E402,F401  (同时完成 sys.path 初始化)

from vpmin.domain.curve import CurveBuffer  # noqa: E402
from vpmin.domain.judge import TYPE_OVER, judge  # noqa: E402
from vpmin.domain.models import Window  # noqa: E402
from vpmin.domain.standard import StandardPolicy  # noqa: E402
from vpmin.domain.state_machine import ReactionStateMachine  # noqa: E402
from vpmin.domain.trigger import Thresholds  # noqa: E402
from vpmin.runner import ReactionRunner  # noqa: E402


# ---------- 1. 50 炉真实亮光时长 ----------

@pytest.fixture(scope="module")
def rows50():
    with open(orig.DATA50, encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f))
    assert data, "real_50_furnaces_1029.csv 为空"
    return data


def test_50_furnaces_judge_agrees_with_production(rows50):
    w = Window(60.0, 90.0, "后端上下限")
    bad = [(r["DB记录ID"], r["亮光时长(s)"], judge(orig._num(r["亮光时长(s)"]), w).text,
            r["生产原判定"])
           for r in rows50 if judge(orig._num(r["亮光时长(s)"]), w).text != r["生产原判定"]]
    assert not bad, f"判定与生产原判定不一致 {len(bad)}/{len(rows50)}: {bad[:3]}"


def test_50_furnaces_success_rows_in_60_90(rows50):
    ok_rows = [r for r in rows50 if r["生产原判定"] == "成功"]
    assert ok_rows, "数据里应有一批成功炉"
    bad = [r for r in ok_rows if not (60.0 <= orig._num(r["亮光时长(s)"]) <= 90.0)]
    assert not bad, \
        "; ".join(f"{r['DB记录ID']}:{r['亮光时长(s)']}" for r in bad)


def test_50_furnaces_over_rows_reason_format(rows50):
    w = Window(60.0, 90.0, "后端上下限")
    overs = [r for r in rows50 if (orig._num(r["亮光时长(s)"]) or 0) > 90.0]
    for r in overs:
        v = judge(orig._num(r["亮光时长(s)"]), w)
        assert "超过标准值" in v.abnormal_reason, f"{r['DB记录ID']}: {v.abnormal_reason}"
        assert "> 上限 90" in v.abnormal_reason, f"{r['DB记录ID']}: {v.abnormal_reason}"


# ---------- 2. R51 真实逐帧 ----------

@pytest.fixture(scope="module")
def r51_records():
    rows = []
    with open(orig.DATA_R51, encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for x in rd:
            if len(x) >= 2 and x[0]:
                rows.append((float(x[0]), float(x[1])))
    standard = StandardPolicy((60.0, 90.0), 5.0)
    standard.update({"standard_spheroidization_time_lower": 60,
                     "standard_spheroidization_time_upper": 90})
    runner = ReactionRunner(
        probe=orig._Probe(rows), thresholds=Thresholds(40.0, 70.0),
        machine=ReactionStateMachine(0.2, 4.0), standard=standard,
        curve=CurveBuffer(5.0), painter=orig._Painter(), sink=orig._Sink(),
        counter=orig._Counter(), base=datetime(2026, 10, 29, 10, 34, 0),
        merge_gap=120.0, pause_threshold=10.0)
    out = []
    for t, _pct in rows:
        runner.process(None, t)
        out.extend(rec for rec, _e, _m in runner.drain())
    runner.flush_pending()
    out.extend(rec for rec, _e, _m in runner.drain())
    return out


def test_r51_single_record(r51_records):
    assert len(r51_records) == 1, f"应出 1 条记录, 实际 {len(r51_records)} 条"


def test_r51_duration_matches_production(r51_records):
    r = r51_records[0]
    assert 90.0 <= r["duration"] <= 91.0, f"时长应≈90.47s, 实际 {r['duration']:.2f}s"


def test_r51_single_segment_no_pause(r51_records):
    assert "spheroidization_segments" not in r51_records[0], \
        "这一炉没有真停顿，不应出现分段"


def test_r51_type_consistent_with_duration(r51_records):
    r = r51_records[0]
    assert (r["type"] == TYPE_OVER) == (r["duration"] > 90.0), \
        f"type={r['type']} dur={r['duration']:.2f}"
