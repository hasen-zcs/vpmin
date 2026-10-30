"""状态快照：把当前各层读数拼成 JSON。纯函数，不持有状态、不做 I/O。"""

from ..adapters.formats import hms
from ..domain.models import Window


def status_text(running: bool, started: bool, ended: bool, verdict) -> str:
    """与 vp10.8.3 同一套文案。"""
    if not started:
        return "等待反应" if running else "未开始"
    if not ended:
        return "进行中" if running else "未结束"
    if verdict is None or verdict.ok is None:
        return "判定中"
    return "成功" if verdict.ok else "失败"


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def reaction_block(take: dict) -> dict:
    data = take.get("standard_payload") or {}
    verdict = take["verdict"]
    window: Window = take["window"]
    started = take.get("start_t") is not None
    ended = take.get("end_t") is not None
    if not started:
        duration = None
    elif ended:
        duration = max(0.0, take["end_t"] - take["start_t"])
    else:
        duration = max(0.0, take.get("elapsed", 0.0) - take["start_t"])

    return {
        "reaction_count": take["count"],
        "start_time": take.get("start_wall"),
        "end_time": take.get("end_wall"),
        "duration_sec": None if duration is None else round(duration, 3),
        "duration_str": hms(duration) if duration is not None else None,
        "status": status_text(take["running"], started, ended, verdict),
        "success": None if verdict is None else verdict.ok,
        "success_text": "未判定" if verdict is None else verdict.text,
        "standard_min_sec": window.low,
        "standard_max_sec": window.high,
        "standard_range_str": ("" if window.low is None
                               else f"{hms(window.low)} ~ {hms(window.high)}"),
        "standard_source": "后端标准" if window.kind.startswith("后端") else "本地参数（回退）",
        "window_kind": window.kind,
        "standard_value": _num(data.get("standard_spheroidization_time")),
        "standard_lower": _num(data.get("standard_spheroidization_time_lower")),
        "standard_upper": _num(data.get("standard_spheroidization_time_upper")),
        "tolerance_sec": take.get("tolerance"),
        "curve_count": len(take["curve_points"]),
        "curve_image": take["curve_image"] or None,
        "curve_stats": take.get("curve_stats"),
        "curve_enabled": True,
    }


def build(take: dict) -> dict:
    return {
        "running": take["running"],
        "state": take["state"],
        "cameras": take["cameras"],
        "standard": {
            "enabled": take["standard_enabled"],
            "window_low": take["window"].low,
            "window_high": take["window"].high,
            "window_kind": take["window"].kind,
        },
        "reaction": reaction_block(take),
        "curve_images": take["images"],
        "analysis": {"csv_path": take.get("csv_path")},   # 记录器 --full 用 run 标识
        "counts": {"reactions": take["count"], "frames": take["frames"]},
        # ---- 排障扩展（原版没有，页面在用）----
        "standard_health": take.get("standard_health") or {},
        "roi_summary": take.get("roi_summary", ""),
        "stop_reason": take.get("stop_reason"),
    }


def curve_stats(points) -> dict:
    """曲线统计（记录器 --full 要用，也便于排查判定依据）。"""
    if not points:
        return {"count": 0, "gray_min": None, "gray_max": None,
                "gray_mean": None, "t_start": None, "t_end": None}
    grays = [g for _, g in points]
    return {
        "count": len(points),
        "gray_min": round(min(grays), 2),
        "gray_max": round(max(grays), 2),
        "gray_mean": round(sum(grays) / len(grays), 2),
        "t_start": round(points[0][0], 3),
        "t_end": round(points[-1][0], 3),
    }
