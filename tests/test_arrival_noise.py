"""到位启发式的现场噪声回归（2026-10-29 影子实测踩坑）。

现场事实：没有「铁水到位」位；喂丝过程中重量本身就在动（1792→1800→1840），
老逻辑把任何 >=5kg 的变化（含下跌、含首拍）都当「新一炉到位」，
结果一炉被切成好几段、进线起止错配出负数时长、类型1 误报。

这里钉住四条修正：
1) 首拍只当基线，不产生 arrival
2) 只有「上涨」且涨幅够才算新铁水到位
3) 一炉进行中（喂丝未结束 / 还在亮）时，arrival 不许切炉
4) 按钮按下（超过去抖窗口且上一炉已有动作）= 新一炉开始，兼作炉边界
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vpmin.adapters.plc import EdgeDetector                      # noqa: E402
from vpmin.domain.heat import (HeatTracker, KIND_ARRIVAL, KIND_BUTTON,  # noqa: E402
                               KIND_FEED_START, KIND_FEED_END)


def kinds(evs):
    return [e.kind for e in evs]


class WeightArrivalTest(unittest.TestCase):
    def test_first_weight_is_only_baseline(self):
        """服务刚起来读到 1808kg，不能凭空来一条「到位」。"""
        det = EdgeDetector(points=(), weight_arrival=True)
        self.assertEqual(kinds(det.feed(1000.0, {}, 1808.0)), [])
        self.assertEqual(det.last_weight, 1808.0)

    def test_drop_is_not_arrival(self):
        det = EdgeDetector(points=(), weight_arrival=True)
        det.feed(1000.0, {}, 1808.0)
        self.assertEqual(kinds(det.feed(1010.0, {}, 1700.0)), [])

    def test_small_rise_is_not_arrival(self):
        """喂丝抖动 +3kg 不该当成新炉。"""
        det = EdgeDetector(points=(), weight_arrival=True)
        det.feed(1000.0, {}, 1800.0)
        self.assertEqual(kinds(det.feed(1060.0, {}, 1803.0)), [])

    def test_big_rise_is_arrival(self):
        det = EdgeDetector(points=(), weight_arrival=True)
        det.feed(1000.0, {}, 1500.0)
        self.assertEqual(kinds(det.feed(1060.0, {}, 1808.0)), ["arrival"])

    def test_dedupe_window(self):
        det = EdgeDetector(points=(), weight_arrival=True)
        det.feed(1000.0, {}, 1500.0)
        self.assertEqual(kinds(det.feed(1010.0, {}, 1808.0)), ["arrival"])
        det.feed(1015.0, {}, 1700.0)
        self.assertEqual(kinds(det.feed(1020.0, {}, 1810.0)), [])   # 30s 内不重发

    def test_disabled_by_config(self):
        det = EdgeDetector(points=(), weight_arrival=False)
        det.feed(1000.0, {}, 1500.0)
        self.assertEqual(kinds(det.feed(1060.0, {}, 1900.0)), [])


class ArrivalDoesNotChopTest(unittest.TestCase):
    def test_arrival_during_feed_does_not_split_heat(self):
        t = HeatTracker()
        t.on_event(KIND_FEED_START, 1000.0, 1800.0)
        closed = t.on_event(KIND_ARRIVAL, 1010.0, 1850.0)
        self.assertEqual(closed, [])
        self.assertEqual(len(t.closed), 0)
        self.assertIsNotNone(t.current)
        self.assertEqual(t.current.feed_start_ts, 1000.0)

    def test_arrival_when_idle_opens_new_heat(self):
        t = HeatTracker()
        t.on_event(KIND_ARRIVAL, 1000.0, 1800.0)
        self.assertEqual(len(t.closed), 0)
        self.assertEqual(t.current.anchor, KIND_ARRIVAL)


class ButtonBoundaryTest(unittest.TestCase):
    def test_second_button_starts_new_heat(self):
        t = HeatTracker()
        t.on_event(KIND_BUTTON, 1000.0, 1800.0)
        t.on_event(KIND_FEED_START, 1030.0, 1800.0)
        t.on_event(KIND_FEED_END, 1100.0, 1800.0)
        closed = t.on_event(KIND_BUTTON, 1300.0, 1800.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(len(t.closed), 1)
        self.assertEqual(closed[0].close_reason, "新一炉按钮按下")
        self.assertEqual(t.current.anchor, KIND_BUTTON)
        self.assertEqual(t.current.button_ts, 1300.0)

    def test_bounce_within_dedupe_ignored(self):
        t = HeatTracker()
        t.on_event(KIND_BUTTON, 1000.0, 1800.0)
        t.on_event(KIND_FEED_START, 1001.0, 1800.0)
        closed = t.on_event(KIND_BUTTON, 1002.0, 1800.0)
        self.assertEqual(closed, [])
        self.assertEqual(len(t.closed), 0)

    def test_button_without_previous_action_does_not_chop(self):
        """同一炉重复按下（还没有任何动作）不该切炉。"""
        t = HeatTracker()
        t.on_event(KIND_BUTTON, 1000.0, 1800.0)
        closed = t.on_event(KIND_BUTTON, 1200.0, 1800.0)
        self.assertEqual(closed, [])
        self.assertEqual(t.current.button_ts, 1000.0)      # 锚点不后移


if __name__ == "__main__":
    unittest.main(verbosity=2)
