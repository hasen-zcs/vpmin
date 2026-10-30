"""反应截图存储适配器（data URI → 文件，标准库实现）。"""

import base64
import binascii
import os
import re
from datetime import datetime

_NAME_RE = re.compile(
    r"^reaction_(\d{8}_\d{6})_(\d{2})_(\d{14})\.jpg$"
)


def _started_part(start_wall: str) -> str:
    """'2026-10-28 17:00:00.000' → '20261028170000'；解析不了返回 '0' 占位。"""
    if not isinstance(start_wall, str):
        return "0"
    text = start_wall.strip()
    # 去掉毫秒/小数部分
    text = re.sub(r"\.\d+$", "", text)
    text = text.replace("-", "").replace(":", "").replace(" ", "")
    if re.fullmatch(r"\d{14}", text):
        return text
    return "0"


class ImageStore:
    """reaction 截图的保存与列举。"""

    def __init__(self, directory: str, keep: int = 0) -> None:   # keep=0：不删旧图（与原版一致）
        self.directory = directory
        self.keep = int(keep)

    def save(self, data_uri: str, count: int, start_wall: str) -> str:
        try:
            if not isinstance(data_uri, str) or not data_uri.startswith("data:"):
                return ""
            header, sep, b64 = data_uri.partition(",")
            if not sep or "base64" not in header or "image/" not in header:
                return ""
            data = base64.b64decode(b64.encode("ascii"), validate=True)
            os.makedirs(self.directory, exist_ok=True)
            now = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 0
            name = f"reaction_{now}_{count:02d}_{_started_part(start_wall)}.jpg"
            path = os.path.join(self.directory, name)
            with open(path, "wb") as f:
                f.write(data)
            self._prune()
            return name
        except (binascii.Error, ValueError, OSError, UnicodeError):
            return ""

    def _prune(self) -> None:
        """按修改时间保留最近 keep 张，多余的删掉（失败忽略）。"""
        if self.keep <= 0:
            return
        entries = []
        try:
            for name in os.listdir(self.directory):
                if name.startswith("reaction_") and name.endswith(".jpg"):
                    path = os.path.join(self.directory, name)
                    try:
                        entries.append((os.path.getmtime(path), name))
                    except OSError:
                        pass
        except OSError:
            return
        entries.sort(reverse=True)
        for _, name in entries[self.keep:]:
            try:
                os.remove(os.path.join(self.directory, name))
            except OSError:
                pass

    def list(self, limit: int = 200) -> list:
        entries = []
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        for name in names:
            if not (name.startswith("reaction_") and name.endswith(".jpg")):
                continue
            path = os.path.join(self.directory, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            index, start_time = _parse_name(name)
            entries.append(
                {
                    "file": name,
                    "index": index,
                    "start_time": start_time,
                    "mtime": mtime,
                }
            )
        entries.sort(key=lambda item: item["mtime"], reverse=True)
        if limit is not None and limit > 0:
            entries = entries[:limit]
        return entries


def _parse_name(name: str):
    """从 reaction_YYYYmmdd_HHMMSS_{count}_{started}.jpg 解析 index 与 start_time。"""
    match = _NAME_RE.match(name)
    if not match:
        return 0, ""
    started = match.group(3)
    if len(started) == 14:
        start_time = (
            f"{started[0:4]}-{started[4:6]}-{started[6:8]} "
            f"{started[8:10]}:{started[10:12]}:{started[12:14]}"
        )
    else:
        start_time = ""
    try:
        index = int(match.group(2))
    except ValueError:
        index = 0
    return index, start_time
