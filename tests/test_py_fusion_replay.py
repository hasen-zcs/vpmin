"""pytest 化包装（T-007b 第二批）：tests/test_fusion_replay.py 的 1450 炉融合回放。

原脚本保留不动，本文件直接复用其 main() 驱动（炉数一一对应 / 类型2 命中 /
无假类型1 / 落盘不掉行，口径与原脚本完全一致），把原 check() 记录的失败
转为 pytest 断言，并校验返回码。

真实 PLC 数据回放，统一标 @pytest.mark.slow（默认跑法仍执行）。
原脚本在数据缺失时模块级 sys.exit(0)（已知小瑕疵），包装侧先查数据再 import。
"""
import os

import pytest

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                     "plc_replay_all.jsonl")
if not os.path.exists(_DATA):
    pytest.skip("缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）",
                allow_module_level=True)

import test_fusion_replay as orig  # noqa: E402,F401  (同时完成 sys.path 初始化)

pytestmark = pytest.mark.slow


def test_full_fusion_replay_all_checks_pass():
    orig.FAILS.clear()
    rc = orig.main()
    assert orig.FAILS == [], "原脚本 check 项未通过: " + "; ".join(orig.FAILS)
    assert rc == 0, f"原脚本返回码 {rc}"
