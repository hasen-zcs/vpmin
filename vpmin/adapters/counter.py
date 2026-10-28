"""反应序号持久化：跨「停止/开始」与进程重启续号（10.8.2 行为）。"""

import json
import os
import time


class CounterStore:
    def __init__(self, path: str = "", start: int = 0):
        self.path = path
        self.value = int(start)
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.value = int(json.load(fh).get("reaction_count", self.value))
        except Exception:
            pass    # 读不到就当 0，绝不让分析起不来

    def next(self) -> int:
        self.value += 1
        self._save()
        return self.value

    def _save(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"reaction_count": self.value,
                           "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, fh)
        except Exception:
            pass    # 写不了也不影响判定
