"""判定区间：三级回退。只做「payload → Window」，不发请求。"""

from typing import Any, Optional, Tuple

from .models import Window


def _num(value: Any) -> Optional[float]:
    """后端可能给 null / 字符串 / 数字；转不了就当没给。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class StandardPolicy:
    """
    持有「后端当前标准」缓存，按三级取区间：
        ① 后端给了上下限 → [lower, upper]（写反了自动摆正，单边当没给）
        ② 只给标准值     → 标准值 ± tolerance
        ③ 都没有         → 本地回退区间
    update(payload, ok=False) 表示这次没取到 —— 缓存保持不动（网络抖动不该翻转判定）。
    """

    def __init__(
        self,
        fallback: Tuple[Optional[float], Optional[float]] = (None, None),
        tolerance: float = 5.0,
    ):
        self.fallback = (fallback[0], fallback[1])
        self.tolerance = float(tolerance)
        self.payload: Optional[dict] = None
        # ---- 取数统计（对齐原版 /api/status 的 standard.ok_count / fail_count / fresh）----
        self.ok_count = 0
        self.fail_count = 0
        self.last_error: Optional[str] = None
        self.last_ok_at: Optional[float] = None
        self.ttl = 0.0

    def update(self, payload: Optional[dict], ok: bool = True,
               now: Optional[float] = None, error: Optional[str] = None) -> None:
        if ok:
            self.payload = payload or None
            self.ok_count += 1
            self.last_ok_at = now
            self.last_error = None
        else:
            self.fail_count += 1
            self.last_error = error or "取不到后端标准"
            if now is not None:
                self.last_fail_at = now

    def fresh(self, now: Optional[float] = None) -> bool:
        """最近一次成功是否还在 TTL 内（原版 standard.fresh）。"""
        if self.last_ok_at is None or now is None or not self.ttl:
            return self.last_ok_at is not None
        return (now - self.last_ok_at) <= self.ttl

    def window(self) -> Window:
        data = self.payload or {}

        low = _num(data.get("standard_spheroidization_time_lower"))
        high = _num(data.get("standard_spheroidization_time_upper"))
        if low is not None and high is not None:
            if low > high:
                low, high = high, low
            return Window(low, high, "后端上下限")

        value = _num(data.get("standard_spheroidization_time"))
        if value is not None:
            return Window(value - self.tolerance, value + self.tolerance, "后端标准值±容差")

        low, high = self.fallback
        if low is None or high is None:
            return Window()
        low, high = float(low), float(high)
        if low > high:
            low, high = high, low
        return Window(low, high, "本地回退区间")
