"""曲线图一致性回归：4 组固定样例出图，md5 必须与基线完全一致。

基线来自 vp10.8.3 原版算法（plot_render 精简移植前后、以及原版 core/gray_plot 三方一致）。
改动绘图相关代码后跑一遍：只要 md5 变了，说明图样变了，必须停下来说明原因。

用法：python3 tests/verify_plot_parity.py
"""

import base64
import hashlib
import os
import random
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.adapters.painter import CurvePainter

BASELINE = {
    "a_polygon_full": "275b170ca4eb54ca000f2fa0eb59b5b4",
    "b_partial": "3b62d04396251a2b4ee65d908dba4021",
    "c_short": "9d0c7b50b315c5fe33f671e89f972b27",
    "d_no_marks": "91a242e73cf95f44c3491bc8f650b8be",
}


def points(seed, n=600, base=120.0):
    rnd = random.Random(seed)
    out, g = [], base
    for i in range(n):
        g += rnd.uniform(-1.5, 1.5) + (6 if 200 < i < 520 else 0)
        out.append((round(i * 0.1, 3), round(g, 3)))
    return out


def main() -> int:
    cases = {
        "a_polygon_full": (points(1), 20.0, 52.0, 240),
        "b_partial": (points(2, 300), 5.0, None, 200),
        "c_short": (points(3, 40), 1.2, 3.4, 240),
        "d_no_marks": (points(4, 200), None, None, 180),
    }
    painter = CurvePainter()
    bad = 0
    for name, (pts, st, en, sp) in cases.items():
        uri = painter.draw(pts, st, en, start_pixel=sp)
        raw = base64.b64decode(uri.split(",", 1)[1])
        digest = hashlib.md5(raw).hexdigest()
        ok = digest == BASELINE[name]
        print(f"{'PASS' if ok else 'FAIL'} {name:16s} {digest}")
        bad += 0 if ok else 1
    print("全部一致" if not bad else f"{bad} 个样例图样变了")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
