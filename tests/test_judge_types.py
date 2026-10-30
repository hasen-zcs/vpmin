"""判定单元测试：R5 的 3/4/5（超标准 / 低于标准 / 中间停顿）+ 优先级 + 边界 + 舍入。

口径（老板 2026-10-29 定）：判定时长 = 亮光时长（首段起 → 末段止，含停顿）。

用法：
    python3 tests/test_judge_types.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.domain.judge import (TYPE_NORMAL, TYPE_OVER, TYPE_PAUSE, TYPE_UNDER,
                               TYPE_UNJUDGED, judge)
from vpmin.domain.models import Segment, Window

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


W = Window(60.0, 90.0, "后端上下限")
SEG2 = (Segment(0.0, 30.0), Segment(48.0, 100.0, gap_before=18.0))   # 中间停 18s


def main():
    print("1. 正常：75s 在 60~90 内")
    v = judge(75.0, W)
    check("成功 + type=0 + 无原因", v.ok is True and v.abnormal_type == TYPE_NORMAL
          and v.abnormal_reason == "", f"{v.text} type={v.abnormal_type}")

    print("\n2. 类型3 超标准")
    v = judge(95.2, W)
    check("失败 + type=3", v.ok is False and v.abnormal_type == TYPE_OVER, f"type={v.abnormal_type}")
    check("原因带实际值和上限", "95.2" in v.abnormal_reason and "90" in v.abnormal_reason,
          v.abnormal_reason)

    print("\n3. 类型4 低于标准")
    v = judge(58.3, W)
    check("失败 + type=4", v.ok is False and v.abnormal_type == TYPE_UNDER, f"type={v.abnormal_type}")
    check("原因带实际值和下限", "58.3" in v.abnormal_reason and "60" in v.abnormal_reason,
          v.abnormal_reason)

    print("\n4. 类型5 中间停顿")
    v = judge(100.0, W, SEG2, pause_threshold=10.0)
    check("失败 + type=5", v.ok is False and v.abnormal_type == TYPE_PAUSE, f"type={v.abnormal_type}")
    check("原因指出第 2 段前停 18s", "第 2 段" in v.abnormal_reason and "18" in v.abnormal_reason,
          v.abnormal_reason)
    check("分段带出来了", len(v.segments) == 2 and v.segmented)

    print("\n5. 优先级：停顿 > 超上限 > 低于下限")
    v = judge(120.0, W, SEG2, pause_threshold=10.0)      # 同时超上限
    check("同时超上限仍判 5", v.abnormal_type == TYPE_PAUSE, f"type={v.abnormal_type}")
    v = judge(40.0, W, SEG2, pause_threshold=10.0)       # 同时低于下限
    check("同时低于下限仍判 5", v.abnormal_type == TYPE_PAUSE, f"type={v.abnormal_type}")

    print("\n6. 停顿阈值边界（必须 > T 才算停顿）")
    v = judge(100.0, W, (Segment(0, 30), Segment(40, 100, gap_before=10.0)), pause_threshold=10.0)
    check("gap=10.0 = T → 不算停顿", v.abnormal_type != TYPE_PAUSE, f"type={v.abnormal_type}")
    v = judge(100.0, W, (Segment(0, 30), Segment(40.001, 100, gap_before=10.001)), pause_threshold=10.0)
    check("gap=10.001 > T → 停顿", v.abnormal_type == TYPE_PAUSE, f"type={v.abnormal_type}")
    v = judge(100.0, W, (Segment(0, 30), Segment(40, 100, gap_before=10.0)), pause_threshold=0.0)
    check("T=0（关闭）→ 不判停顿", v.abnormal_type != TYPE_PAUSE, f"type={v.abnormal_type}")

    print("\n7. 单段（gap 为空）永不判停顿")
    v = judge(100.0, W, (Segment(0.0, 100.0),), pause_threshold=10.0)
    check("单段 → type=3 不是 5", v.abnormal_type == TYPE_OVER, f"type={v.abnormal_type}")

    print("\n8. 区间边界（闭区间）")
    check("90.0 恰好上限 → 成功", judge(90.0, W).ok is True)
    check("60.0 恰好下限 → 成功", judge(60.0, W).ok is True)
    check("90.001 → 超上限", judge(90.001, W).abnormal_type == TYPE_OVER)
    check("59.999 → 低于下限", judge(59.999, W).abnormal_type == TYPE_UNDER)

    print("\n9. 浮点噪声（10-27 那批 102.47999999999999）")
    v = judge(102.47999999999999, W)
    check("原因显示 102.48 不带长尾", "102.48" in v.abnormal_reason, v.abnormal_reason)
    check("值被 round 到 3 位", v.duration == 102.48, f"duration={v.duration}")
    check("90.0000004 → 成功（舍入后 90.0）", judge(90.0000004, W).ok is True)

    print("\n10. 未判定（缺时长或缺标准）")
    v = judge(None, W)
    check("缺时长 → type=6 且 ok=None", v.abnormal_type == TYPE_UNJUDGED and v.ok is None,
          f"type={v.abnormal_type}")
    v = judge(75.0, Window())
    check("缺标准 → type=6", v.abnormal_type == TYPE_UNJUDGED, f"type={v.abnormal_type}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
