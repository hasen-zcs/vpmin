# 极简检测模块 · 设计说明（先设计，后实施）

> 目标：**功能与 vp10.8.3 判定链路完全一致**，代码从 6267 行压到约 600 行，且按层次拆分、每个类只做一件事。
> 对齐基准：`core/reaction_detector.py`（状态机）、`core/engine.py::process/judge_duration/_freeze_verdict_locked`（像素口径/判定/冻结）、`core/biz_standard.py::window`（三级回退）。

## 1. 分层

```
┌ ui/cli ────────────────────────────── 参数解析、组装、打印
├ application (runner.py) ───────────── 只做编排：一帧进来，按顺序调用下面几层
├ domain（纯 Python，不 import cv2/numpy/urllib，可单测）
│    models.py        FrameMetrics / Transition / Window / Verdict   纯数据
│    trigger.py       Thresholds                                     阈值比较（唯一知道百分比阈值的地方）
│    state_machine.py ReactionStateMachine                           六态状态机（唯一知道状态迁移的地方）
│    standard.py      StandardPolicy                                 三级回退（唯一知道区间怎么来的地方）
│    judge.py         judge()                                        闭区间判定（唯一知道成功/失败的地方）
│    curve.py         CurveBuffer                                    曲线点集 + 5s 前置缓冲
└ adapters（每个外部依赖一个文件，可替换）
     roi.py       Roi（矩形/多边形几何，numpy mask）
     probe.py     RoiProbe（OpenCV 像素统计 → FrameMetrics）
     standard_http.py HttpStandardFeed（唯一发 HTTP 的地方，失败保留上次）
     painter.py   CurvePainter（cv2 画图 → base64 data URI）
     sink.py      JsonlSink（7 字段落盘）
     counter.py   CounterStore（reaction_count 持久化）
     source.py    FrameSource（视频/RTSP 取帧 + 节流）
     formats.py   hms() / wall()（时间格式，纯函数）
```

依赖方向单向：cli → runner → domain ← adapters。domain 不 import 任何 adapter；adapter 只实现 runner 需要的 3 个方法（measure/fetch/draw）。

## 2. 每帧的数据流

```
frame ──RoiProbe──► FrameMetrics(mean, start_pct, end_pct)
                       │
              Thresholds.start_ok/end_ok
                       │
             ReactionStateMachine.advance ──► Transition(started, ended)
                       │
         CurveBuffer.push（始终喂前置缓冲；started 时 begin）
                       │ ended
        StandardPolicy.window ──► Window(low, high, kind)
                       │
                 judge(duration, window) ──► Verdict(成功/失败/未判定)
                       │
   CurvePainter.draw + JsonlSink.write（7 字段，与原版同名同序）
```

## 3. 与原版逐条对齐的判定契约

| # | 原版行为 | 本实现 |
|---|---|---|
| 1 | `start_pct = 灰度>=start_pixel 占比%`，`end_pct = 灰度<=end_pixel 占比%` | `RoiProbe` 同一口径；多边形按顶点 mask 取样（不是外接框） |
| 2 | `WAITING→CAND_START→STARTED→CAND_END→ENDED→COOLDOWN` | 同名同序，`COOLDOWN` 必须 `not start_ok` 才回 WAITING（迟滞） |
| 3 | 确认成立取**该帧**时刻（非首次满足时刻） | `advance()` 内 `start_t/end_t = now` |
| 4 | 结束后 `rearm()` 立刻进 COOLDOWN，开始/结束时刻保留 | `machine.rearm()`，`start_t/end_t` 不清 |
| 5 | 结束即冻结判定，之后不重算 | 判定只在 `feed()` 返回 ended 的那一帧算一次 |
| 6 | 区间三级：后端上下限 → 标准值±容差 → 本地回退；读失败**不清缓存** | `StandardPolicy.update(payload, ok)`，`ok=False` 不动缓存 |
| 7 | 字段：reaction_count / detection_time / start / end / duration / abnormal / image | `JsonlSink.SCHEMA` 同名同序，duration 为 `HH:MM:SS.ss` |
| 8 | reaction_count 跨重启续号 | `CounterStore`（JSON 文件） |
| 9 | 灰度曲线带 5s 前置基线，逐帧采样 | `CurveBuffer(preroll_sec=5)` |

有意不做的（下游职责，不在检测模块内）：HTTP 服务/前端、逐帧 CSV、双机位、事件表、合并器与上传。

## 4. 验收（可测量）

1. **状态机逐帧等价**：随机/真实指标序列同时喂原版 `ReactionDetector` 与本实现，逐帧断言 state / started / ended / start_t / end_t 全等（`tests/test_parity_state_machine.py`）。
2. **端到端等价**：同一段测试视频、同一 ROI/参数，原版引擎与本实现产出的反应条数、起止时刻（±1 帧）、时长、判定完全一致（`tests/parity_e2e.py`）。
3. **接口契约自洽**：判定区间三级的 4 种输入组合各有断言（`tests/test_standard.py`）。
