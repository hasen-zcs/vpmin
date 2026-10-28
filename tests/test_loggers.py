"""自测：FrameCsv 与 EventLog（纯标准库，tempfile 落盘 + 断言）。

用法：
    python3 tests/test_loggers.py
"""

import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.adapters.frame_csv import FrameCsv, HEADER as FRAME_HEADER
from vpmin.adapters.event_log import EventLog, HEADER as EVENT_HEADER
from vpmin.domain.models import FrameMetrics


def metrics(elapsed, mean_gray, start_pct, end_pct):
    return FrameMetrics(elapsed=elapsed, mean_gray=mean_gray,
                        start_pct=start_pct, end_pct=end_pct)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="vpmin_loggers_")
    results = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        assert cond, f"断言失败: {name} {detail}"
        results.append(f"PASS {name} {detail}".rstrip())

    # ---------- FrameCsv ----------
    fpath = os.path.join(tmp, "frames.csv")
    fc = FrameCsv(fpath)

    # 1. 表头与列序（读回文件首行）
    with open(fpath, "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf", "逐帧 CSV 应带 UTF-8 BOM（对齐原版 utf-8-sig）"
    with open(fpath, encoding="utf-8-sig", newline="") as fh:
        first_line = fh.readline().strip()
    # 表头对齐参考实现 config.CSV_HEADERS（英文 11 列）
    assert first_line == "Frame,Elapsed(s),Mean Gray,Highlight Start (%)," \
                         "Highlight End (%),Reaction State,Start Confirm(s)," \
                         "End Confirm(s),Reaction Start(s),Reaction End(s)," \
                         "Reaction Duration(s)", \
        f"表头不符: {first_line}"
    assert len(FRAME_HEADER) == 11
    check("frame_csv 表头11列且列序正确", True, f"cols={len(FRAME_HEADER)}")

    # 2. 一行数值与格式化（3 位小数、None→空串）
    m = metrics(elapsed=1.23456, mean_gray=128.987, start_pct=4.5, end_pct=None)
    fc.write(7, m, "running", 1.0, None, None, None, None)
    fc.close()
    with open(fpath, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2, f"应为表头+1行, 实际 {len(rows)}"
    row = rows[1]
    assert row == ["7", "1.235", "128.987", "4.500", "", "running",
                   "1.000", "", "", "", ""], f"行内容不符: {row}"
    check("frame_csv 3位小数与None→空串", True,
          f"row={row}")

    # 3. 行数 = 写入次数（250 行，覆盖每 200 行 flush）
    fpath2 = os.path.join(tmp, "frames2.csv")
    fc2 = FrameCsv(fpath2)
    for i in range(250):
        fc2.write(i, metrics(i * 0.1, 10.0 + i, 1.0, 2.0), "idle",
                  0.0, 0.0, None, None, None)
    fc2.close()
    with open(fpath2, encoding="utf-8", newline="") as fh:
        n_rows = sum(1 for _ in fh)
    assert n_rows == 251, f"应为 表头+250=251 行, 实际 {n_rows}"
    check("frame_csv 行数=写入次数", True, f"rows={n_rows - 1}, file_lines={n_rows}")

    # 4/6. close() 调两次不报错（fc 已关，再关一次）
    fc.close()
    check("frame_csv close 两次不报错", True)

    # ---------- EventLog ----------
    epath = os.path.join(tmp, "events.csv")
    el = EventLog(epath, keep=500)
    rec0 = el.add("start", 3, metrics(1.1115, 90.5, 5.0, None),
                  duration=0.0, wall_time="12:00:01")
    assert isinstance(rec0, dict) and rec0["event_type"] == "start"
    assert rec0["time_sec"] == 1.1115 and isinstance(rec0["time_sec"], float)
    el.add("end", 9, metrics(3.3335, 91.0, 5.0, 8.0),
           duration=2.222, wall_time="12:00:03")
    el.close()

    # 事件表头：17 列（元信息 9 + 事件 8）；原版恒为 0 的「速度阈值」列已删,
    # 并新增「实时时间」列（原版只有 elapsed，对不上墙钟）
    with open(epath, encoding="utf-8-sig", newline="") as fh:
        efirst = fh.readline().strip()
    assert efirst == "运行ID,视频,开始像素下限,结束像素上限," \
                     "开始高亮阈值(%),结束阈值(%),开始确认(s),结束确认(s)," \
                     "事件类型,帧号,时间(s),实时时间,平均灰度," \
                     "开始占比(%),结束占比(%),反应时长(s)", \
        f"事件表头不符: {efirst}"
    assert len(EVENT_HEADER) == 16, f"事件表应为 16 列, 实际 {len(EVENT_HEADER)}"
    check("event_log 表头16列且列序正确", True, f"cols={len(EVENT_HEADER)}")

    # 4. recent() 倒序、limit 生效（新事件在前）
    epath2 = os.path.join(tmp, "events2.csv")
    el2 = EventLog(epath2)
    for i in range(10):
        el2.add("start", i, metrics(i, 50.0, 1.0, 1.0), 0.0, f"w{i:02d}")
    recent = el2.recent(limit=3)
    assert len(recent) == 3
    assert [r["frame_index"] for r in recent] == [7, 8, 9], \
        f"recent 应返回最后 3 条且按时间正序（与 engine.events 一致）: {[r['frame_index'] for r in recent]}"
    assert all(isinstance(r["time_sec"], float) for r in recent)
    check("event_log recent 取最后limit条且正序", True,
          f"frame_index={[r['frame_index'] for r in recent]}")

    # 5. keep=10 时写 25 条，内存只剩 10 条（且是最新的 10 条）
    epath3 = os.path.join(tmp, "events3.csv")
    el3 = EventLog(epath3, keep=10)
    for i in range(25):
        el3.add("start", i, metrics(i, 1.0, 1.0, 1.0), 0.0, f"w{i:02d}")
    buf = el3.recent(limit=100)
    assert len(buf) == 10, f"keep=10 写25条后应剩10条, 实际 {len(buf)}"
    assert [r["frame_index"] for r in buf] == list(range(15, 25)), \
        f"应剩最新10条且正序: {[r['frame_index'] for r in buf]}"
    el3.close()
    el3.close()  # 再关一次
    check("event_log keep=10 写25条内存只剩10条(最新)", True,
          f"frame_index={[r['frame_index'] for r in buf]}")

    # 6. close() 调两次不报错
    el.close()
    check("event_log close 两次不报错", True)

    print("=" * 60)
    print("test_loggers 自测全部通过")
    for line in results:
        print(" ", line)
    print("=" * 60)


if __name__ == "__main__":
    main()
