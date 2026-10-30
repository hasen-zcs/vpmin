"""PLC 采集适配器：把 PLC 位信号变成事件流。

三种来源共用同一套边沿检测：
    PlcSource     真机（snap7，0.1s 轮询，断线重连）—— 现场用
    ReplaySource  回放（从 all_plc.jsonl 合成位时序）—— 无硬件也能测全链路
    ScriptSource  完全脚本化的位序列 —— 单元测试用

约定（老板 2026-10-29）：
  * 喂丝结束 位在物理停止后 24s 才置位 → 事件 ts 回推 24s，raw_ts 保留真实跳变时刻
  * 按钮 DB200.128.0 未接线，配置里留着，接线后把 enabled 打开即可
  * 重量不再只在结束时读：每拍都读，用于"铁水到位"识别
"""

import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:                                   # 真机才需要；本地无 snappy 也能跑回放/测试
    import snap7
except Exception:                      # pragma: no cover - 环境相关
    snap7 = None


# ============ 位地址表（与 Get_PLCInfo.py 一致） ============
# (事件名, DB号, 字节, 位, 时间补偿秒)
DEFAULT_POINTS: Tuple[Tuple[str, int, int, int, float], ...] = (
    ("button",     200, 128, 0, 0.0),
    ("feed_start", 200, 129, 2, 0.0),
    ("feed_end",   200, 129, 3, 24.0),
)

# 重量寄存器 (DB号, 起始字节, 长度, 类型)
DEFAULT_WEIGHT = (200, 8, 4, "REAL")


@dataclass
class PlcEvent:
    kind: str
    ts: float                       # 判定用时刻（feed_end 已回推）
    raw_ts: Optional[float] = None  # 位真正跳变时刻
    weight: Optional[float] = None

    def as_dict(self) -> dict:
        return {"event": self.kind, "ts": self.ts, "raw_ts": self.raw_ts,
                "weight": self.weight}


def _unpack(raw, dtype: str):
    if dtype == "REAL":
        return struct.unpack(">f", bytes(raw))[0]
    if dtype == "INT":
        return struct.unpack(">h", bytes(raw))[0]
    if dtype in ("UINT", "WORD"):
        return struct.unpack(">H", bytes(raw))[0]
    if dtype == "DINT":
        return struct.unpack(">i", bytes(raw))[0]
    if dtype in ("DWORD", "UDINT"):
        return struct.unpack(">I", bytes(raw))[0]
    raise ValueError("未知类型: %s" % dtype)


class EdgeDetector:
    """位电平 → 事件。与 IO 无关，真机和回放共用。"""

    def __init__(self, points=DEFAULT_POINTS, arrival_weight_min: float = 100.0,
                 arrival_weight_step: float = 5.0, arrival_dedupe_sec: float = 30.0,
                 weight_arrival: Optional[bool] = None):
        self.points = tuple(points)
        # 有位就别再用重量猜（否则同一次到位会出两条 arrival，把炉拆碎）
        self.weight_arrival = (not any(p[0] == "arrival" for p in self.points)
                               if weight_arrival is None else bool(weight_arrival))
        self.arrival_weight_min = float(arrival_weight_min)
        self.arrival_weight_step = float(arrival_weight_step)
        self.arrival_dedupe_sec = float(arrival_dedupe_sec)
        self.prev: Dict[str, int] = {}
        self.last_weight: Optional[float] = None
        self.last_arrival_ts: Optional[float] = None
        self.boot_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def reset(self) -> None:
        """断线重连后：只重记电平，不补发事件（避免重连制造假边沿）。"""
        self.prev.clear()

    def boot_event(self, now: float) -> PlcEvent:
        return PlcEvent("boot", now, now, self.last_weight)

    def feed(self, now: float, bits: Dict[str, int], weight: Optional[float]) -> List[PlcEvent]:
        """喂一拍，返回这一拍产生的事件（0~2 条）。"""
        out: List[PlcEvent] = []

        for name, _db, _byte, _bit, offset in self.points:
            cur = 1 if bits.get(name) else 0
            if name not in self.prev:
                self.prev[name] = cur                 # 首次只记电平
                continue
            if cur == 1 and self.prev[name] == 0:     # 0→1 上升沿
                ts = now - offset if offset else now
                out.append(PlcEvent(name, ts, now, weight))
                if name == "arrival":
                    self.last_arrival_ts = now
            self.prev[name] = cur

        # 铁水到位：有位走位；没位时才用重量启发式（15% 的相邻炉重量相同，会漏）
        # 🔴 2026-10-29 现场影子实测修正（一炉被切成好几段、进线时长出现负数）：
        #   ① 首次读数只当基线 —— 原来的 prev_w is None 即算 ripe，服务一启动就凭空
        #      来一条「到位」，停机时段 120s 后必误判类型1；
        #   ② 必须是「上涨」且涨幅 >= step 才算新铁水到位 —— 原来用 abs() 变化，
        #      喂丝时重量在 1792→1800→1840 抖动，每抖一次都被当成新炉到位。
        if weight is not None:
            w = float(weight)
            prev_w = self.last_weight
            self.last_weight = w
            if self.weight_arrival and prev_w is not None and w >= self.arrival_weight_min:
                rise = w - prev_w
                dup = (self.last_arrival_ts is not None
                       and now - self.last_arrival_ts < self.arrival_dedupe_sec)
                if rise >= self.arrival_weight_step and not dup:
                    out.append(PlcEvent("arrival", now, now, w))
                    self.last_arrival_ts = now

        return out


class PlcSource:
    """真机采集：后台线程 0.1s 轮询，断线自动重连。"""

    def __init__(self, on_event, ip: str = "192.0.2.1", rack: int = 0, slot: int = 1,
                 poll: float = 0.1, points=DEFAULT_POINTS, weight_spec=DEFAULT_WEIGHT,
                 clock=time.time, weight_arrival: Optional[bool] = None):
        # 用墙钟：要和亮光/落盘时间对齐
        if snap7 is None:
            raise RuntimeError("未安装 snap7，无法连真机（回放/测试不受影响）")
        self.ip, self.rack, self.slot = ip, rack, slot
        self.poll = float(poll)
        self.weight_spec = weight_spec
        self.on_event = on_event
        self.clock = clock
        self.detector = EdgeDetector(points, weight_arrival=weight_arrival)
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self.connected = False
        self.error: Optional[str] = None
        self.reads = 0
        self.events = 0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="plc-source")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._disconnect()

    def status(self) -> dict:
        return {"connected": self.connected, "error": self.error,
                "reads": self.reads, "events": self.events,
                "last_weight": self.detector.last_weight, "ip": self.ip}

    # ---------- 内部 ----------
    def _connect(self) -> None:
        c = snap7.client.Client()
        c.connect(self.ip, self.rack, self.slot)
        self._client = c
        self.connected = True
        self.error = None
        self.detector.reset()

    def _disconnect(self) -> None:
        self.connected = False
        try:
            if self._client is not None:
                self._client.disconnect()
        except Exception:
            pass
        self._client = None

    def _read_bits(self) -> Dict[str, int]:
        bits: Dict[str, int] = {}
        for name, db, byte, bit, _offset in self.detector.points:
            raw = self._client.db_read(db, byte, 1)
            bits[name] = (raw[0] >> bit) & 1
        return bits

    def _read_weight(self) -> Optional[float]:
        db, start, size, dtype = self.weight_spec
        try:
            return round(float(_unpack(self._client.db_read(db, start, size), dtype)), 3)
        except Exception:
            return None

    def _loop(self) -> None:
        while not self._stop:
            try:
                if self._client is None:
                    self._connect()
                    self._emit(self.detector.boot_event(self.clock()))

                bits = self._read_bits()
                weight = self._read_weight()
                self.reads += 1

                for ev in self.detector.feed(self.clock(), bits, weight):
                    self._emit(ev)

            except Exception as exc:            # 断线：退避重连，别刷日志
                self.error = str(exc)
                self.connected = False
                self._disconnect()
                time.sleep(2.0)
                continue

            time.sleep(self.poll)

    def _emit(self, ev: PlcEvent) -> None:
        self.events += 1
        try:
            self.on_event(ev)
        except Exception as exc:
            print("[plc] 事件回调异常: %s" % exc, file=sys.stderr)


# ============ 回放（无硬件也能跑全链路） ============
@dataclass
class ReplayHeat:
    """一条历史记录（一炉）→ 合成的位时序。"""

    start_ts: float
    end_ts: float
    weight: Optional[float] = None


class ReplaySource:
    """把「一炉一行」的历史数据合成位电平，再走真实 EdgeDetector 出新事件。

    合成规则（对应现场物理过程）：
      * feed_start 位：进线区间 [start, end) 内为 1
      * feed_end   位：物理结束 24s 后才置位（脉冲 1s）—— 这就是那 24s 补偿的来源
      * arrival    位：start 前 arrival_lead 秒置位（**真实 PLC 若没这个位就必须用重量判**）
      * weight     在到位后取本炉值
    默认加一个合成 arrival 位，用来验证「有位」和「靠重量」两种做法的差别。
    """

    def __init__(self, heats: List[ReplayHeat], on_event,
                 arrival_lead: float = 30.0, arrival_pulse: float = 5.0,
                 end_bit_delay: float = 24.0,
                 end_bit_pulse: float = 1.0, poll: float = 0.5,
                 with_arrival_bit: bool = True, points=DEFAULT_POINTS):
        self.heats = list(heats)
        self.on_event = on_event
        self.arrival_lead = float(arrival_lead)
        self.arrival_pulse = float(arrival_pulse)
        self.end_bit_delay = float(end_bit_delay)
        self.end_bit_pulse = float(end_bit_pulse)
        self.poll = float(poll)
        self.points = tuple(points) + (
            (("arrival", 200, 130, 0, 0.0),) if with_arrival_bit else ())
        self.stats = {"ticks": 0, "events": 0}

    def _bits_at(self, t: float):
        bits: Dict[str, int] = {}
        weight = None
        for h in self.heats:
            arr = h.start_ts - self.arrival_lead
            if h.start_ts <= t < h.end_ts:
                bits["feed_start"] = 1
            raw_end = h.end_ts + self.end_bit_delay
            if raw_end <= t < raw_end + self.end_bit_pulse:
                bits["feed_end"] = 1
            if arr <= t < arr + self.arrival_pulse:
                bits["arrival"] = 1
            if arr <= t:
                weight = h.weight          # 最近一炉的重量，一直持有到下一炉
        return bits, weight

    def run(self) -> List[PlcEvent]:
        if not self.heats:
            return []
        det = EdgeDetector(self.points)
        out: List[PlcEvent] = []
        t = self.heats[0].start_ts - self.arrival_lead - 5.0
        end = self.heats[-1].end_ts + self.end_bit_delay + self.end_bit_pulse + 5.0

        while t <= end:
            bits, weight = self._bits_at(t)
            for ev in det.feed(t, bits, weight):
                out.append(ev)
                self.on_event(ev)
            self.stats["ticks"] += 1
            t += self.poll
        self.stats["events"] = len(out)
        return out
