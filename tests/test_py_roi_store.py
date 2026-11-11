"""pytest 化包装（T-007b 第二批）：tests/test_roi_store.py 的 RoiHistory 落盘用例。

原脚本保留不动，本文件复用其 RECT/POLY 常量，逐条改写为 pytest 断言
（口径与原脚本完全一致：新增/去重/倒序/持久化/上限/JSON 合法性）。
"""
import json
import os

import pytest

import test_roi_store as orig  # noqa: F401  (同时完成 sys.path 初始化)

from vpmin.adapters.roi_store import RoiHistory


@pytest.fixture()
def path(tmp_path):
    return os.path.join(str(tmp_path), "roi_history.json")


def test_empty_file_returns_empty_list(path):
    assert RoiHistory(path).list() == []


def test_add_returns_id_and_shape(path):
    e1 = RoiHistory(path).add(orig.RECT)
    assert bool(e1["id"]), "新增应返回 id"
    assert e1["shape"] == "矩形 300×400", f"shape 不符: {e1['shape']}"


def test_duplicate_region_deduped(path):
    h = RoiHistory(path)
    e1 = h.add(orig.RECT)
    e2 = h.add(dict(orig.RECT))
    assert len(h.list()) == 1, "同一块区域应去重"
    assert e2["id"] == e1["id"], "重复区域应复用原 id"


def test_polygon_recorded_and_newest_first(path):
    h = RoiHistory(path)
    h.add(orig.RECT)
    h.add(orig.POLY)
    assert [i["kind"] for i in h.list()] == ["polygon", "rect"], \
        f"倒序（新的在前）: {[i['kind'] for i in h.list()]}"
    assert h.list()[0]["shape"] == "多边形 3 点", f"shape 不符: {h.list()[0]['shape']}"


def test_persistence_reload_and_get(path):
    h = RoiHistory(path)
    e1 = h.add(orig.RECT)
    h.add(orig.POLY)
    reloaded = RoiHistory(path)
    assert len(reloaded.list()) == 2, "落盘后可恢复 2 条"
    assert reloaded.get(e1["id"])["rect"] == orig.RECT["rect"], "按 id 应取回原矩形"
    assert reloaded.get("nope") is None, "不存在的 id 应返回 None"


def test_keep_limit_keeps_latest(path):
    limit = RoiHistory(os.path.join(os.path.dirname(path), "limit.json"), keep=3)
    for i in range(5):
        limit.add({"kind": "rect", "rect": [i, i, 10, 10]})
    assert len(limit.list()) == 3, f"keep=3 只留最近 3 条: {len(limit.list())}"
    assert limit.list()[0]["rect"][0] == 4, f"留下的应是最新的: {limit.list()[0]}"


def test_file_is_valid_json_list(path):
    h = RoiHistory(path)
    h.add(orig.RECT)
    with open(path, encoding="utf-8") as fh:
        assert isinstance(json.load(fh), list)
