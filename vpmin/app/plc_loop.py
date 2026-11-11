"""同进程 PLC 采集回路（A 方案）：来源 → 边沿事件 → 一炉状态机 → 落盘/回调。

这一层就是「把采集融合进检测模块」的接线点：
    serve.py 里 plc = PlcLoop(PlcSource(...), HeatTracker(...), PlcEventLog(...))
    检测侧的亮光事件用 plc.light(kind, ts) 喂进来（用于排除"未球化"误判）

设计要点：
  * 采集在独立线程，检测主循环不受影响（PLC 断了只影响判定，不卡视频）
  * 事件全部 append-only 落盘，进程挂了重启能回补（判定不依赖内存）
  * 时间统一走 clock（默认 monotonic），事件落盘时同时写墙钟，便于事后对齐
"""

import threading
import time
from datetime import datetime
from typing import Callable, List, Optional

from ..adapters.plc import PlcEvent
from ..domain.heat import Heat, HeatTracker


def _wall(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class PlcEventLog:
    """append-only 事件文件（不滚动！滚动会让追尾读者丢同步，10-10 踩过）。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.count = 0

    def append(self, ev: PlcEvent) -> None:
        line = ('{"seq": %d, "event": "%s", "ts": "%s", "raw_ts": %s, '
                '"weight": %s}\n' % (
                    self.count + 1, ev.kind, _wall(ev.ts),
                    ('"%s"' % _wall(ev.raw_ts)) if ev.raw_ts is not None else "null",
                    ev.weight if ev.weight is not None else "null"))
        with self._lock:
            self.count += 1
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()


class PlcLoop:
    """采集 + 状态机 + 落盘，同进程跑。"""

    def __init__(self, source, tracker: HeatTracker, event_log: Optional[PlcEventLog] = None,
                 on_heat: Optional[Callable[[Heat], None]] = None,
                 on_tick: Optional[Callable[[], None]] = None,
                 tick_sec: float = 1.0, clock=time.monotonic):
        self.source = source
        self.tracker = tracker
        self.event_log = event_log
        self.on_heat = on_heat
        self.on_tick = on_tick
        self.on_arrival = None       # 到位事件回调：炉边界（用来切断亮光的合并窗口）
        self.on_button = None        # 按钮事件回调：同样是炉边界（按钮=新一炉开始）
        self.tick_sec = float(tick_sec)
        self.clock = clock
        self.heats: List[Heat] = []
        self._ticker: Optional[threading.Thread] = None
        self._stop = False
        self._lock = threading.Lock()

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        self._stop = False
        self.source.on_event = self.handle       # 来源把事件推到这里
        self.source.start()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True,
                                        name="plc-ticker")
        self._ticker.start()

    def stop(self) -> None:
        self._stop = True
        try:
            self.source.stop()
        except Exception:
            pass
        if self._ticker is not None:
            self._ticker.join(timeout=2)

    # ---------------- 事件入口 ----------------
    def handle(self, ev: PlcEvent) -> None:
        if ev.kind == "arrival" and self.on_arrival is not None:
            try:
                self.on_arrival()
            except Exception:
                pass
        if ev.kind == "button" and self.on_button is not None:
            try:
                self.on_button()
            except Exception:
                pass
        if self.event_log is not None:
            try:
                self.event_log.append(ev)
            except Exception:
                pass
        with self._lock:
            closed = self.tracker.on_event(ev.kind, ev.ts, ev.weight, ev.raw_ts)
        self._emit(closed)

    def light(self, kind: str, ts: float) -> None:
        """检测侧的亮光事件（kind 只用 light_start / light_end）。"""
        with self._lock:
            closed = self.tracker.on_event(kind, ts)
        self._emit(closed)

    def tick(self, now: Optional[float] = None) -> None:
        with self._lock:
            closed = self.tracker.tick(self.clock() if now is None else now)
        self._emit(closed)

    # ---------------- 内部 ----------------
    def _tick_loop(self) -> None:
        while not self._stop:
            try:
                self.tick()
                if self.on_tick is not None:
                    self.on_tick()
            except Exception:
                pass
            time.sleep(self.tick_sec)

    def _emit(self, closed: List[Heat]) -> None:
        for heat in closed:
            self.heats.append(heat)
            if self.on_heat is not None:
                try:
                    self.on_heat(heat)
                except Exception:
                    pass

    def status(self) -> dict:
        src = getattr(self.source, "status", None)
        heat = self.tracker.current
        return {
            "source": src() if callable(src) else {},
            "current_heat": None if heat is None else {
                "arrival_ts": heat.arrival_ts, "weight": heat.weight,
                "button_ts": heat.button_ts, "feed_start_ts": heat.feed_start_ts,
                "feed_end_ts": heat.feed_end_ts, "has_light": heat.has_light,
            },
            "closed_heats": len(self.heats),
        }
