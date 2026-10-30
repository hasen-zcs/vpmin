"""状态机逐帧对拍：原版 ReactionDetector vs 新实现 ReactionStateMachine。

纯 Python，不依赖 cv2/numpy。

用法：
    python3 tests/test_parity_state_machine.py

生成 200 条随机指标序列（每条 600 帧），逐帧同时推进两个实现，
断言 state / started / ended / start_t / end_t 五字段完全相等。
"""

import os
import random
import sys

# ---- 路径：原版在 /path/to/vp10.8.3-production，新实现在本仓库 ----
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/path/to/vp10.8.3-production")
sys.path.insert(0, HERE)

from core.reaction_detector import ReactionDetector      # 原版，直接导入不复制
from vpmin.domain.state_machine import ReactionStateMachine  # 新实现

N_TRACES = 200
FRAMES = 600
CONFIRM_PAIRS = [(1.0, 3.0), (0.2, 4.0), (0.0, 0.0)]

# 原版 highlight 模式：start_ok = start_pct >= 3.0，end_ok = end_pct >= 3.0
# 用 10.0 / 0.0 两个取值精确映射布尔信号。
ON, OFF = 10.0, 0.0


def gen_trace(rng):
    """一条 600 帧序列：帧间隔 0.05~0.2s 累加；信号「保持 3~120 帧后翻转」。"""
    start_ok = False
    end_ok = False
    start_hold = rng.randint(3, 120)
    end_hold = rng.randint(3, 120)
    frames = []
    elapsed = 0.0
    for _ in range(FRAMES):
        frames.append((elapsed, start_ok, end_ok))
        elapsed += rng.uniform(0.05, 0.2)
        start_hold -= 1
        if start_hold == 0:
            start_ok = not start_ok
            start_hold = rng.randint(3, 120)
        end_hold -= 1
        if end_hold == 0:
            end_ok = not end_ok
            end_hold = rng.randint(3, 120)
    return frames


def run_trace(trace_id, frames, start_confirm, end_confirm):
    detector = ReactionDetector(mode="highlight",
                                start_confirm_seconds=start_confirm,
                                end_confirm_seconds=end_confirm)
    machine = ReactionStateMachine(start_confirm, end_confirm)

    for f, (now, start_ok, end_ok) in enumerate(frames):
        t = machine.advance(now, start_ok, end_ok)
        d = detector.update(now=now,
                            brightness_rate=0.0,
                            highlight_240_percent=ON if start_ok else OFF,
                            highlight_end_percent=ON if end_ok else OFF)

        # ended 的那一帧：两边同一帧内、同一位置各自 rearm
        if t.ended:
            machine.rearm()
        if d["ended_now"]:
            detector.rearm()

        pairs = [
            ("state",  t.state,  d["state"]),
            ("started", t.started, d["started_now"]),
            ("ended",  t.ended,  d["ended_now"]),
            ("start_t", machine.start_t, detector.reaction_start_time),
            ("end_t",  machine.end_t,  detector.reaction_end_time),
        ]
        for name, a, b in pairs:
            if a != b:
                print(
                    f"MISMATCH trace={trace_id} frame={f} now={now!r} "
                    f"start_ok={start_ok} end_ok={end_ok} field={name} "
                    f"mine={a!r} ref={b!r}\n"
                    f"  mine: state={t.state} started={t.started} "
                    f"ended={t.ended} start_t={machine.start_t!r} "
                    f"end_t={machine.end_t!r}\n"
                    f"  ref:  state={d['state']} started_now={d['started_now']} "
                    f"ended_now={d['ended_now']} "
                    f"reaction_start_time={detector.reaction_start_time!r} "
                    f"reaction_end_time={detector.reaction_end_time!r}"
                )
                raise AssertionError(
                    f"对拍不一致：trace={trace_id} frame={f} field={name}"
                )


def main():
    rng = random.Random(1234)
    traces = [gen_trace(rng) for _ in range(N_TRACES)]

    # 四种 (start_ok, end_ok) 组合都必须出现过
    combos = {
        (s, e) for frames in traces for (_, s, e) in frames
    }
    missing = {(True, True), (True, False),
               (False, True), (False, False)} - combos
    if missing:
        print(f"FAIL 信号生成缺组合：{sorted(missing)}")
        return 1

    for trace_id, frames in enumerate(traces):
        start_confirm, end_confirm = CONFIRM_PAIRS[trace_id % len(CONFIRM_PAIRS)]
        run_trace(trace_id, frames, start_confirm, end_confirm)

    print(f"PASS {N_TRACES} traces x {FRAMES} frames")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"FAIL {e}")
        sys.exit(1)
