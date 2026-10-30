"""逐帧 CSV：每帧一行，11 列固定表头，写盘失败只 [warn] 不抛出。"""

import csv
import sys

from .formats import num3

# 列名与顺序对齐 vp10.8.3 config.CSV_HEADERS
HEADER = (
    "Frame", "Elapsed(s)", "Mean Gray",
    "Highlight Start (%)", "Highlight End (%)", "Reaction State",
    "Start Confirm(s)", "End Confirm(s)",
    "Reaction Start(s)", "Reaction End(s)", "Reaction Duration(s)",
)
FLUSH_EVERY = 200


class FrameCsv:
    """逐帧指标落盘。打开即写表头；close() 幂等；任何写盘异常只打 [warn]。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = None
        self._writer = None
        self._closed = False
        self._rows = 0
        try:
            self._fh = open(path, "w", newline="", encoding="utf-8-sig")   # 带 BOM，Excel 直开不乱码
            self._writer = csv.writer(self._fh)
            self._writer.writerow(HEADER)
            self._fh.flush()
        except Exception as exc:
            print(f"[warn] FrameCsv: 打不开 {path}: {exc}", file=sys.stderr)
            self._fh = None

    def write(self, frame_index: int, metrics, state: str,
              start_confirm: float, end_confirm: float,
              start_t, end_t, duration) -> None:
        if self._closed or self._writer is None:
            return
        try:
            self._writer.writerow((
                frame_index,
                num3(metrics.elapsed),
                num3(metrics.mean_gray),
                num3(metrics.start_pct),
                num3(metrics.end_pct),
                state,
                num3(start_confirm),
                num3(end_confirm),
                num3(start_t),
                num3(end_t),
                num3(duration),
            ))
            self._rows += 1
            if self._rows % FLUSH_EVERY == 0:
                self._fh.flush()
        except Exception as exc:
            print(f"[warn] FrameCsv: 写 {self.path} 失败: {exc}", file=sys.stderr)

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
            print(f"[warn] FrameCsv: 关 {self.path} 失败: {exc}", file=sys.stderr)
