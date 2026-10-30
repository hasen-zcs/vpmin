"""一炉状态机单元测试：类型 1（进站未球化）、类型 2（进线中断）。

用法：
    python3 tests/test_heat_tracker.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.heat import (TYPE_FEED_BREAK, TYPE_NO_LIGHT, HeatConfig,
                               HeatTracker)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    cfg = HeatConfig(no_action_sec=120, feed_end_timeout=240, feed_min_duration=60)

    print("1. 正常炉：到位→喂丝开始→喂丝结束，什么异常都不报")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    tr.on_event("feed_end", 100.0, raw_ts=124.0)
    closed = tr.on_event("arrival", 700.0, weight=1810)     # 下一炉到位，收掉上一炉
    check("关 1 炉", len(closed) == 1)
    check("不判异常", closed and closed[0].judged_type == 0,
          f"type={closed[0].judged_type if closed else None}")
    check("进线时长 95s", closed and abs(closed[0].feed_duration - 95.0) < 1e-6)

    print("\n2. 类型1：到位后 120s 内没动作、没亮光")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    check("119s 时还不报", not tr.tick(119.0))
    out = tr.tick(121.0)
    check("121s 时报类型1", out and out[0].judged_type == TYPE_NO_LIGHT,
          out[0].reason if out else "无输出")

    print("\n3. 类型1 不该误报：到位后有亮光（PLC 动作位丢了也得放过）")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("light_start", 20.0)
    check("130s 不判类型1", not tr.tick(130.0))

    print("\n4. 类型1 不该误报：到位后有按钮")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("button", 30.0)
    check("130s 不判类型1", not tr.tick(130.0))

    print("\n5. 类型2a：喂丝开始后 240s 没等到结束位")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    check("239s 时还不报", not tr.tick(244.0))
    out = tr.tick(246.0)
    check("246s 时报类型2", out and out[0].judged_type == TYPE_FEED_BREAK,
          out[0].reason if out else "无输出")

    print("\n6. 类型2b：进线时长 40s < 最短 60s")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    out = tr.on_event("feed_end", 45.0, raw_ts=69.0)
    check("立刻报类型2", out and out[0].judged_type == TYPE_FEED_BREAK,
          out[0].reason if out else "无输出")
    check("原因写了时长", out and "40.0s" in out[0].reason,
          out[0].reason if out else "")

    print("\n7. 类型2 优先于类型1（同一炉既有中断又没亮光）")
    tr = HeatTracker(cfg)
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    out = tr.tick(246.0)
    check("判 2 不判 1", out and out[0].judged_type == TYPE_FEED_BREAK,
          f"type={out[0].judged_type if out else None}")

    print("\n8. next_arrival 模式：到位后一直没动作，到下一炉才追判")
    cfg2 = HeatConfig(no_action_mode="next_arrival", no_action_sec=120)
    tr = HeatTracker(cfg2)
    tr.on_event("arrival", 0.0, weight=1800)
    check("120s 时不判（等下一炉）", not tr.tick(200.0))
    out = tr.on_event("arrival", 700.0, weight=1810)
    check("下一炉到位时追判类型1", out and out[0].judged_type == TYPE_NO_LIGHT,
          out[0].reason if out else "无输出")

    print("\n9. 长空闲关炉：有动作但没亮光结束，也要收口")
    tr = HeatTracker(HeatConfig(close_idle_sec=600))
    tr.on_event("arrival", 0.0, weight=1800)
    tr.on_event("feed_start", 5.0)
    tr.on_event("feed_end", 100.0, raw_ts=124.0)
    out = tr.tick(800.0)
    check("超空闲关炉且不判异常", out and out[0].judged_type == 0,
          f"type={out[0].judged_type if out else None}")

    print("\n10. 没有到位事件也能从动作开炉（PLC 到位信号缺失兜底）")
    tr = HeatTracker(cfg)
    tr.on_event("feed_start", 5.0, weight=1800)
    check("开炉成功", tr.current is not None and tr.current.weight == 1800)
    out = tr.on_event("feed_end", 100.0, raw_ts=124.0)
    check("不误判 1/2", not out)

    print("\n11. 按钮 = 判定开始条件（锚点）")
    tr = HeatTracker(cfg)
    tr.on_event("button", 50.0, weight=1800)
    check("按钮开炉且锚点是按钮", tr.current is not None
          and tr.current.anchor == "button" and tr.current.anchor_ts == 50.0,
          tr.current.anchor if tr.current else "无")
    tr.on_event("feed_start", 80.0)
    check("后到的喂丝开始不改锚点", tr.current.anchor_ts == 50.0, str(tr.current.anchor_ts))
    out = tr.on_event("feed_end", 180.0, raw_ts=204.0)
    check("正常关炉不判异常", not out or out[0].judged_type == 0)

    print("\n12. 先喂丝后补按按钮：锚点仍是最早那个")
    tr = HeatTracker(cfg)
    tr.on_event("feed_start", 10.0, weight=1800)
    tr.on_event("button", 25.0)
    check("锚点仍是喂丝开始", tr.current.anchor == "feed_start"
          and tr.current.anchor_ts == 10.0,
          f"{tr.current.anchor}@{tr.current.anchor_ts}")
    check("按钮时刻单独记着", tr.current.button_ts == 25.0)

    print("\n13. 连按去抖：同一炉只认第一次")
    tr = HeatTracker(cfg)
    tr.on_event("button", 100.0, weight=1800)
    tr.on_event("button", 103.0)          # 抖动
    tr.on_event("button", 107.0)          # 还在 10s 内
    check("只记第一次", tr.current.button_ts == 100.0, str(tr.current.button_ts))

    print("\n14. 按钮后 120s 无亮光 → 未球化；亮光来了就不判")
    tr = HeatTracker(cfg)
    tr.on_event("button", 100.0, weight=1800)
    check("119s 不判", not tr.tick(219.0))
    out = tr.tick(221.0)
    check("121s 判类型1", out and out[0].judged_type == TYPE_NO_LIGHT,
          out[0].reason if out else "无输出")
    tr2 = HeatTracker(cfg)
    tr2.on_event("button", 100.0, weight=1800)
    tr2.on_event("light_start", 130.0)
    check("有亮光就不判", not tr2.tick(260.0))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
