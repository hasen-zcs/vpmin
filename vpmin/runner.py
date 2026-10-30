"""应用层：只做编排，一帧进来按顺序驱动领域层与适配器。

断点续接（merge_gap）：
    判到结束后不立刻落盘，先挂起 merge_gap 秒；这期间若又判到开始，
    就当成同一炉的续段——序号不变、起点不变、曲线接回来，结束时刻后移。
    超过 merge_gap 才真正落盘。merge_gap=0 时行为与旧版完全一致（立即落盘）。
"""

from datetime import datetime
from typing import List, Optional, Tuple

from .adapters.formats import wall
from .domain.curve import CurveBuffer
from .domain.filter import MedianFilter
from .domain.judge import judge
from .domain.models import Segment
from .domain.state_machine import ReactionStateMachine
from .domain.standard import StandardPolicy
from .domain.trigger import Thresholds


class ReactionRunner:
    def __init__(self, probe, thresholds: Thresholds, machine: ReactionStateMachine,
                 standard: StandardPolicy, curve: CurveBuffer, painter, sink, counter,
                 standard_feed=None, base: Optional[datetime] = None, merge_gap: float = 0.0,
                 guard_window: float = 0.0, guard_peak: float = 80.0,
                 min_duration: float = 0.0, pause_threshold: float = 0.0,
                 median_frames: int = 0):
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
        self.merge_gap = max(0.0, float(merge_gap))
        self.guard_window = max(0.0, float(guard_window))
        self.guard_peak = float(guard_peak)
        self.min_duration = max(0.0, float(min_duration))
        self.pause_threshold = max(0.0, float(pause_threshold))   # 中间停顿阈值（秒），0=不判
        self.median_frames = max(0, int(median_frames))            # 因果中值帧数，0/1=关
        self.median = MedianFilter(self.median_frames)
        self.count = 0
        self.last_verdict = None      # 最近一次判完的冻结结果（给状态页）
        self.reaction_start = None    # 当前这条反应的起点（新反应开始时清空终点）
        self.reaction_end = None
        self.pending = None           # 已判结束、等待合并窗口的落盘缓冲
        self.guard = None             # 杂光考察期：{"t0", "peak"}
        self.last_merge_info = None   # 最近一次落盘是否合并过（给页面看）
        self.dropped = 0              # 累计丢弃的杂光次数（给页面看）
        self._flushed = []            # 已就绪、等 analysis 取走的记录

    # ---------------- 生命周期 ----------------
    def reset(self) -> None:
        """重新「开始分析」时清干净上一条反应（序号与落盘记录不动）。"""
        self.machine.reset()
        self.curve.reset()
        self.median.reset()
        self.reaction_start = None
        self.reaction_end = None
        self.last_verdict = None
        self.pending = None
        self.guard = None

    # ---------------- 每帧 ----------------
    def process(self, frame, elapsed: float) -> None:
        """推进一帧。产出不在这里返回，统一由 drain() 取走。"""
        metrics = self.probe.measure(frame, elapsed)
        if metrics is None:
            return

        if self.standard_feed is not None:
            refreshed = self.standard_feed.poll(elapsed)
            if refreshed is not None:
                payload, ok = refreshed
                self.standard.update(payload, ok, now=elapsed)

        # 挂起的上一段：过了合并窗口就落盘
        self._expire(elapsed)

        # 因果中值先滤一遍抖动，再交给状态机（原始值仍照旧进逐帧 CSV/曲线）
        start_pct, end_pct = self.median.apply(metrics.start_pct, metrics.end_pct)
        transition = self.machine.advance(
            elapsed,
            self.thresholds.start_ok(start_pct),
            self.thresholds.end_ok(end_pct),
        )

        if transition.started:
            if self.pending is not None:
                # 断点续接：同一炉的下一段（序号/起点不变，曲线接回来）
                segs = self.pending["segments"]
                gap = None
                if segs and segs[-1]["end"] is not None:
                    gap = max(0.0, self.machine.start_t - segs[-1]["end"])
                segs.append({"start": self.machine.start_t, "end": None, "gap_before": gap})
                self.reaction_start = segs[0]["start"]
                self.reaction_end = None
                self.curve.resume()
                self.guard = None            # 续接段不再考考察期
            else:
                self.count = max(1, self.counter.next())
                self.reaction_start = self.machine.start_t
                self.reaction_end = None          # 新反应：清掉上一条的终点
                self.curve.begin()
                # 新炉：段列表从起判那一刻就建好，结束/续接时往里填
                self.pending = {"segments": [{"start": self.machine.start_t, "end": None,
                                              "gap_before": None}],
                                "end_metrics": None}
                # 杂光考察期：起判后 guard_window 秒内峰值要够高，否则当开门杂光丢掉
                self.guard = ({"t0": self.machine.start_t, "peak": metrics.start_pct}
                              if self.guard_window > 0 else None)

        self.curve.push(elapsed, metrics.mean_gray)

        # ---- 杂光考察期 ----
        if self.guard is not None:
            if metrics.start_pct > self.guard["peak"]:
                self.guard["peak"] = metrics.start_pct
            # 考察期没跑完就判结束了 → 这么短的不是真反应，直接丢
            if transition.ended:
                self._drop_spurious("结束太快")
            elif elapsed - self.guard["t0"] >= self.guard_window:
                if self.guard["peak"] < self.guard_peak:
                    self._drop_spurious("峰值%.0f%%<%.0f%%" % (self.guard["peak"], self.guard_peak))
                else:
                    self.guard = None         # 转正，按正常反应继续

        if transition.ended and self.guard is None:
            self.reaction_end = self.machine.end_t
            if self.pending is None:
                self.pending = {"segments": [], "end_metrics": None}
            if not self.pending["segments"]:
                self.pending["segments"].append({"start": self.reaction_start,
                                                 "end": None, "gap_before": None})
            self.pending["segments"][-1]["end"] = self.machine.end_t
            self.pending["end_metrics"] = metrics
            self.curve.close()
            self.machine.rearm()
            if self.merge_gap <= 0:
                self._commit()

    # ---------------- 取结果 ----------------
    def drain(self) -> List[Tuple[dict, float, object]]:
        """取走已就绪的记录：[(record, end_elapsed, end_metrics), ...]"""
        out = self._flushed
        self._flushed = []
        return out

    def flush_pending(self) -> None:
        """停止分析时，把还在合并窗口里的那条也落盘（不能丢）。"""
        if self.pending is not None:
            self._commit()

    # ---------------- 杂光过滤 ----------------
    def _drop_spurious(self, reason: str) -> None:
        """开门杂光：状态复位、序号退回、不落盘。"""
        peak = self.guard["peak"] if self.guard else 0.0
        self.dropped += 1
        self.last_merge_info = None
        self.guard = None
        self.pending = None
        self.machine.reset()
        self.curve.reset()
        self.median.reset()
        self.reaction_start = None
        self.reaction_end = None
        self.count = self.counter.rollback() if hasattr(self.counter, "rollback") else self.count
        if self.dropped <= 3 or self.dropped % 20 == 0:        # 日志限流，别刷爆
            print("[drop] 杂光丢弃 #%s 峰值%.1f%% (%s) 累计%d次" % (
                self.count + 1, peak, reason, self.dropped))

    def _drop_short(self, dur: float) -> None:
        """太短的反应：同样是杂光（开门/余辉），不落盘。"""
        self.dropped += 1
        self.last_merge_info = None
        self.count = self.counter.rollback() if hasattr(self.counter, "rollback") else self.count
        if self.dropped <= 3 or self.dropped % 20 == 0:
            print("[drop] 时长%.1fs < 最短%.0fs，丢弃 #%s 累计%d次" % (
                dur, self.min_duration, self.count + 1, self.dropped))

    # ---------------- 内部 ----------------
    def _expire(self, elapsed: float) -> None:
        p = self.pending
        if p is None or not p["segments"]:
            return
        end_t = p["segments"][-1]["end"]
        if end_t is None:
            return
        if elapsed - end_t >= self.merge_gap:
            self._commit()

    def _commit(self) -> None:
        p = self.pending
        self.pending = None
        if p is None or not p["segments"]:
            return
        raw = [s for s in p["segments"] if s["end"] is not None]
        if not raw:
            return
        start_t, end_t = raw[0]["start"], raw[-1]["end"]
        duration = max(0.0, end_t - start_t)          # 首段起 → 末段止（含停顿）
        if self.min_duration > 0 and duration < self.min_duration:
            self._drop_short(duration)
            return
        segments = tuple(Segment(s["start"], s["end"], s["gap_before"]) for s in raw)
        window = self.standard.window()
        verdict = judge(duration, window, segments, self.pause_threshold)
        self.last_verdict = verdict
        # 曲线在续接后是拼好的完整一条，画图自然也是连着的
        image = self.painter.draw(self.curve.view(), start_t, end_t)
        record = self.sink.build(self.count, wall(self.base, start_t),
                                 wall(self.base, end_t), verdict, image,
                                 self._extras(verdict, segments))
        n = len(segments)
        # 原 7 字段不动，新字段追加（合并上传那边按字段名读，天然兼容）
        self.last_merge_info = {"segments": n, "gap_merged": n > 1}
        self._flushed.append((record, end_t, p["end_metrics"]))

    def _extras(self, verdict, segments) -> dict:
        """R2/R5 追加字段：异常类型 + 原因 + 分段明细（仅真分段时输出）。"""
        out = {"abnormal_type": verdict.abnormal_type,
               "abnormal_reason": verdict.abnormal_reason}
        if len(segments) > 1:
            out["spheroidization_segments"] = [
                {"seq": i,
                 "start": wall(self.base, s.start),
                 "end": wall(self.base, s.end),
                 "duration_sec": round(s.duration, 3),
                 "gap_before_sec": (None if s.gap_before is None
                                    else round(s.gap_before, 3))}
                for i, s in enumerate(segments, start=1)
            ]
        return out
