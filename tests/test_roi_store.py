"""ROI 历史适配器自测：新增/去重/持久化/上限。"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vpmin.adapters.roi_store import RoiHistory   # noqa: E402

RECT = {"kind": "rect", "rect": [10, 20, 300, 400]}
POLY = {"kind": "polygon", "points": [[1, 2], [3, 4], [5, 6]]}
OTHER = {"kind": "rect", "rect": [0, 0, 100, 100]}


def check(name, ok):
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


def main():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "roi_history.json")

    h = RoiHistory(path)
    check("空文件返回空表", h.list() == [])

    e1 = h.add(RECT)
    check("新增返回带 id/shape", bool(e1["id"]) and e1["shape"] == "矩形 300×400")
    check("列表 1 条", len(h.list()) == 1)

    h2 = h.add(dict(RECT))
    check("同一块区域去重", len(h.list()) == 1 and h2["id"] == e1["id"])

    h.add(POLY)
    check("多边形也能记", h.list()[0]["kind"] == "polygon" and h.list()[0]["shape"] == "多边形 3 点")
    check("倒序（新的在前）", [i["kind"] for i in h.list()] == ["polygon", "rect"])

    reloaded = RoiHistory(path)
    check("落盘可恢复", len(reloaded.list()) == 2)
    check("按 id 取回", reloaded.get(e1["id"])["rect"] == RECT["rect"])
    check("取不存在的 id 返回 None", reloaded.get("nope") is None)

    limit = RoiHistory(os.path.join(tmp, "limit.json"), keep=3)
    for i in range(5):
        limit.add({"kind": "rect", "rect": [i, i, 10, 10]})
    check("keep=3 只留最近 3 条", len(limit.list()) == 3)
    check("留下的是最新的", limit.list()[0]["rect"][0] == 4)

    with open(path, encoding="utf-8") as fh:
        check("文件是合法 JSON", isinstance(json.load(fh), list))

    print("\n全部 PASS")


if __name__ == "__main__":
    main()
