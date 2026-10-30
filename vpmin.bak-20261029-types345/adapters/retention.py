"""磁盘保留策略：定期删掉超期的数据文件。

只做一件事：按修改时间删文件，不猜结构。
  * 覆盖目录：逐帧 CSV（frames/）、曲线图（image/）、按天轮转的 reactions-*.jsonl
  * 节流：默认每小时真正扫一次盘（maybe_run），避免每次落盘都遍历目录
  * 失败只告警：删不掉（权限/占用）不算致命，绝不能影响判定

为什么不用「按条数清空」：原版 reactions.jsonl 满 10 条清空，合并器只要慢一步就永久丢数
（10-26 的事故记录里就是这个坑）。按天保留既不会丢近期数据，也不会无限长。
"""

import os
import sys
import time
from pathlib import Path

DEFAULT_KEEP_DAYS = 30
DEFAULT_INTERVAL = 3600.0


class Retention:
    """targets: [(目录, glob 模式)] 或 [(目录, glob 模式, 保留天数)]。

    模式用来白名单，绝不误删 settings/counter 这类文件；
    逐帧 CSV 体积大（生产约 70MB/天）可以单独给更短的保留期。
    """

    def __init__(self, targets, keep_days: int = DEFAULT_KEEP_DAYS,
                 interval: float = DEFAULT_INTERVAL, clock=time.monotonic):
        self.targets = []
        for item in targets:
            directory, pattern = item[0], item[1]
            days = item[2] if len(item) > 2 and item[2] else keep_days
            self.targets.append((Path(directory), pattern, max(1.0, float(days)) * 86400.0))
        self.interval = float(interval)
        self.clock = clock
        self._next_at = 0.0

    def maybe_run(self, now: float = None) -> list:
        """到点才扫盘；返回本次删掉的文件名列表。"""
        now = self.clock() if now is None else now
        if now < self._next_at:
            return []
        self._next_at = now + self.interval
        return self.run()

    def run(self) -> list:
        now_ts = time.time()
        removed = []
        for directory, pattern, keep_seconds in self.targets:
            cutoff = now_ts - keep_seconds
            if not directory.is_dir():
                continue
            try:
                entries = list(directory.glob(pattern))
            except OSError:
                continue
            for path in entries:
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                    path.unlink()
                    removed.append(path.name)
                except OSError as exc:
                    print(f"[warn] Retention: 删不掉 {path}: {exc}", file=sys.stderr)
        if removed:
            print(f"[info] Retention: 清理 {len(removed)} 个超期文件", file=sys.stderr)
        return removed


def day_stamp(when=None) -> str:
    """YYYYMMDD（按天轮转用）。"""
    return time.strftime("%Y%m%d", time.localtime(when))
