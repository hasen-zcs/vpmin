"""pytest 化包装（T-007b 第二批）：tests/test_retention.py 的保留策略用例。

原脚本保留不动，本文件复用其 touch() 辅助，逐条改写为 pytest 断言
（口径与原脚本完全一致：keep_days=30、节流、目录不存在、分目标保留期、白名单）。
"""
import os

import pytest

import test_retention as orig  # noqa: F401  (同时完成 sys.path 初始化)

from vpmin.adapters.retention import Retention, day_stamp


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("retention"))
    frames = os.path.join(tmp, "frames"); os.makedirs(frames)
    image = os.path.join(tmp, "image"); os.makedirs(image)
    orig.touch(os.path.join(frames, "old_40d.csv"), 40)
    orig.touch(os.path.join(frames, "new_1d.csv"), 1)
    orig.touch(os.path.join(image, "old_31d.jpg"), 31)
    orig.touch(os.path.join(image, "new_today.jpg"), 0)
    r = Retention([(frames, "*.csv"), (image, "*.jpg")], keep_days=30, interval=0)
    removed = r.run()
    return frames, image, removed


def test_removes_40d_old_csv(env):
    _f, _i, removed = env
    assert "old_40d.csv" in removed


def test_removes_31d_old_image(env):
    _f, _i, removed = env
    assert "old_31d.jpg" in removed


def test_keeps_1d_csv(env):
    frames, _i, removed = env
    assert "new_1d.csv" not in removed
    assert os.path.exists(os.path.join(frames, "new_1d.csv"))


def test_keeps_today_image(env):
    _f, image, _removed = env
    assert os.path.exists(os.path.join(image, "new_today.jpg"))


def test_throttle_no_rescan_within_interval():
    r = Retention([(os.path.join(os.getcwd(), "nonexistent-dir-x"), "*.csv")],
                  keep_days=0, interval=3600)
    first = r.maybe_run(now=0.0)
    second = r.maybe_run(now=1.0)
    assert isinstance(first, list) and second == [], \
        f"节流期间不应重复扫盘: first={first} second={second}"


def test_missing_dir_returns_empty(tmp_path):
    r = Retention([(os.path.join(str(tmp_path), "nope"), "*")], keep_days=1)
    assert r.run() == []


def test_per_target_keep_days(tmp_path):
    d = str(tmp_path / "mix"); os.makedirs(d)
    orig.touch(os.path.join(d, "a_9d.csv"), 9)
    orig.touch(os.path.join(d, "b_2d.csv"), 2)
    r = Retention([(d, "*.csv", 7)], keep_days=30)
    got = r.run()
    assert "a_9d.csv" in got, "9 天前的 CSV 超出逐目标 7 天保留期，应删"
    assert os.path.exists(os.path.join(d, "b_2d.csv")), "2 天前的 CSV 应保留"


def test_whitelist_non_matching_not_removed(tmp_path):
    frames = str(tmp_path / "frames"); os.makedirs(frames)
    orig.touch(os.path.join(frames, "old.csv"), 40)
    orig.touch(os.path.join(frames, "settings.json"), 40)
    Retention([(frames, "*.csv")], keep_days=30).run()
    assert not os.path.exists(os.path.join(frames, "old.csv"))
    assert os.path.exists(os.path.join(frames, "settings.json")), \
        "非目标 glob 的文件绝不能被删"


def test_day_stamp_format():
    s = day_stamp()
    assert len(s) == 8 and s.isdigit(), f"day_stamp 应为 8 位数字: {s}"
