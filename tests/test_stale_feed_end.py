"""迟到结束位归属（2026-11-02 现场 398 炉真实序列回归）。

现场事实（10.0.0.19 shadow，plc_events.jsonl seq 1001~1005 / all_plc.jsonl）：
    15:50:17  button      ← A 炉
    15:51:33  feed_start  ← A 炉
    15:51:43  light_start
    15:53:14  light_end
    16:03:34  button      ← B 炉（A 的结束位还没到）
    16:03:56  feed_end    （raw 16:04:20，24s 位延迟）← 这是 **A 炉** 的结束
    16:04:43  feed_start  ← B 炉

旧行为的病：A 的结束位落到 B 头上，B 又拿到自己的开始 → end(16:03:56) < start(16:04:43)
→ 进线时长 -47.288s；同时 A 的结束位丢失（null）。
all_plc 里 A 炉真实进线 = 15:51:33 → 16:03:56（742.822s）。

用法：/home/hasen/miniconda3/bin/python tests/test_stale_feed_end.py
"""

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.fusion import build_final
from vpmin.domain.heat import HeatTracker

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def ep(hms):
    return datetime.strptime("2026-11-02 " + hms, "%Y-%m-%d %H:%M:%S.%f").timestamp()


def site_sequence(wait_before_flush=0.0):
    """复现现场 5 个事件，返回 (tracker, 已吐出的炉列表)。"""
    t = HeatTracker()
    emitted = []

    def ev(kind, hms, raw=None, w=None):
        emitted.extend(t.on_event(kind, ep(hms), weight=w,
                                  raw_ts=ep(raw) if raw else None))

    ev("button", "15:50:17.275", w=1840)
    ev("feed_start", "15:51:33.745", w=1864)
    ev("light_start", "15:51:43.467")
    ev("light_end", "15:53:14.578")
    ev("button", "16:03:34.642", w=1864)
    ev("feed_end", "16:03:56.400", raw="16:04:20.400", w=1864)
    ev("feed_start", "16:04:43.689", w=1832)
    if wait_before_flush:
        emitted.extend(t.tick(ep("16:04:43.689") + wait_before_flush))
    return t, emitted


def by_detect(heats, hms):
    for h in heats:
        if h.button_ts is not None and abs(h.button_ts - ep(hms)) < 0.01:
            return h
    return None


def main():
    print("1. 现场序列：迟到结束位必须归 A 炉，B 炉不能拿负数")
    t, emitted = site_sequence()
    a = by_detect(emitted, "15:50:17.275")
    check("A 炉已被吐出（挂起后回填）", a is not None, f"emitted={len(emitted)}")
    if a is not None:
        rec = build_final(a, None).to_dict()
        check("A 炉进线结束已回填 = 16:03:56.400",
              rec["spheroidization_wire_entry_end_time"] == "2026-11-02 16:03:56.400",
              str(rec["spheroidization_wire_entry_end_time"]))
        check("A 炉进线时长 ≈ 742.8s（与 all_plc 742.822 差 < 0.5s，秒截断所致）",
              abs(rec["spheroidization_wire_entry_total_duration"] - 742.822) < 0.5,
              str(rec["spheroidization_wire_entry_total_duration"]))

    print("\n2. 当前炉（B）不能出现 end < start")
    b = t.current
    check("B 炉还有当前炉", b is not None)
    if b is not None:
        check("B 炉 feed_end 为空（没吞上一炉的结束位）", b.feed_end_ts is None,
              str(b.feed_end_ts))
        check("B 炉 feed_start = 16:04:43.689",
              b.feed_start_ts is not None
              and abs(b.feed_start_ts - ep("16:04:43.689")) < 0.01)

    print("\n3. 兜底：end < start 的记录不许输出负时长")
    from vpmin.domain.fusion import FinalRecord
    bad = FinalRecord(feed_start=ep("16:04:43.689"), feed_end=ep("16:03:56.400"))
    check("负区间 → 时长 None", bad.to_dict()["spheroidization_wire_entry_total_duration"] is None,
          str(bad.to_dict()["spheroidization_wire_entry_total_duration"]))

    print("\n4. 正常炉不受影响（结束位在炉内到达）")
    t2 = HeatTracker()
    got = []
    got.extend(t2.on_event("button", ep("16:37:25.391"), 1832))
    got.extend(t2.on_event("feed_start", ep("16:38:15.545"), 1832))
    got.extend(t2.on_event("light_start", ep("16:38:31.524")))
    got.extend(t2.on_event("light_end", ep("16:40:00.202")))
    got.extend(t2.on_event("feed_end", ep("16:39:59.256"), 1832, ep("16:40:23.256")))
    check("正常炉 1 条吐出", len(got) == 1, f"got={len(got)}")
    if got:
        rec = build_final(got[0], None).to_dict()
        check("时长 103.711s", rec["spheroidization_wire_entry_total_duration"] == 103.711,
              str(rec["spheroidization_wire_entry_total_duration"]))

    print("\n5. 挂起超时：等太久也要吐出来（别扣死）")
    t3, em3 = site_sequence()
    flushed = t3.tick(ep("16:03:34.642") + 200.0)   # 关炉后 200s > stale_end_wait
    check("超时兜底吐出挂起的炉", len(flushed) >= 1, f"flushed={len(flushed)}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
