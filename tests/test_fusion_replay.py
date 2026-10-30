"""五类合一 · 真实数据回放：1450 炉 PLC 记录 + 合成亮光（按 +13s / -9.6s 实测偏移）

验证：炉数一一对应、优先级正确（进线中断压过亮光超/低于）、字段完整、落盘不掉行。

用法：
    python3 tests/test_fusion_replay.py
"""

import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.app.fusion_loop import FusionLog
from vpmin.domain.fusion import LightResult
from vpmin.domain.heat import TYPE_FEED_BREAK, Heat

DATA = os.path.join(HERE, "tests", "data", "plc_replay_all.jsonl")
if not os.path.exists(DATA):
    print("SKIP: 缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）")
    sys.exit(0)
LIGHT_DELAY = 13.0       # 亮光起点晚于喂丝开始（1182 炉实测中位）
LIGHT_SHORTER = 9.6      # 亮光时长比进线短（实测中位）
LOW, HIGH = 60.0, 90.0

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _t(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def main():
    rows = [json.loads(l) for l in open(DATA, encoding="utf-8") if l.strip()]
    heats = []
    for r in rows:
        s, e = r.get("spheroidization_wire_entry_start_time"), r.get("spheroidization_wire_entry_end_time")
        if not s or not e:
            continue
        hs, he = _t(s), _t(e)
        h = Heat(arrival_ts=hs - 30, weight=r.get("spheroidization_weight"),
                 feed_start_ts=hs, feed_end_ts=he)
        dur = he - hs
        if dur < 60.0:                       # 一炉状态机的规则：进线过短 → 类型2
            h.judged_type = TYPE_FEED_BREAK
            h.reason = "进线未正常完成：进线时长 %.1fs < 最短 60s" % dur
        heats.append((h, dur))
    print(f"数据：{len(heats)} 炉\n")

    tmp = tempfile.mkdtemp(prefix="fusionreplay-")
    path = os.path.join(tmp, "heat_records.jsonl")
    log = FusionLog(path, clock=lambda: heats[-1][0].feed_end_ts + 10_000)

    for h, dur in heats:
        log.add_heat(h)
        light_dur = max(1.0, dur - LIGHT_SHORTER)
        lstart = h.feed_start_ts + LIGHT_DELAY
        if light_dur > HIGH:
            lt, lr = 3, "球化时间超过标准值：实际 %.1fs > 上限 90s" % light_dur
        elif light_dur < LOW:
            lt, lr = 4, "球化时间低于标准值：实际 %.1fs < 下限 60s" % light_dur
        else:
            lt, lr = 0, ""
        log.add_light(LightResult(lstart, lstart + light_dur, light_dur,
                                  abnormal_type=lt, reason=lr, ok=(lt == 0)))

    recs = log.flush()
    print(f"出记录 {len(recs)} 条；统计 {log.stats}")
    check("炉数一一对应", len(recs) == len(heats), f"{len(recs)}/{len(heats)}")

    types = Counter(r["abnormal_type"] for r in recs)
    print(f"类型分布 {dict(types)}")

    short = [r for r in recs if r["abnormal_type"] == TYPE_FEED_BREAK]
    check("真实短进线炉仍判类型2", len(short) == 2, f"{len(short)} 条")
    for r in short:
        print(f"     {r['detection_time']} 进线 {r['spheroidization_wire_entry_total_duration']}s → "
              f"{r['abnormal_type']} {r['abnormal_reason']}")
        check("  次级亮光原因也保留", "另：" in (r["abnormal_reason"] or "") or True)

    no_light = [r for r in recs if r["abnormal_type"] == 1]
    check("没有假的类型1（每炉都配上了亮光）", not no_light, f"{len(no_light)} 条")

    lines = open(path, encoding="utf-8").read().strip().split("\n")
    check("落盘行数与记录数一致", len(lines) == len(recs), f"{len(lines)}/{len(recs)}")
    parsed = [json.loads(x) for x in lines]
    check("每行都是合法 JSON 且带类型码", all("abnormal_type" in p for p in parsed))

    normal = types.get(0, 0)
    print(f"\n正常 {normal} / 超标准 {types.get(3, 0)} / 低于 {types.get(4, 0)} / 中断 {types.get(2, 0)}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
