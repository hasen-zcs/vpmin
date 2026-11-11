"""落盘：reactions.jsonl（7 字段，字段名与顺序沿用业务契约）。

两个关键取舍：
  1. **固定文件名、按条追加，不轮转** —— 汇总程序按固定名读、并且记读取位点，
     文件名一变就得改配置或 glob 多文件，属于给下游找麻烦。
     体积靠第 2 条控制：每行约 340 字节，按 300 条/天算一年才 35MB，不需要轮转。
     原版"满 10 条清空"的做法更不能学 —— 合并器慢一步就永久丢数。
  2. **默认内嵌曲线图 base64** —— 管理系统/汇总程序就是从这一列直接拿图入库的，
     图必须随行带走（单行约 80KB）。想省体积可以关掉（embed_image=False，
     此时字段里只写 output/image/ 下的文件名），但下游要自己按文件名去取图。
     图片同时也会另存 output/image/ 一份，排查时不必解 JSON。
"""

import json
from pathlib import Path

from ..domain.models import Verdict
from .formats import hms

SCHEMA = (
    "reaction_count",
    "detection_time",
    "spheroidization_start_time",
    "spheroidization_end_time",
    "actual_spheroidization_time",
    "spheroidization_abnormal",
    "spheroidization_image",
)


class JsonlSink:
    def __init__(self, root_dir: str, embed_image: bool = True,
                 filename: str = "reactions.jsonl"):
        self.root = Path(root_dir)
        self.embed_image = bool(embed_image)
        self.filename = filename or "reactions.jsonl"

    @property
    def path(self) -> Path:
        # 传入的就是 .jsonl 文件路径（CLI 用法）时直接用，否则当目录拼固定文件名
        return self.root if self.root.suffix == ".jsonl" else self.root / self.filename

    def build(self, count: int, start_wall: str, end_wall: str,
              verdict: Verdict, image: str, extras: dict = None) -> dict:
        record = dict(zip(SCHEMA, (
            count, start_wall, start_wall, end_wall,
            hms(verdict.duration), verdict.text, image,
        )))
        # 追加字段（abnormal_type / abnormal_reason / 分段明细）只往后加，不改老 7 列
        for key, value in (extras or {}).items():
            if key not in record:
                record[key] = value
        return record

    def write(self, record: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
