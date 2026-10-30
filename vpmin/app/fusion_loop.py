"""把 PLC 一炉结果和亮光结果配成一条记录，落盘到**新文件**（既有文件一律不碰）。

流程：
    PlcLoop.on_heat  → fusion.add_heat(heat)
    亮光出记录时     → fusion.add_light(LightResult)
    定时/收尾        → fusion.settle()  → 写 heat_records.jsonl

配对规则：亮光起点落在 [炉起-容差, 炉止+宽限] 内就算这一炉的（一炉只配一次）。
宽限期 match_grace：喂丝结束后亮光还要 3~4 秒才结束，等它落定再出最终记录。
"""

import json
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..domain.fusion import LightResult, build_final, matches
from ..domain.heat import Heat


class FusionLog:
    def __init__(self, path: str, *, match_grace: float = 30.0, tolerance: float = 5.0,
                 no_light_type: int = 1, light_ttl: float = 900.0,
                 on_record: Optional[Callable[[dict], None]] = None,
                 clock=time.time):
        self.path = path
        self.match_grace = float(match_grace)
        self.tolerance = float(tolerance)
        self.no_light_type = int(no_light_type)
        self.light_ttl = float(light_ttl)
        self.on_record = on_record
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: List[Dict] = []      # {"heat": Heat, "ready": ts, "light": LightResult|None}
        self._lights: List[LightResult] = []  # 还没配上的亮光
        self.count = 0
        self.stats = {"heats": 0, "lights": 0, "written": 0, "unmatched_lights": 0}

    # ---------------- 入口 ----------------
    def add_heat(self, heat: Heat) -> None:
        with self._lock:
            start = heat.arrival_ts or heat.feed_start_ts or heat.button_ts
            end = heat.feed_end_ts or heat.light_end_ts or start or self.clock()
            item = {"heat": heat, "ready": end + self.match_grace, "light": None}
            self.stats["heats"] += 1
            # 先看看有没有已经到场、还没配上的亮光
            for lr in list(self._lights):
                if matches(heat, lr, self.tolerance, self.match_grace):
                    item["light"] = lr
                    self._lights.remove(lr)
                    break
            self._pending.append(item)

    def add_light(self, light: LightResult) -> None:
        with self._lock:
            self.stats["lights"] += 1
            for item in self._pending:
                if item["light"] is None and matches(item["heat"], light,
                                                     self.tolerance, self.match_grace):
                    item["light"] = light
                    return
            self._lights.append(light)
            self._expire_lights()

    # ---------------- 出记录 ----------------
    def settle(self, now: Optional[float] = None) -> List[dict]:
        now = self.clock() if now is None else now
        out: List[dict] = []
        with self._lock:
            keep: List[Dict] = []
            for item in self._pending:
                if now < item["ready"]:
                    keep.append(item)
                    continue
                out.append(self._emit(item))
            self._pending = keep
            self._expire_lights(now)
        return out

    def flush(self, now: Optional[float] = None) -> List[dict]:
        now = self.clock() if now is None else now
        with self._lock:
            out = [self._emit(item) for item in self._pending]
            self._pending = []
        return out

    # ---------------- 内部 ----------------
    def _emit(self, item: Dict) -> dict:
        self.count += 1
        rec = build_final(item["heat"], item["light"], reaction_count=self.count,
                          no_light_type=self.no_light_type).to_dict()
        line = json.dumps(rec, ensure_ascii=False)
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError:
            pass
        self.stats["written"] += 1
        if self.on_record is not None:
            try:
                self.on_record(rec)
            except Exception:
                pass
        return rec

    def _expire_lights(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        alive = [lr for lr in self._lights if now - lr.end_ts <= self.light_ttl]
        self.stats["unmatched_lights"] += len(self._lights) - len(alive)
        self._lights = alive

    def status(self) -> dict:
        return {"pending_heats": len(self._pending), "unmatched_lights": len(self._lights),
                **self.stats}
