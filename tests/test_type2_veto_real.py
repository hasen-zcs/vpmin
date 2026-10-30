"""实盘验证：长进线炉（>180s）不能被误判成「进线中断」。

真实数据里 1450 炉有 10 炉进线超过 180s（最长 887s）。
- 有亮光 → 必须放过（亮光否决）
- 亮光流没了（相机停） → 判中断也不能瞎报：light_stream_ok=False 时干脆不判

用法：
    python3 tests/test_type2_veto_real.py
"""

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.heat import TYPE_FEED_BREAK, HeatConfig, HeatTracker

DATA = os.path.join(HERE, "tests", "data", "plc_replay_all.jsonl")
if not os.path.exists(DATA):
    print("SKIP: 缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）")
    sys.exit(0)
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _t(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def load_long_heats():
    out = []
    for line in open(DATA, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        s, e = d.get("spheroidization_wire_entry_start_time"), d.get("spheroidization_wire_entry_end_time")
        if not s or not e:
            continue
        hs, he = _t(s), _t(e)
        if he - hs > 180.0:
            out.append((hs, he, he - hs, d.get("spheroidization_weight")))
    out.sort(key=lambda x: x[2])
    return out


def replay(heats, with_light):
    """实时模拟：每个 heat 从喂丝开始逐秒 tick（步长 10s，够精确了）。"""
    cfg = HeatConfig()
    tr = HeatTracker(cfg)
    closed = []
    for hs, he, dur, w in heats:
        tr.on_event("arrival", hs - 30, weight=w)
        tr.on_event("feed_start", hs, weight=w)
        if with_light:
            tr.on_event("light_start", hs + 13.0)
            tr.on_event("light_end", he + 3.4)
        # 真实时序：光先熄（he+3.4），结束位在物理结束后 24s 才到（he+24）
        t = hs + 10.0
        delivered = False
        while t <= he + 60.0:
            if not delivered and t >= he + 24.0:
                closed += tr.on_event("feed_end", he, weight=w, raw_ts=he + 24.0)
                delivered = True
            closed += tr.tick(t)
            t += 10.0
        if not delivered:
            closed += tr.on_event("feed_end", he, weight=w, raw_ts=he + 24.0)
        closed += tr.tick(he + 60.0)
    return tr, closed


def main():
    heats = load_long_heats()
    print(f"真实长进线炉（>180s）：{len(heats)} 条")
    for hs, he, dur, w in heats[:3]:
        print(f"   {datetime.fromtimestamp(hs)} 进线 {dur:.1f}s")

    print("\n1. 有亮光 → 一律放过（不能误报）")
    tr, closed = replay(heats, with_light=True)
    bad = [h for h in closed if h.judged_type == TYPE_FEED_BREAK]
    check("0 条误报", not bad,
          "; ".join(f"{h.reason}" for h in bad[:2]))
    got = len([h for h in closed if h.feed_end_ts is not None])
    check("全部正常关炉", got == len(heats), f"{got}/{len(heats)}")

    print("\n2. 对照：同样时长但没亮光 → 必须报中断（证明否决只在有亮光时生效）")
    tr2, closed2 = replay(heats, with_light=False)
    flagged = [h for h in closed2 if h.judged_type == TYPE_FEED_BREAK]
    check("全部报类型2", len(flagged) == len(heats), f"{len(flagged)}/{len(heats)}")

    print("\n3. 亮光流不可用（相机停/检测没开）→ 不判，宁可不判也不瞎报")
    tr3 = HeatTracker(HeatConfig())
    tr3.light_stream_ok = False
    hs, he, dur, w = heats[0]
    tr3.on_event("arrival", hs - 30, weight=w)
    tr3.on_event("feed_start", hs, weight=w)
    check("500s 也不判中断", not tr3.tick(hs + 500.0))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
