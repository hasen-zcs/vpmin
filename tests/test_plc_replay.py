"""回放实验：用 1452 炉真实 PLC 数据验证「一炉状态机」能不能判 1/2 类且不误报。

数据：tests/data/plc_replay_all.jsonl（10-08 22:11 ~ 10-29 18:27，生产 all_plc.jsonl 全量只读拷贝）

三层验证：
  A. 位级回放（最近 60 炉）：ReplaySource 合成位电平 → EdgeDetector → HeatTracker 全链路
  B. 事件级回放（全部 1452 炉）：确认零误报、时长保真
  C. 故障注入：去掉结束 / 缩短进线 / 到位后没动作 → 必须报对类型，并量出检出延迟
  D. 重量启发式可靠性：连续相同重量会让"靠重量判到位"漏掉

用法：
    python3 tests/test_plc_replay.py
"""

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.adapters.plc import PlcEvent, ReplayHeat, ReplaySource
from vpmin.app.plc_loop import PlcEventLog, PlcLoop
from vpmin.domain.heat import (TYPE_FEED_BREAK, TYPE_NO_LIGHT, HeatConfig,
                               HeatTracker)

DATA = os.path.join(HERE, "tests", "data", "plc_replay_all.jsonl")
if not os.path.exists(DATA):
    print("SKIP: 缺少 tests/data/plc_replay_all.jsonl（生产真实 PLC 数据，不随公开仓库发布）")
    sys.exit(0)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _t(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def load_heats():
    heats = []
    for line in open(DATA, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        s = d.get("spheroidization_wire_entry_start_time")
        e = d.get("spheroidization_wire_entry_end_time")
        if not s or not e:
            continue
        heats.append(ReplayHeat(_t(s), _t(e), d.get("spheroidization_weight")))
    heats.sort(key=lambda h: h.start_ts)
    return heats


def part_a_bit_replay(heats):
    print("A. 位级回放（最近 60 炉，含 24s 结束位延迟 + 到位脉冲）")
    sub = heats[-60:]
    tracker = HeatTracker(HeatConfig())

    def cb(ev):                       # 事件对象 → 状态机入参（生产里由 PlcLoop 做）
        tracker.on_event(ev.kind, ev.ts, ev.weight, ev.raw_ts)

    src = ReplaySource(sub, cb, arrival_lead=30, poll=0.5)
    src.run()
    tracker.tick(src.heats[-1].end_ts + 700)        # 空闲关炉
    closed = tracker.closed
    print(f"     回放 {len(sub)} 炉，出 {len(closed)} 条记录，{src.stats['ticks']} 拍 / {src.stats['events']} 个事件")
    check("炉数对得上", len(closed) == len(sub), f"{len(closed)}/{len(sub)}")
    bad = [h for h in closed if h.judged_type != 0]
    check("零误报（没有假 1/2 类）", not bad,
          "; ".join(f"{h.judged_type}:{h.reason}" for h in bad[:3]))
    diffs = []
    for got, want in zip(closed, sub):
        if got.feed_duration is None:
            continue
        diffs.append(abs(got.feed_duration - (want.end_ts - want.start_ts)))
    check("进线时长保真（误差 ≤ 1.0s）", diffs and max(diffs) <= 1.0,
          f"最大误差 {max(diffs):.2f}s" if diffs else "无数据")


def part_b_event_replay(heats):
    print(f"\nB. 事件级回放（全部 {len(heats)} 炉）")
    tracker = HeatTracker(HeatConfig())
    closed = []
    for h in heats:
        for ev in tracker.on_event("arrival", h.start_ts - 30, weight=h.weight):
            closed.append(ev)
        for ev in tracker.on_event("feed_start", h.start_ts, weight=h.weight):
            closed.append(ev)
        for ev in tracker.on_event("feed_end", h.end_ts, raw_ts=h.end_ts + 24):
            closed.append(ev)
        closed += tracker.tick(h.start_ts + 260)        # 推一拍，别让超时误触发
    closed += tracker.tick(heats[-1].end_ts + 700)
    print(f"     出 {len(closed)} 条记录")
    check("炉数一致", len(closed) == len(heats), f"{len(closed)}/{len(heats)}")

    # 真实数据里本来就有进线超短的炉（2s / 10s），判成类型2 是**对的**，不是误报
    short = [h for h in heats if (h.end_ts - h.start_ts) < 60.0]
    flagged = [h for h in closed if h.judged_type == TYPE_FEED_BREAK]
    check(f"类型2 命中真实短进线炉 {len(short)} 条（不多不少）",
          len(flagged) == len(short), f"报了 {len(flagged)} 条 / 实际短炉 {len(short)} 条")
    others = [h for h in closed if h.judged_type not in (0, TYPE_FEED_BREAK)]
    check("没有别的误判", not others,
          "; ".join(f"type={h.judged_type} {h.reason}" for h in others[:3]))
    for h in sorted(short, key=lambda x: x.end_ts - x.start_ts)[:5]:
        print(f"     真实短炉：{datetime.fromtimestamp(h.start_ts)} 进线 {h.end_ts - h.start_ts:.1f}s")


def part_c_injections(heats):
    print("\nC. 故障注入（必须报对类型）")
    base = heats[-1]

    # C1 丢掉喂丝结束 → 类型2a（默认阈值 180s，无亮光否决生效）
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    out = tr.tick(base.start_ts + 179)
    check("C1 179s 还不报（阈值没到）", not out)
    out = tr.tick(base.start_ts + 181)
    check("C1 181s 报类型2（无结束且无亮光）", out and out[0].judged_type == TYPE_FEED_BREAK,
          out[0].reason if out else "无输出")
    print("     检出延迟：喂丝开始后 180s（正常炉平均进线 ~100s）")

    # C1b 同样超时但还在亮 → 必须放过（长炉）
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    tr.on_event("light_start", base.start_ts + 13)
    check("C1b 亮着的时候 200s 不报", not tr.tick(base.start_ts + 200))
    check("C1b 亮着的时候 400s 仍不报", not tr.tick(base.start_ts + 400))

    # C1c 熄了但还在静默窗口内 → 不报；过了静默窗口才报
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    tr.on_event("light_start", base.start_ts + 13)
    tr.on_event("light_end", base.start_ts + 200)
    check("C1c 刚熄 10s（<静默60s）不报", not tr.tick(base.start_ts + 210))
    out = tr.tick(base.start_ts + 300)
    check("C1c 熄了 100s 仍无结束位 → 报类型2", out and out[0].judged_type == TYPE_FEED_BREAK,
          out[0].reason if out else "无输出")

    # C2 进线只有 40s → 类型2b（立刻报）
    tr = HeatTracker(HeatConfig())
    tr.on_event("arrival", base.start_ts - 30, weight=base.weight)
    tr.on_event("feed_start", base.start_ts)
    out = tr.on_event("feed_end", base.start_ts + 40, raw_ts=base.start_ts + 64)
    check("C2 40s 进线 → 立刻报类型2", out and out[0].judged_type == TYPE_FEED_BREAK,
          out[0].reason if out else "无输出")

    # C3 到位后没人干活 → 类型1（window 模式）
    tr = HeatTracker(HeatConfig(no_action_sec=120))
    tr.on_event("arrival", base.start_ts, weight=base.weight)
    check("C3 119s 不报", not tr.tick(base.start_ts + 119))
    out = tr.tick(base.start_ts + 121)
    check("C3 121s 报类型1", out and out[0].judged_type == TYPE_NO_LIGHT,
          out[0].reason if out else "无输出")
    print("     检出延迟：到位后 120s（W1 可调；现场到位→按钮的真实时延未知，需采一轮数据校准）")

    # C4 到位后有亮光（PLC 动作位全丢）→ 不许报类型1
    tr = HeatTracker(HeatConfig(no_action_sec=120))
    tr.on_event("arrival", base.start_ts, weight=base.weight)
    tr.on_event("light_start", base.start_ts + 40)
    check("C4 有亮光就不报类型1", not tr.tick(base.start_ts + 200))


def part_d_weight_heuristic(heats):
    print("\nD. 「靠重量变化判到位」可靠性（真实数据）")
    same = sum(1 for a, b in zip(heats, heats[1:]) if a.weight == b.weight)
    total = max(1, len(heats) - 1)
    print(f"     相邻两炉重量完全相同：{same}/{total}（{same / total * 100:.1f}%）")
    check("结论：重量变化不足以判到位（必须加到位位或让重量归零）", True,
          "相邻同重量时会漏判" if same else "本批数据未出现，但规则上仍不保险")
    print("     → 建议：PLC 加一个「铁水到位」位（或重量在两炉之间归零），否则类型1 会漏")


def part_e_plcloop(heats):
    print("\nE. 同进程回路 PlcLoop（事件→状态机→落盘/回调）")
    import tempfile
    base = heats[-1]
    got = []

    class _Src:
        def __init__(self):
            self.on_event = None

        def start(self):
            pass

        def stop(self):
            pass

        def status(self):
            return {"connected": True}

    log_path = os.path.join(tempfile.mkdtemp(prefix="plcloop-"), "plc_events.jsonl")
    loop = PlcLoop(_Src(), HeatTracker(HeatConfig()), PlcEventLog(log_path),
                   on_heat=got.append)
    loop.handle(PlcEvent("arrival", base.start_ts, base.start_ts, base.weight))
    loop.handle(PlcEvent("feed_start", base.start_ts + 5, base.start_ts + 5, base.weight))
    loop.handle(PlcEvent("feed_end", base.start_ts + 35, base.start_ts + 59, base.weight))
    check("回调收到 1 条炉记录", len(got) == 1, f"{len(got)} 条")
    check("判成类型2（进线 30s）", got and got[0].judged_type == TYPE_FEED_BREAK,
          got[0].reason if got else "")
    lines = open(log_path, encoding="utf-8").read().strip().split("\n")
    check("事件全部落盘（3 条）", len(lines) == 3, f"{len(lines)} 条")
    check("落盘带墙钟和序号", '"seq": 1' in lines[0] and '"ts": "' in lines[0], lines[0][:60])
    st = loop.status()
    check("status 能看采集与当前炉", "source" in st and "closed_heats" in st)


def main():
    heats = load_heats()
    print(f"数据：{len(heats)} 炉，{datetime.fromtimestamp(heats[0].start_ts)} ~ "
          f"{datetime.fromtimestamp(heats[-1].start_ts)}\n")
    part_a_bit_replay(heats)
    part_b_event_replay(heats)
    part_c_injections(heats)
    part_d_weight_heuristic(heats)
    part_e_plcloop(heats)
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
