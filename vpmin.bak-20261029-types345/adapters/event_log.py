"""事件表：磁盘 CSV（原版 19 列中文表头）+ 内存环形缓冲（只留最近 keep 条）。

列结构对齐 vp10.8.3 core/event_log.py：
  前 10 列是本轮运行的元信息（每行重复，方便筛选）：
      运行ID / 视频 / 判断口径 / 开始像素下限 / 结束像素上限 /
      速度阈值(灰度/秒) / 开始高亮阈值(%) / 结束阈值(%) / 开始确认(s) / 结束确认(s)
  后 8 列是事件本身：事件类型 / 帧号 / 时间(s) / 平均灰度 / 亮度变化速度 /
      开始占比(%) / 结束占比(%) / 反应时长(s)
其中「亮度变化速度」原版恒为 0.0（占位），这里保持一致。
"""

import csv
import os
import sys
from collections import deque

from .formats import num3, px_int

# 列结构说明（本版口径，不再照抄原版）：
#   前 8 列 = 本轮运行的元信息，每行重复，便于在 Excel 里筛选
#   相对原版删了两列恒值列：「判断口径」(只有 highlight 一种口径)、
#   「速度阈值(灰度/秒)」(本模块没有速度判据，恒为 0) —— 都是原版历史包袱
#   后 8 列 = 事件本身；其中「实时时间」是原版缺的一列：原版只记 elapsed，
#     出了纠纷无法对到墙钟时间，本版补上。
HEADER = (
    "运行ID", "视频", "开始像素下限", "结束像素上限",
    "开始高亮阈值(%)", "结束阈值(%)", "开始确认(s)", "结束确认(s)",
    "事件类型", "帧号", "时间(s)", "实时时间", "平均灰度",
    "开始占比(%)", "结束占比(%)", "反应时长(s)",
)


class EventLog:
    """start/end 等事件落盘 + 内存缓冲。写盘失败只 [warn]，环形缓冲照常工作。"""

    def __init__(self, path: str, keep: int = 500, meta=None) -> None:
        self.path = path
        self._buffer = deque(maxlen=keep)
        self._fh = None
        self._writer = None
        self._closed = False
        meta = dict(meta or {})
        self._run_id = str(meta.get("run_id") or "")
        self._video = str(meta.get("video") or "")
        self._params = dict(meta.get("params") or {})
        try:
            fresh = not os.path.exists(path) or os.path.getsize(path) == 0
            self._fh = open(path, "a", newline="", encoding="utf-8-sig")   # 追加：重启/重开都保留历史
            self._writer = csv.writer(self._fh)
            if fresh:
                self._writer.writerow(HEADER)
            self._fh.flush()
        except Exception as exc:
            print(f"[warn] EventLog: 打不开 {path}: {exc}", file=sys.stderr)
            self._fh = None

    def set_params(self, params) -> None:
        """参数热更后，后续行的元信息列用新值（已有行不动）。"""
        if isinstance(params, dict):
            self._params.update(params)

    def _meta_row(self) -> list:
        p = self._params
        return [
            self._run_id,
            self._video,
            px_int(p.get("start_pixel")),
            px_int(p.get("end_pixel")),
            num3(p.get("start_threshold")),
            num3(p.get("end_threshold")),
            num3(p.get("start_confirm")),
            num3(p.get("end_confirm")),
        ]

    def add(self, event_type: str, frame_index: int, metrics,
            duration: float, wall_time: str) -> dict:
        def _num_or_none(value):
            return float(value) if value is not None else None

        record = {
            "event_type": event_type,
            "frame_index": frame_index,
            "time_sec": _num_or_none(metrics.elapsed),
            "wall_time": wall_time,
            "mean_gray": _num_or_none(metrics.mean_gray),
            "start_percent": _num_or_none(metrics.start_pct),
            "end_percent": _num_or_none(metrics.end_pct),
            "duration_sec": _num_or_none(duration),
        }
        self._buffer.append(record)  # 内存环形缓冲必须照常工作，先于写盘
        if self._closed or self._writer is None:
            return record
        try:
            self._writer.writerow(self._meta_row() + [
                str(event_type),
                str(int(frame_index)),
                num3(record["time_sec"]),
                wall_time or "",                 # 实时时间（本版新增，原版只有 elapsed）
                num3(record["mean_gray"]),
                num3(record["start_percent"]),
                num3(record["end_percent"]),
                num3(record["duration_sec"]),
            ])
            self._fh.flush()
        except Exception as exc:
            print(f"[warn] EventLog: 写 {self.path} 失败: {exc}", file=sys.stderr)
        return record

    def recent(self, limit: int = 100) -> list:
        items = list(self._buffer)
        return items[-limit:] if limit and limit > 0 else items

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._fh is None:
            return
        try:
            self._fh.flush()
            self._fh.close()
        except Exception as exc:
            print(f"[warn] EventLog: 关 {self.path} 失败: {exc}", file=sys.stderr)
