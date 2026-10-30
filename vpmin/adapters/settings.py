"""设置持久化适配器（JSON 文件，原子写）。"""

import json
import os

#: 默认参数（值类型即权威类型，save 时按此转换）
DEFAULT_PARAMS = {
    "start_pixel": 240,
    "end_pixel": 240,
    "start_threshold": 3.0,
    "end_threshold": 3.0,
    "start_confirm": 1.0,
    "end_confirm": 3.0,
    "standard_min": 60.0,
    "standard_max": 120.0,
    "standard_tolerance": 5.0,
    "max_seconds": 0.0,
    "merge_gap": 0.0,
    "guard_window": 0.0,
    "guard_peak": 80.0,
    "min_duration": 0.0,
    "pause_threshold": 10.0,
    "median_frames": 5,
}


def _coerce(key, value):
    """把 value 转成默认值类型；转不了返回 None。"""
    target = type(DEFAULT_PARAMS[key])
    try:
        if target is int:
            # bool 不是 int 的合法来源
            if isinstance(value, bool):
                return None
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_params(raw):
    """从原始 dict 提取合法 params：补齐默认、忽略未知键、按类型转换。"""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, default in DEFAULT_PARAMS.items():
        if key in raw:
            coerced = _coerce(key, raw[key])
            if coerced is None:
                coerced = default
            out[key] = coerced
        else:
            out[key] = default
    if out["standard_min"] > out["standard_max"]:
        out["standard_min"], out["standard_max"] = out["standard_max"], out["standard_min"]
    return out


class SettingsStore:
    """ROI + 参数的 JSON 存取。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict:
        data = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        roi = data.get("roi")
        if not isinstance(roi, dict):
            roi = None
        return {"roi": roi, "params": _clean_params(data.get("params"))}

    def save(self, roi, params: dict) -> None:
        clean_roi = roi if isinstance(roi, dict) else None
        payload = {"roi": clean_roi, "params": _clean_params(params)}
        tmp = self.path + ".tmp"
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[warn] settings save failed: {exc}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
