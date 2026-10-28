# 任务：写两个纯标准库适配器（逐帧 CSV、事件表）

只允许新建这两个文件 + 一个自测文件，**不许改 `vpmin/` 下任何已有文件**，不许提问、不许等确认。

## 环境
- 仓库根：/home/hasen/vp-lab/minimal
- 数据结构：`vpmin/domain/models.py` 的 `FrameMetrics(elapsed, mean_gray, start_pct, end_pct)`
- Python：/home/hasen/miniconda3/bin/python（只许标准库，不许 cv2/numpy）

## 文件一：vpmin/adapters/frame_csv.py

```python
class FrameCsv:
    def __init__(self, path: str) -> None
    def write(self, frame_index: int, metrics, state: str,
              start_confirm: float, end_confirm: float,
              start_t, end_t, duration) -> None
    def close(self) -> None
```

- 打开即写表头，列**固定 11 列、顺序不变**：
  `frame_index,elapsed_sec,mean_gray,start_percent,end_percent,state,start_confirm_sec,end_confirm_sec,start_elapsed,end_elapsed,duration_sec`
- 浮点统一 3 位小数；`start_t/end_t/duration` 为 None 时写空串
- 用 `csv.writer`；每 200 行 flush 一次；`close()` 幂等
- 任何写盘异常都不许抛出（打一行 `[warn]` 后继续）

## 文件二：vpmin/adapters/event_log.py

```python
class EventLog:
    def __init__(self, path: str, keep: int = 500) -> None
    def add(self, event_type: str, frame_index: int, metrics,
            duration: float, wall_time: str) -> dict
    def recent(self, limit: int = 100) -> list
    def close(self) -> None
```

- CSV 列固定：`event_type,frame_index,time_sec,wall_time,mean_gray,start_percent,end_percent,duration_sec`，浮点 3 位
- 内存只留最近 `keep` 条（默认 500），`recent(limit)` 返回**最新的在前**（倒序），每条是 dict（键同上，数值为 float）
- `add` 返回这条 dict；写盘异常只 `[warn]`，内存环形缓冲必须照常工作

## 文件三：tests/test_loggers.py

用 `tempfile` + 断言自测，至少覆盖：
1. 表头与列序（读回文件首行）
2. 一行数值与格式化（3 位小数、None→空串）
3. 行数 = 写入次数
4. `recent()` 倒序、limit 生效
5. `keep=10` 时写 25 条，内存只剩 10 条
6. `close()` 调两次不报错

跑通后把实际输出贴到本文件末尾的“自测结果”一节。

## 自测结果

命令：`/home/hasen/miniconda3/bin/python tests/test_loggers.py`（复跑两次，输出一致，exit=0）

```
============================================================
test_loggers 自测全部通过
  PASS frame_csv 表头11列且列序正确 cols=11
  PASS frame_csv 3位小数与None→空串 row=['7', '1.235', '128.987', '4.500', '', 'running', '1.000', '', '', '', '']
  PASS frame_csv 行数=写入次数 rows=250, file_lines=251
  PASS frame_csv close 两次不报错
  PASS event_log 表头8列且列序正确
  PASS event_log recent 倒序且limit生效 frame_index=[9, 8, 7]
  PASS event_log keep=10 写25条内存只剩10条(最新) frame_index=[24, 23, 22, 21, 20, 19, 18, 17, 16, 15]
  PASS event_log close 两次不报错
============================================================
```

断言逐条对应任务书要求：
1. 表头与列序：读回首行与固定 11 列逐字相等（event 表同法校验 8 列）
2. 数值与格式化：elapsed 1.23456→"1.235"、mean 128.987→"128.987"、start 4.5→"4.500"；end_pct/start_t/end_t/duration 为 None→空串
3. 行数=写入次数：写 250 行，文件 251 行（含表头），覆盖"每 200 行 flush"路径
4. recent() 倒序+limit：写 10 条，recent(limit=3) 返回 frame_index [9,8,7]，数值均为 float
5. keep=10 写 25 条：内存只剩 10 条，且为最新 10 条，倒序 [24..15]
6. close() 两次：FrameCsv / EventLog 各调两次均无异常
