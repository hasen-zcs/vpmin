"""ROI 历史：记住用过的 ROI，下次直接选。

只做「存取与去重」，不认识相机、不碰判定。文件是 JSON 列表，最近 50 条。
"""

import hashlib
import json
import os
import time
from typing import Optional


def _shape_text(roi: dict) -> str:
    if roi.get("kind") == "polygon":
        return f"多边形 {len(roi.get('points') or [])} 点"
    rect = roi.get("rect") or roi.get("bbox") or [0, 0, 0, 0]
    return f"矩形 {int(rect[2])}×{int(rect[3])}"


def _key(roi: dict) -> str:
    """同一块区域的指纹（用于去重）。"""
    if roi.get("kind") == "polygon":
        body = json.dumps([[int(x), int(y)] for x, y in roi.get("points") or []])
    else:
        body = json.dumps([int(v) for v in (roi.get("rect") or roi.get("bbox") or [])])
    return hashlib.sha1(f"{roi.get('kind')}:{body}".encode()).hexdigest()[:10]


class RoiHistory:
    def __init__(self, path: str, keep: int = 50):
        self.path = path
        self.keep = int(keep)
        self.items = self._load()

    # ---------------- 读 ----------------
    def _load(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return [item for item in data if isinstance(item, dict)][: self.keep]
        except (OSError, ValueError):
            return []

    def list(self) -> list:
        return list(self.items)

    def get(self, roi_id: str) -> Optional[dict]:
        for item in self.items:
            if item.get("id") == roi_id:
                return item
        return None

    # ---------------- 写 ----------------
    def add(self, roi: dict) -> dict:
        """记一条；同一块区域已存在就直接复用（不重复堆）。"""
        key = _key(roi)
        for index, item in enumerate(self.items):
            if item.get("key") == key:
                if index:                       # 用过的重新提到最前，下拉默认就是最近用的
                    self.items.insert(0, self.items.pop(index))
                    self._save()
                return item

        entry = {
            "id": f"r{int(time.time() * 1000)}-{key[:6]}",   # 毫秒 + 几何指纹，保证同一毫秒内也不撞
            "key": key,
            "kind": roi.get("kind", "rect"),
            "shape": _shape_text(roi),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if entry["kind"] == "polygon":
            entry["points"] = [[int(x), int(y)] for x, y in roi.get("points") or []]
        else:
            entry["rect"] = [int(v) for v in (roi.get("rect") or roi.get("bbox") or [])]

        self.items.insert(0, entry)
        del self.items[self.keep:]
        self._save()
        return entry

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.items, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass        # 记不上历史不影响当前 ROI 生效
