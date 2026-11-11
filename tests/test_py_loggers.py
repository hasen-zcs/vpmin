"""pytest 化包装（T-007b 第二批）：tests/test_loggers.py 的 FrameCsv / EventLog 落盘用例。

原脚本保留不动，本文件复用其 metrics() 辅助，逐条改写为 pytest 断言
（口径与原脚本完全一致：BOM、列序、3 位小数、None→空串、recent 正序、keep 截断、双 close）。
"""
import csv
import os

import test_loggers as orig  # noqa: F401  (同时完成 sys.path 初始化)

from vpmin.adapters.event_log import EventLog, HEADER as EVENT_HEADER
from vpmin.adapters.frame_csv import FrameCsv, HEADER as FRAME_HEADER


def test_frame_csv_has_utf8_bom(tmp_path):
    fpath = str(tmp_path / "frames.csv")
    fc = FrameCsv(fpath)
    fc.close()
    with open(fpath, "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf", "逐帧 CSV 应带 UTF-8 BOM（对齐原版 utf-8-sig）"


def test_frame_csv_header_11_cols(tmp_path):
    fpath = str(tmp_path / "frames.csv")
    fc = FrameCsv(fpath)
    fc.close()
    with open(fpath, encoding="utf-8-sig", newline="") as fh:
        first_line = fh.readline().strip()
    assert first_line == "Frame,Elapsed(s),Mean Gray,Highlight Start (%)," \
                         "Highlight End (%),Reaction State,Start Confirm(s)," \
                         "End Confirm(s),Reaction Start(s),Reaction End(s)," \
                         "Reaction Duration(s)", f"表头不符: {first_line}"
    assert len(FRAME_HEADER) == 11


def test_frame_csv_row_format_3dec_none_empty(tmp_path):
    fpath = str(tmp_path / "frames.csv")
    fc = FrameCsv(fpath)
    m = orig.metrics(elapsed=1.23456, mean_gray=128.987, start_pct=4.5, end_pct=None)
    fc.write(7, m, "running", 1.0, None, None, None, None)
    fc.close()
    with open(fpath, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2, f"应为表头+1行, 实际 {len(rows)}"
    assert rows[1] == ["7", "1.235", "128.987", "4.500", "", "running",
                       "1.000", "", "", "", ""], f"行内容不符: {rows[1]}"


def test_frame_csv_row_count_250(tmp_path):
    fpath = str(tmp_path / "frames2.csv")
    fc = FrameCsv(fpath)
    for i in range(250):
        fc.write(i, orig.metrics(i * 0.1, 10.0 + i, 1.0, 2.0), "idle",
                 0.0, 0.0, None, None, None)
    fc.close()
    with open(fpath, encoding="utf-8", newline="") as fh:
        n_rows = sum(1 for _ in fh)
    assert n_rows == 251, f"应为 表头+250=251 行, 实际 {n_rows}"


def test_frame_csv_close_twice(tmp_path):
    fpath = str(tmp_path / "frames.csv")
    fc = FrameCsv(fpath)
    fc.close()
    fc.close()  # 再关一次不报错


def test_event_log_header_16_cols(tmp_path):
    epath = str(tmp_path / "events.csv")
    el = EventLog(epath, keep=500)
    el.add("start", 3, orig.metrics(1.1115, 90.5, 5.0, None),
           duration=0.0, wall_time="12:00:01")
    el.close()
    with open(epath, encoding="utf-8-sig", newline="") as fh:
        efirst = fh.readline().strip()
    assert efirst == "运行ID,视频,开始像素下限,结束像素上限," \
                     "开始高亮阈值(%),结束阈值(%),开始确认(s),结束确认(s)," \
                     "事件类型,帧号,时间(s),实时时间,平均灰度," \
                     "开始占比(%),结束占比(%),反应时长(s)", f"事件表头不符: {efirst}"
    assert len(EVENT_HEADER) == 16


def test_event_log_recent_last_n_ascending(tmp_path):
    epath = str(tmp_path / "events2.csv")
    el = EventLog(epath)
    for i in range(10):
        el.add("start", i, orig.metrics(i, 50.0, 1.0, 1.0), 0.0, f"w{i:02d}")
    recent = el.recent(limit=3)
    assert len(recent) == 3
    assert [r["frame_index"] for r in recent] == [7, 8, 9], \
        f"recent 应返回最后 3 条且按时间正序: {[r['frame_index'] for r in recent]}"
    assert all(isinstance(r["time_sec"], float) for r in recent)


def test_event_log_keep_truncates_to_latest(tmp_path):
    epath = str(tmp_path / "events3.csv")
    el = EventLog(epath, keep=10)
    for i in range(25):
        el.add("start", i, orig.metrics(i, 1.0, 1.0, 1.0), 0.0, f"w{i:02d}")
    buf = el.recent(limit=100)
    assert len(buf) == 10, f"keep=10 写25条后应剩10条, 实际 {len(buf)}"
    assert [r["frame_index"] for r in buf] == list(range(15, 25)), \
        f"应剩最新10条且正序: {[r['frame_index'] for r in buf]}"


def test_event_log_add_returns_dict_and_close_twice(tmp_path):
    epath = str(tmp_path / "events.csv")
    el = EventLog(epath, keep=500)
    rec0 = el.add("start", 3, orig.metrics(1.1115, 90.5, 5.0, None),
                  duration=0.0, wall_time="12:00:01")
    assert isinstance(rec0, dict) and rec0["event_type"] == "start"
    assert rec0["time_sec"] == 1.1115 and isinstance(rec0["time_sec"], float)
    el.close()
    el.close()  # 再关一次不报错
