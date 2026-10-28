"""后端当前标准：唯一发 HTTP 的地方。失败不清缓存（由 StandardPolicy 保证）。"""

import json
import urllib.request
from typing import Optional, Tuple


class HttpStandardFeed:
    """按 TTL 取 /api/standards/current，返回 (payload, ok)。"""

    def __init__(self, url: str, ttl: float = 30.0, timeout: float = 3.0):
        self.url = url
        self.ttl = float(ttl)
        self.timeout = float(timeout)
        self._next_at = 0.0

    def poll(self, now: float) -> Optional[Tuple[Optional[dict], bool]]:
        """未到刷新时刻返回 None；到点返回 (data, 是否取到)。"""
        if now < self._next_at:
            return None
        self._next_at = now + self.ttl
        return self._fetch()

    def force(self, now: float):
        """立刻重新读一次（手动刷新按钮）。"""
        self._next_at = 0.0
        return self.poll(now)

    def _fetch(self) -> Tuple[Optional[dict], bool]:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as resp:
                body = json.load(resp)
        except Exception:
            return None, False

        if not isinstance(body, dict) or body.get("code") != 200:
            return None, False
        return body.get("data"), True
