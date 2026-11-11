"""真实逐帧数据回归：start_confirm 一旦改错，起判时刻就崩。

数据来源（2026-10-29 东贝影子实测，25fps，elapsed 33040–33205）：
    同一炉反应，原生产参数 start_confirm=0.2 → 起判 33109.3（10:35:10）
    影子一度被改成 start_confirm=1.5 → 起判 33185.26（晚 76 秒）
    两者结束时刻完全一致，所以只错在起点 → 时长从 90.47s 缩成 14.50s

这个用例把「参数 → 起判时刻」钉死：以后谁把 start_confirm 调大，测试立刻红。

用法：
    /home/hasen/miniconda3/bin/python tests/test_real_frame_regression.py
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds

DATA = os.path.join(HERE, "tests", "data", "real_r51_frames.csv")

FAILS = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  {tag} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def load_frames(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x in r:
            if len(x) >= 2 and x[0]:
                rows.append((float(x[0]), float(x[1])))
    return rows


def first_start(rows, start_confirm, start_pct=40.0):
    """只推进到「第一次判到开始」为止，返回 (起判时刻, 起判时长)"""
    m = ReactionStateMachine(start_confirm=start_confirm, end_confirm=4.0)
    th = Thresholds(start_pct=start_pct, end_pct=70.0)
    for t, pct in rows:
        tr = m.advance(t, th.start_ok(pct), th.end_ok(100.0 - pct))
        if tr.started:
            return m.start_t
    return None


def main():
    rows = load_frames(DATA)
    print(f"数据：{len(rows)} 帧，{rows[0][0]:.2f} → {rows[-1][0]:.2f}")
    print()

    print("1. 生产参数 start_confirm=0.2 起判时刻")
    t02 = first_start(rows, 0.2)
    check("起判在 33109.2~33109.5（生产实测 33109.2，10:35:10）",
          t02 is not None and 33109.2 <= t02 <= 33109.5, f"实测起判 {t02}")

    print()
    print("2. 错误参数 start_confirm=1.5 起判时刻")
    t15 = first_start(rows, 1.5)
    check("起判在 33185.0~33185.5（影子 10:36 那炉实测 33185.262）",
          t15 is not None and 33185.0 <= t15 <= 33185.5, f"实测起判 {t15}")

    print()
    print("3. 参数改大导致起点滞后（这是 2026-10-29 那次事故的复现）")
    gap = (t15 - t02) if (t02 and t15) else 0
    check("滞后 > 70 秒（实测 76 秒）", gap > 70, f"滞后 {gap:.2f}s")

    print()
    print("4. 边界扫描：起判时刻随 start_confirm 单调不降")
    seq = [(sc, first_start(rows, sc)) for sc in (0.0, 0.2, 0.4, 0.6, 1.0, 1.5)]
    ok = all(a[1] is not None and b[1] is not None and a[1] <= b[1]
             for a, b in zip(seq, seq[1:]))
    detail = " ".join(f"{sc}→{t:.2f}" for sc, t in seq if t)
    check("随确认时长增大，起判只可能变晚", ok, detail)

    print()
    print("5. 阈值恒定：整段数据 ≥40% 的帧占比应约 20.5%")
    ratio = sum(1 for _, p in rows if p >= 40.0) / len(rows) * 100
    check("阈值 40% 命中率 19%~22%", 19 <= ratio <= 22, f"实测 {ratio:.1f}%")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
