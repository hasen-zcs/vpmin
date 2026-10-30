"""五类合一测试：优先级、配对、无亮光兜底、落盘。

用法：
    python3 tests/test_fusion.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.app.fusion_loop import FusionLog
from vpmin.domain.fusion import (LightResult, build_final, matches, merge_types,
                                 shift_segments)
from vpmin.domain.models import Segment
from vpmin.domain.heat import TYPE_FEED_BREAK, TYPE_NO_LIGHT, Heat

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def heat(start=1000.0, end=1100.0, weight=1800.0, judged=0, reason="", light=False, button=None):
    h = Heat(arrival_ts=start - 30, weight=weight, button_ts=button,
             feed_start_ts=start, feed_end_ts=end)
    h.judged_type, h.reason = judged, reason
    if light:
        h.light_start_ts = start + 13
        h.light_end_ts = end + 3
    return h


def main():
    print("1. 优先级：2 > 1 > 5 > 3 > 4")
    cases = [((2, "中断原因"), (3, "超标准原因"), 2),
             ((1, "未球化原因"), (5, "停顿原因"), 1),
             ((0, ""), (5, "停顿原因"), 5),
             ((0, ""), (3, "超标准原因"), 3),
             ((0, ""), (4, "低于原因"), 4)]
    for plc, light, want in cases:
        t, r = merge_types(plc[0], plc[1], light[0], light[1])
        check(f"PLC{plc[0]} + 亮光{light[0]} → {want}", t == want, f"得 {t}：{r}")

    print("\n2. 次级异常不丢（拼进原因）")
    t, r = merge_types(2, "进线未正常完成：进线时长 40.0s < 最短 60s",
                       3, "球化时间超过标准值：实际 120.0s > 上限 90s")
    check("主类型 2", t == 2, f"{t}")
    check("原因里有「另：」+ 超标准", "另：" in r and "超过标准值" in r, r)
    check("原因不超 200 字", len(r) <= 200, f"{len(r)} 字")

    print("\n3. 有动作没亮光 → 类型1")
    rec = build_final(heat(), None, reaction_count=1)
    check("type=1", rec.abnormal_type == TYPE_NO_LIGHT, f"{rec.abnormal_type}")
    check("原因说明", "未检测到亮光" in rec.abnormal_reason, rec.abnormal_reason)
    check("失败标记", rec.ok is False)

    print("\n4. 进线中断 + 有亮光 → 仍是 2（亮光的超标准降为次级）")
    h = heat(start=1000.0, end=1040.0, judged=TYPE_FEED_BREAK,
             reason="进线未正常完成：进线时长 40.0s < 最短 60s", light=True)
    lr = LightResult(1013.0, 1050.0, 37.0, abnormal_type=4,
                     reason="球化时间低于标准值：实际 37.0s < 下限 60s", ok=False)
    rec = build_final(h, lr, reaction_count=2)
    check("type=2", rec.abnormal_type == TYPE_FEED_BREAK, f"{rec.abnormal_type}")
    check("亮光时长也记下", rec.light_duration == 37.0, f"{rec.light_duration}")
    check("次级进原因", "另：" in rec.abnormal_reason, rec.abnormal_reason)

    print("\n5. 配对的窗口判定")
    check("亮光起点在窗口内 → 配上", matches(heat(), LightResult(1013.0, 1103.0, 90.0)))
    check("亮光起点早太多 → 不配", not matches(heat(), LightResult(900.0, 1000.0, 100.0)))
    check("亮光起点晚太多 → 不配", not matches(heat(), LightResult(1200.0, 1300.0, 100.0)))

    print("\n6. FusionLog：亮光后到 / 先到 都要配上")
    for order in ("heat_first", "light_first"):
        tmp = tempfile.mkdtemp(prefix="fusion-")
        path = os.path.join(tmp, "heat_records.jsonl")
        now = [1000.0]
        log = FusionLog(path, clock=lambda: now[0])
        h = heat(light=True)
        lr = LightResult(1013.0, 1103.0, 90.0, abnormal_type=3,
                         reason="球化时间超过标准值：实际 90.0s > 上限 90s", ok=False)
        if order == "heat_first":
            log.add_heat(h)
            log.add_light(lr)
        else:
            log.add_light(lr)
            log.add_heat(h)
        now[0] = 1200.0
        out = log.settle()
        check(f"{order} → 出 1 条", len(out) == 1, f"{len(out)} 条")
        if out:
            check(f"{order} → 带上亮光时长", out[0]["actual_spheroidization_time"] == "00:01:30.00",
                  str(out[0]["actual_spheroidization_time"]))
            check(f"{order} → 类型来自亮光(3)", out[0]["abnormal_type"] == 3,
                  f"{out[0]['abnormal_type']}")
        lines = open(path, encoding="utf-8").read().strip().split("\n")
        check(f"{order} → 落盘 1 行", len(lines) == 1 and json.loads(lines[0]), "")

    print("\n7. 记录字段完整性（对齐后端形状）")
    tmp = tempfile.mkdtemp(prefix="fusion-")
    log = FusionLog(os.path.join(tmp, "r.jsonl"), clock=lambda: 1300.0)
    log.add_heat(heat(light=True))
    log.add_light(LightResult(1013.0, 1103.0, 90.0, 0, "", True))
    rec = log.flush()[0]
    want = ("reaction_count", "detection_time", "spheroidization_start_time",
            "spheroidization_end_time", "actual_spheroidization_time",
            "spheroidization_abnormal", "abnormal_type", "abnormal_reason",
            "spheroidization_wire_entry_start_time", "spheroidization_wire_entry_end_time",
            "spheroidization_wire_entry_total_duration", "spheroidization_weight",
            "plc_button_time", "plc_arrival_time")
    missing = [k for k in want if k not in rec]
    check("字段齐全", not missing, f"缺 {missing}")
    check("进线时长算对 (100s)", rec["spheroidization_wire_entry_total_duration"] == 100.0,
          str(rec["spheroidization_wire_entry_total_duration"]))
    check("正常炉 abnormal=0", rec["spheroidization_abnormal"] == 0,
          str(rec["spheroidization_abnormal"]))

    print("\n8. 分段明细时间单位（1970 那个 bug 的回归）")
    segs = (Segment(1000.0, 1060.0), Segment(1090.0, 1150.0, gap_before=30.0))
    shifted = shift_segments(segs, 1_700_000_000.0)
    check("起止平移到日历秒", shifted[0].start == 1_700_001_000.0
          and shifted[1].end == 1_700_001_150.0, f"{shifted[0].start}")
    check("停顿值不变", shifted[1].gap_before == 30.0, str(shifted[1].gap_before))
    check("时长不变", abs(shifted[0].duration - 60.0) < 1e-9)
    h = heat(start=1_700_000_013.0, end=1_700_000_103.0)
    lr = LightResult(1_700_000_013.0, 1_700_000_113.0, 100.0, 5,
                     "球化过程中间亮光停顿：第 2 段前停顿 30s", False, shifted)
    rec = build_final(h, lr, reaction_count=9).to_dict()
    seg0 = rec["spheroidization_segments"][0]["start"]
    check("记录里的分段不是 1970", not seg0.startswith("1970"), seg0)
    check("分段与检测时间同一天", seg0[:10] == rec["detection_time"][:10],
          f"{seg0} vs {rec['detection_time']}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
