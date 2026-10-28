"""应用层：只做编排，一帧进来按顺序驱动领域层与适配器。"""

from datetime import datetime
from typing import Optional

from .adapters.formats import wall
from .domain.curve import CurveBuffer
from .domain.judge import judge
from .domain.state_machine import ReactionStateMachine
from .domain.standard import StandardPolicy
from .domain.trigger import Thresholds


class ReactionRunner:
    def __init__(self, probe, thresholds: Thresholds, machine: ReactionStateMachine,
                 standard: StandardPolicy, curve: CurveBuffer, painter, sink, counter,
                 standard_feed=None, base: Optional[datetime] = None):
        self.probe = probe
        self.thresholds = thresholds
        self.machine = machine
        self.standard = standard
        self.curve = curve
        self.painter = painter
        self.sink = sink
        self.counter = counter
        self.standard_feed = standard_feed
        self.base = base or datetime.now()
        self.count = 0
        self.last_verdict = None      # 最近一次判完的冻结结果（给状态页）
        self.reaction_start = None    # 当前这条反应的起点（新反应开始时清空终点）
        self.reaction_end = None


    def reset(self) -> None:
        """重新「开始分析」时清干净上一条反应（序号与落盘记录不动）。"""
        self.machine.reset()
        self.curve.reset()
        self.reaction_start = None
        self.reaction_end = None
        self.last_verdict = None

    def process(self, frame, elapsed: float) -> Optional[dict]:
        """推进一帧；判到结束时返回落盘记录，否则 None。"""
        metrics = self.probe.measure(frame, elapsed)
        if metrics is None:
            return None

        if self.standard_feed is not None:
            refreshed = self.standard_feed.poll(elapsed)
            if refreshed is not None:
                payload, ok = refreshed
                self.standard.update(payload, ok, now=elapsed)

        transition = self.machine.advance(
            elapsed,
            self.thresholds.start_ok(metrics.start_pct),
            self.thresholds.end_ok(metrics.end_pct),
        )

        if transition.started:
            self.count = self.counter.next()
            self.count = max(1, self.count)
            self.reaction_start = self.machine.start_t
            self.reaction_end = None          # 新反应：清掉上一条的终点

            self.curve.begin()

        self.curve.push(elapsed, metrics.mean_gray)

        if transition.ended:
            self.reaction_end = self.machine.end_t
            return self._finish()

        return None

    def _finish(self) -> dict:
        start_t, end_t = self.reaction_start, self.reaction_end
        window = self.standard.window()
        verdict = judge(max(0.0, end_t - start_t), window)
        self.last_verdict = verdict
        image = self.painter.draw(self.curve.points(), start_t, end_t)
        record = self.sink.build(self.count, wall(self.base, start_t),
                                 wall(self.base, end_t), verdict, image)

        self.curve.close()
        self.machine.rearm()
        return record
