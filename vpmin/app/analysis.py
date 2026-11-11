"""单机位分析循环：计时、逐帧记录、结束落图、停止时保半条曲线。

只做「一帧来了之后按顺序调谁」，判定逻辑全在 domain / runner 里。
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..adapters.formats import wall


class AnalysisLoop:
    def __init__(self, probe, runner, painter, image_store=None,
                 frame_csv=None, event_log=None, clock=None, frame_csv_factory=None,
                 wall_clock=None):
        self.probe = probe
        self.runner = runner
        self.painter = painter
        self.image_store = image_store
        self.frame_csv = frame_csv
        self.frame_csv_factory = frame_csv_factory
        self.event_log = event_log
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or datetime.now     # 可注入，便于测试
        self.on_light = None          # 亮光事件回调 (kind, elapsed)：融合 PLC 判定用
        self.on_record = None         # 亮光出记录回调 (record, verdict, start_el, end_el)

        self.running = False
        self._t0 = 0.0
        self.frames = 0
        self.elapsed = 0.0
        self.max_seconds = 0.0
        self.last_record: Optional[dict] = None
        self.last_image_path = ""
        self.curve_image_path = ""
        self.stop_reason = None       # None / "manual" / "max_seconds"（原版 analysis.stop_reason）

    # ---------------- 启停 ----------------
    def start(self) -> None:
        if self.running:            # 已经在跑：重复点「开始」不做任何事
            return
        self.stop_reason = None
        self.runner.reset()
        # base 必须跟 elapsed 的 0 点对齐：否则「停止→再开始」后所有墙钟时间
        # 会倒回服务启动时刻，合并器按时间窗配对就会错配（实测确认的 bug）
        if hasattr(self.runner, "base"):
            self.runner.base = self.wall_clock()
        if self.frame_csv_factory is not None:
            if self.frame_csv is not None:
                self.frame_csv.close()
            self.frame_csv, _ = self.frame_csv_factory()
        self._t0 = self.clock()
        self.frames = 0
        self.running = True

    def stop(self, reason: str = "manual") -> None:
        """停止分析：把未判完的曲线也存一张（partial），并把挂起的记录落盘。"""
        self.stop_reason = reason
        started = self.runner.reaction_start is not None
        ended = self.runner.reaction_end is not None
        if self.running and started and not ended and len(self.runner.curve.view()) >= 2:
            self._save_image(partial=True)
        self.running = False
        # 断点续接：还在合并窗口里的那条不能丢
        try:
            self.runner.flush_pending()
        except AttributeError:
            pass
        self._drain()

    # ---------------- 每帧 ----------------
    def flush_pending(self) -> None:
        """强制把还在合并窗口里的那条落盘（PLC 到位=炉边界，不能跨炉合并）。"""
        try:
            self.runner.flush_pending()
        except AttributeError:
            return
        self._drain()

    def on_frame(self, frame) -> None:
        if not self.running:
            return
        try:
            self._frame(frame)
        except Exception as exc:                      # 单帧出错只告警，绝不停机
            print(f"[warn] 分析异常：{exc}", file=sys.stderr)

    def _frame(self, frame) -> None:
        elapsed = self.clock() - self._t0
        self.elapsed = elapsed
        before = self.runner.count
        self.runner.process(frame, elapsed)
        metrics = self.probe.last

        self._log_frame(elapsed, metrics)

        if self.runner.count != before:
            self._event("START", elapsed, metrics)
            self._notify_light("light_start", elapsed)
        self._drain()

        self.frames += 1
        if self.max_seconds > 0 and elapsed >= self.max_seconds:
            self.stop("max_seconds")

    def _drain(self) -> None:
        """把 runner 里已就绪的记录写盘（可能是延迟落盘的那条）。"""
        drain = getattr(self.runner, "drain", None)
        if drain is None:
            return
        for record, end_elapsed, end_metrics in drain():
            self.last_record = record
            self._event("END", end_elapsed, end_metrics)
            self._notify_light("light_end", end_elapsed)
            self._notify_record(record, end_elapsed)
            name = self._save_uri(record.get("spheroidization_image", ""), partial=False)
            sink = getattr(self.runner, "sink", None)
            if sink is not None:
                if name and getattr(sink, "embed_image", True) is False:
                    record["spheroidization_image"] = name      # 只记文件名，图另有文件
                sink.write(record)

    # ---------------- 内部 ----------------
    def _log_frame(self, elapsed: float, metrics) -> None:
        if self.frame_csv is None or metrics is None:
            return
        machine = self.runner.machine
        self.frame_csv.write(
            frame_index=self.frames,
            metrics=metrics,
            state=machine.state.value,
            start_confirm=max(0.0, elapsed - machine.start_cond) if machine.start_cond else 0.0,
            end_confirm=max(0.0, elapsed - machine.end_cond) if machine.end_cond else 0.0,
            start_t=self.runner.reaction_start,
            end_t=self.runner.reaction_end,
            duration=(None if self.runner.reaction_start is None
                      else max(0.0, (self.runner.reaction_end or elapsed) - self.runner.reaction_start)),
        )

    def _notify_record(self, record: dict, end_elapsed: float) -> None:
        """把亮光的判定结果交给外部（五类合一用）。"""
        if self.on_record is None:
            return
        verdict = getattr(self.runner, "last_verdict", None)
        start_elapsed = (None if verdict is None
                         else max(0.0, end_elapsed - float(verdict.duration)))
        try:
            self.on_record(record, verdict, start_elapsed, end_elapsed)
        except Exception as exc:
            print(f"[warn] 记录回调异常：{exc}", file=sys.stderr)

    def _notify_light(self, kind: str, elapsed: float) -> None:
        """把亮光起止告诉外部（PLC 一炉状态机用它排除「未球化」误判）。"""
        if self.on_light is None:
            return
        try:
            self.on_light(kind, elapsed)
        except Exception as exc:
            print(f"[warn] 亮光回调异常：{exc}", file=sys.stderr)

    def _event(self, kind: str, elapsed: float, metrics) -> None:
        if self.event_log is None or metrics is None:
            return
        self.event_log.add(kind, self.frames, metrics,
                           max(0.0, (self.runner.reaction_end or elapsed) - self.runner.reaction_start)
                           if self.runner.reaction_start is not None else None,
                           wall(self.runner.base, elapsed))

    def _save_image(self, partial: bool) -> None:
        """停止分析时：把还没判完的曲线也存一张。"""
        points = self.runner.curve.view()
        if len(points) < 2 or self.image_store is None:
            return
        start_pixel = getattr(getattr(self, "probe", None), "start_pixel", None)
        uri = self.painter.draw(points, self.runner.reaction_start,
                                self.runner.reaction_end, start_pixel=start_pixel)
        self._save_uri(uri, partial=partial)

    def _save_uri(self, uri: str, partial: bool) -> str:
        """存图，返回文件名（没有图就返回空串）。"""
        if not uri or self.image_store is None:
            return ""
        name = self.image_store.save(uri, self.runner.count,
                                     wall(self.runner.base, self.runner.reaction_start or 0.0))
        if name:
            path = str(Path(self.image_store.directory) / name)
            self.curve_image_path = path
            self.last_image_path = path
        return name
