"""settings / image_store 自测（纯标准库，直接 python 运行）。"""

import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vpmin.adapters.settings import DEFAULT_PARAMS, SettingsStore
from vpmin.adapters.image_store import ImageStore

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILS.append(name)


# 1x1 最小 JPEG（灰度，约 132 字节）
MINI_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR"
    "ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QA"
    "FAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//"
    "2Q=="
)


def test_missing_file():
    with tempfile.TemporaryDirectory() as d:
        store = SettingsStore(os.path.join(d, "nope.json"))
        loaded = store.load()
        check(
            "1. 文件不存在 → 默认10参数+roi=None",
            loaded["roi"] is None
            and loaded["params"] == DEFAULT_PARAMS
            and len(loaded["params"]) == len(DEFAULT_PARAMS),
            detail=str(loaded),
        )


def test_roundtrip_and_swap():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        store = SettingsStore(path)
        roi = {"x": 10, "y": 20, "w": 100, "h": 30}
        params = dict(DEFAULT_PARAMS, start_pixel=111, unknown_key="zzz")
        store.save(roi, params)
        loaded = store.load()
        ok = (
            loaded["roi"] == roi
            and loaded["params"]["start_pixel"] == 111
            and "unknown_key" not in loaded["params"]
            and loaded["params"] == dict(DEFAULT_PARAMS, start_pixel=111)
        )
        check("2a. save→load 往返一致", ok, detail=str(loaded))
        check("2b. 未知键被忽略", "unknown_key" not in loaded["params"])

        # 写反的 standard_min/max 应交换
        reversed_params = dict(DEFAULT_PARAMS, standard_min=200.0, standard_max=50.0)
        store.save(None, reversed_params)
        loaded2 = store.load()
        check(
            "2c. standard_min/max 写反自动交换",
            loaded2["params"]["standard_min"] == 50.0
            and loaded2["params"]["standard_max"] == 200.0,
            detail=str(loaded2["params"]),
        )

        # 类型按默认值转换
        store.save(None, dict(DEFAULT_PARAMS, start_pixel="17", end_threshold="2.5"))
        loaded3 = store.load()
        check(
            "2d. 类型按默认值转换",
            loaded3["params"]["start_pixel"] == 17
            and isinstance(loaded3["params"]["start_pixel"], int)
            and loaded3["params"]["end_threshold"] == 2.5,
            detail=str(loaded3["params"]),
        )


def test_corrupt_json():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        with open(path, "w") as f:
            f.write("{broken json!!")
        try:
            loaded = SettingsStore(path).load()
            ok = loaded["roi"] is None and loaded["params"] == DEFAULT_PARAMS
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  异常: {exc}")
        check("3. 损坏JSON → 不抛，回默认", ok)

        # 半截结构（roi 缺、params 缺一半）也要兜住
        with open(path, "w") as f:
            json.dump({"params": {"start_pixel": 999}}, f)
        loaded = SettingsStore(path).load()
        ok = (
            loaded["roi"] is None
            and loaded["params"]["start_pixel"] == 999
            and len(loaded["params"]) == len(DEFAULT_PARAMS)
        )
        check("3b. 字段缺失 → 默认值补齐", ok, detail=str(loaded))


def test_image_store():
    with tempfile.TemporaryDirectory() as d:
        img = ImageStore(d)
        uri = "data:image/jpeg;base64," + MINI_JPEG_B64
        name = img.save(uri, 7, "2026-10-28 17:00:00.000")
        check("4a. save 返回文件名", bool(name) and name.endswith(".jpg"), detail=name)
        check(
            "4b. 文件名格式 reaction_TS_07_20261028170000.jpg",
            "_07_20261028170000.jpg" in name and name.startswith("reaction_"),
            detail=name,
        )
        check("4c. 文件存在且内容一致",
              os.path.isfile(os.path.join(d, name))
              and open(os.path.join(d, name), "rb").read()
              == base64.b64decode(MINI_JPEG_B64))
        items = img.list()
        ok = (
            len(items) == 1
            and items[0]["file"] == name
            and items[0]["index"] == 7
            and items[0]["start_time"] == "2026-10-28 17:00:00"
            and isinstance(items[0]["mtime"], float)
        )
        check("4d. list 能列出且 index/start_time 解析正确", ok, detail=str(items))

        # limit 生效 + 倒序
        n2 = img.save(uri, 3, "2026-10-28 18:05:09.123")
        items2 = img.list(limit=1)
        ok = len(items2) == 1 and items2[0]["file"] == n2 and items2[0]["index"] == 3
        check("4e. list(limit=1) 只留最新", ok, detail=str(items2))

        # keep 清理：keep=1 时最旧的被删
        time.sleep(0.05)  # 保证 mtime 可区分
        img2 = ImageStore(d, keep=1)
        n3 = img2.save(uri, 4, "2026-10-28 19:00:00.000")
        remaining = img2.list()
        check("4f. keep 超限清理", len(remaining) == 1 and remaining[0]["file"] == n3,
              detail=str(remaining))


def test_bad_data_uri():
    with tempfile.TemporaryDirectory() as d:
        img = ImageStore(d)
        cases = [
            "not-a-data-uri",
            "data:image/jpeg;base64,####非法base64####",
            "data:text/plain;base64,SGVsbG8=",
            "",
        ]
        ok = True
        for uri in cases:
            try:
                r = img.save(uri, 1, "2026-10-28 17:00:00.000")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  异常: {exc}")
                continue
            if r != "":
                ok = False
        check("5. 非法 data_uri → 返回'' 且不抛", ok)
        check("5b. 目录里没有残留文件", img.list() == [])


def main():
    test_missing_file()
    test_roundtrip_and_swap()
    test_corrupt_json()
    test_image_store()
    test_bad_data_uri()
    if FAILS:
        print(f"\nFAIL（{len(FAILS)} 项未通过）")
        sys.exit(1)
    print("\n全部 PASS")


if __name__ == "__main__":
    main()
