"""时间格式（纯函数）。"""

from datetime import datetime, timedelta


def hms(seconds: float) -> str:
    """秒 → HH:MM:SS.ss（与原版 actual_spheroidization_time 同格式）。"""
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:05.2f}"


def wall(base: datetime, elapsed: float) -> str:
    """基准时刻 + 偏移 → 墙钟字符串（毫秒）。"""
    return (base + timedelta(seconds=elapsed)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def num3(value) -> str:
    """数值 → 3 位小数字符串；None/非法 → 空串（CSV 与事件表统一口径）。"""
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def px_int(value) -> str:
    """像素阈值 → 0~255 整数串；None/非法 → 空串。"""
    if value is None:
        return ""
    try:
        return str(max(0, min(255, int(value))))
    except (TypeError, ValueError):
        return ""
