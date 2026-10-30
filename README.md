# vpmin · 球化亮光检测 + PLC 融合判定（极简分层实现）

**判定内核行为对齐 vp10.8.3**（已 12 万帧对拍 + 端到端 3/3），并补齐生产必需的周边：HTTP 服务、双机位、参数/ROI 热更新、逐帧 CSV、事件表、曲线存档、断流重连、守护单元。

2026-10 起新增 **PLC 融合采集**：接入喂丝开始/结束/按钮/重量位，与亮光判定合成为**五类异常**（进站未球化 / 进线中断 / 超标准 / 低于标准 / 中间停顿），一炉一条记录。

- 判定内核（domain + runner + probe）：1243 行，无 Web 时也能当 CLI 跑
- PLC 融合层（采集 + 一炉状态机 + 五类合一）：1017 行
- 含生产服务：约 3800 行（Python）+ 180 行前端（原生 JS，无构建）
- 依赖：Python 3 + OpenCV + numpy（与原版一致），**Web 服务零额外依赖**（标准库 http.server）
- PLC 采集需 `snap7`（仅真机模式需要，回放/单测不需要）

> 📘 深入文档：[**核心检测模块详解**](docs/核心检测模块.md) · [**PLC 模块详解**](docs/PLC模块.md)

## 1. 分层

```
domain/                 纯逻辑，不 import cv2/numpy/urllib/snap7（可离线单测）
  models.py       5 个数据结构：帧指标 / 迁移结果 / 区间 / 分段 / 判定
  trigger.py      只做百分比阈值比较
  state_machine.py 六态迁移 + rearm（COOLDOWN 迟滞）
  standard.py     三级区间回退（后端上下限 → ±容差 → 本地；取失败不清缓存）
  judge.py        闭区间判定 + 分段停顿判定（类型 3/4/5）
  filter.py       因果中值滤波（压阈值附近高频抖动）
  curve.py        曲线点集（含 5s 前置基线；断点续接 resume()）
  heat.py         一炉生命周期状态机（类型 1/2）+ 锚点/去抖/超时
  fusion.py       五类合一：优先级 2>1>5>3>4，次级异常拼进 reason

adapters/               一个外部依赖一个文件
  probe.py        像素统计（矩形/多边形同口径）
  roi.py          ROI 几何与外接框
  source.py       离线取帧（CLI 用；frame/wall 两种时钟）
  camera.py       生产取帧：断流重连 + 最新帧/JPEG（分析机位才回调）
  plc.py          PLC 采集：snap7 轮询 / 回放 / 脚本三种来源共用边沿检测
  standard_http.py 唯一发 HTTP 的地方（TTL 30s，force 手动刷新）
  painter.py      曲线图 → JPEG data URI
  sink.py         reactions.jsonl（7 字段同名同序 + 追加异常字段）
  counter.py      reaction_count 持久化
  settings.py     settings.json 读写（原子写）
  roi_store.py    ROI 历史（最近 50 条，按几何去重）
  frame_csv.py    逐帧 CSV（11 列）
  event_log.py    START/END 事件（CSV + 内存最近 500）
  image_store.py  曲线图存档 + 最近 200 张清理
  formats.py      hms() / wall()

app/                   编排（不碰像素、不发 HTTP）
  analysis.py     单机位分析循环：计时 → 判定 → 日志/存档
  plc_loop.py     PLC 同进程回路：采集线程 + 每秒 tick + 事件落盘
  fusion_loop.py  炉×亮光配对 → heat_records.jsonl
  status.py       状态快照（纯函数，文案与 10.8.3 一致）
  runtime.py      生命周期：启停、热更新、给 HTTP 层的方法

server/http_api.py     路由分发（标准库 ThreadingHTTPServer）
server/static/         单页前端（MJPEG + 状态 + 实时曲线 + 参数表单）
runner.py              离线/在线共用：一帧进 → 判定 → 落盘
cli.py                 离线 CLI（不进 Web，不读 RTSP 也行）
serve.py               生产入口：装配
```

依赖方向：`serve/cli → app → (domain, adapters)`；domain 不认识任何 I/O。

## 2. 生产启动

```bash
# 仅亮光检测
SOURCE="rtsp://admin:***@192.0.2.10:554/Streaming/Channels/102" \
SOURCE2="rtsp://admin:***@192.0.2.11:554/Streaming/Channels/102" \
BIZ_URL="http://127.0.0.1:8000/api/standards/current" \
PORT=5080 ./run.sh

# 亮光 + PLC 融合（五类判定）
SOURCE="rtsp://admin:***@192.0.2.10:554/Streaming/Channels/102" \
PLC_IP="192.0.2.1" PLC_POLL=0.1 \
BIZ_URL="http://127.0.0.1:8000/api/standards/current" \
PORT=5080 ./run.sh
```

- 打开 `http://<服务器IP>:5080/`
- 开机自启：`sudo cp deploy/vpmin.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vpmin`
- 输出：`output/reactions.jsonl`（7 字段）、`output/frames/web_*.csv`、`output/data_values/reaction_events.csv`、`output/image/*.jpg`、`output/reaction_counter.json`、`output/settings.json`
- PLC 融合额外输出：`output/plc/plc_events.jsonl`（事件流）、`output/plc/heat_records.jsonl`（一炉一条业务记录）

接口（与 10.8.3 对齐的部分同名）：

```
GET  /api/status            状态（reaction/cameras/standard/curve_images）
GET  /api/curve?since=N     实时曲线增量（带 reaction 序号，换反应自动重置）
GET  /api/curve/images      曲线图清单
GET  /images/<file>         曲线图
GET  /api/events?limit=N    START/END 事件
GET  /api/settings          ROI + 参数
GET  /api/rois              ROI 历史
POST /api/rois/apply        套用历史 ROI
GET  /api/snapshot.jpg?cam=N
GET  /api/video.mjpg?cam=N  MJPEG（/video_feed1、/video_feed2 别名）
POST /api/start | /api/stop
POST /api/params            参数热更（下一帧生效 + 落盘）
POST /api/roi               ROI 热更（rect / polygon）
POST /api/standard/refresh  立刻重读后端标准
```

`record_reactions.py` 可直接对接（已验证：只写判完的反应、图像嵌入正常）。

## 3. 离线 CLI（不启服务）

```bash
python -m vpmin.cli --source 测试视频.mp4 --settings output/settings.json \
    --standard 70-80 --clock frame --fast --out out.jsonl
```

`--clock wall` 用墙钟（与 10.8.3 一致，生产用）；`frame` 用帧号/fps（离线可复现）。

## 4. 验证

| 项 | 方法 | 结果 |
|---|---|---|
| 状态机等价 | 200 轨迹 × 600 帧对拍原版 `ReactionDetector` | PASS 12 万帧 0 差异（含变异验证） |
| 端到端等价 | 39,666 帧同视频双跑原版引擎 | PASS 3/3 起止时刻一致（84.60/73.17/76.97s） |
| 服务流程 | 合成视频 + 真浏览器 | 起/停/参数/ROI/曲线/事件/存档全通；无 JS 报错 |
| 记录器兼容 | 原版 `record_reactions.py` 直连本服务 | 只写完成反应、7 字段、图像嵌入 |
| 断流重连 | 摄像头读帧失败重连（0=无限） | 见 `camera.py`；文件源循环重放实测通过 |
| 双机位 | 2 号机位只广播 | 未喂判定（引擎计数不随它变） |
| 五类判定 | 1450 炉真实 PLC 回放 + 合成亮光 | 类型序列 0,3,4,2,1,5 全对；亮光 0 条未配对 |
| 类型 2 亮光否决 | 10 条真实长炉（进线 >180s） | 有亮光 0 误报 / 无亮光 10/10 报中断 / 相机停不判 |
| 现场影子实测 | 连续运行数天 | 20 炉全按钮锚点、零漏炉、进线时长全正、arrival 零误报 |

## 5. 与原版的有意差异

- 无登录鉴权（与原版一致，内网使用）
- ROI 支持鼠标画矩形、逐点画多边形（点回起点/双击/回车收尾，Esc 取消），坐标按 object-fit:contain 的真实显示区换算；每次生效都会记进历史，可随时套用
- 曲线图简化（刻度/网格/起止标记齐全，风格不同）
- 标准刷新按帧轮询 + TTL（原版后台线程 5s），读缓存语义相同
- 不做 `max_seconds` 自动停（原版有此参数；需要再说，10 行以内）

## 5.1 参数基线（现场部署必读）

`config/prod-aligned.json` 是与现场生产逐炉对拍一致的参数，部署时把 `params` 段拷进 `settings.json`，**不要用代码里的默认值**。

其中 **`start_confirm` 是最敏感的一个数**：

- 现场 ROI 只有约 1000 px²（8 边形，外接框 59×71），熔液轻微晃动就让亮光全进全出
- 实测 4130 帧里，亮光占比在 40% 阈值上下翻转 160 次，最长「连续满足」只有 0.54 秒
- `start_confirm=0.2` 时起判正常；一旦被调到 1.5 秒，起判会晚 **76 秒**，90.47s 的反应被算成 14.50s，判定结论也会跟着错

2026-10-29 现场影子实例就因为网页上把这一项改成 1.5 而出现过一次 76 秒偏差。回归用例 `tests/test_real_frame_regression.py` 用真实逐帧数据把「参数 → 起判时刻」钉死，改错立刻红：

```bash
python tests/test_real_frame_regression.py
# 0.2s → 33109.31（与生产一致）
# 1.5s → 33185.26（影子事故复现，滞后 75.96s）
```

**改现场参数请走「先备份 → 改 → 回读校验 → 观察下一炉」流程，不要直接编辑 settings.json 后指望进程自己重读（settings 只在启动时 load，热更必须走 `POST /api/params`）。**

完整的参数含义与调大/调小后果对照表：[`docs/核心检测模块.md` §4](docs/核心检测模块.md)。

## 5.2 断点续接与杂光过滤（2026-10-29 现场实测后新增）

两个参数解决现场两类假记录：

### `merge_gap`（默认 0＝关闭，现场设 5.0）

判到结束后**不立刻落盘**，先挂起 `merge_gap` 秒；这期间又判到开始，就当同一炉的续段——
序号不加、起点不改、结束时刻后移、**曲线接回来**（落盘那张图是完整一条，含中间那段凹陷）。

实测：现场同炉中间熄 1.48 秒会被切成 6.0s + 92.7s 两段，合并后 100.3s，
终点误差从 −91.17s 修正到 +2.98s；而反应结束 8.66 秒后才冒出的杂光**不该**合并
（合并会把本来正确的炉从 +3.95s 搞成 +23.6s），所以窗口取 5 秒。

### `guard_window` / `guard_peak` / `min_duration`（现场设 3.0 / 80 / 20）

球化站**开门时的杂光**会被阈值法当成一炉反应。实测开门杂光特征极稳定：
起判时占比只有 **40.0~45.2%**、灰度 131~183；而正常炉 2~3 秒内必冲到 **90% 以上**、灰度 228。

所以起判后先考察 3 秒：峰值不到 80% 就判为杂光，**状态复位、序号退回、不落盘**；
再加一道 `min_duration`：总时长不足 20 秒也丢弃（现场杂光实测 5~16 秒，正常反应最短 50 秒）。

实测效果（124 万帧）：5 条开门杂光全部清掉，69 条正常反应**零误杀**。

```bash
python tests/test_merge_gap.py        # 断点续接
python tests/test_spurious_filter.py  # 杂光过滤
```

## 5.3 输出文件口径（本版，不照抄原版）

- `output/frames/web_<时间戳>_reaction.csv`：11 列，列名 `Frame / Elapsed(s) / Mean Gray /
  Highlight Start (%) / Highlight End (%) / Reaction State / Start Confirm(s) / End Confirm(s) /
  Reaction Start(s) / Reaction End(s) / Reaction Duration(s)`，UTF-8 BOM
- `output/data_values/reaction_events_history_10_8.csv`：**16 列**
  （元信息 8 列 + 事件 8 列）
  - 比原版**少两列恒值列**：「判断口径」（只有 highlight 一种）、「速度阈值(灰度/秒)」
    （本模块没有速度判据，恒为 0），都是原版历史包袱
  - 比原版**多一列**：「实时时间」——原版事件表只有 elapsed，出了纠纷对不到墙钟，本版补上
- `output/reactions.jsonl`：7 字段（字段名/顺序沿用业务契约），**固定文件名、只追加不轮转**
  （汇总程序按固定名读并记位点，换名会让下游读不到；每行约 340 字节，一年 35MB，无需轮转）
  - 尾部**追加**三个新字段（原 7 字段不动，按字段名读的下游天然兼容）：
    `abnormal_type` / `abnormal_reason` / `spheroidization_segments`（仅多段时输出）
  - `spheroidization_image` **内嵌曲线图 base64**（管理系统/汇总程序直接从这一列拿图入库）
  - 单行约 80KB；一年约 8.7GB，**永不自动删除**（主数据）。
    想省体积可加 `--jsonl-no-image`（字段改记文件名，下游需自行取图）
  - 原版"满 10 条清空"的做法不采用：合并器慢一步就永久丢数
- **保留策略**：逐帧 CSV 保留 **7 天**、曲线图 **30 天**（`--keep-frame-days` / `--keep-days` 可调），
  后台每小时清理一次超期文件，只依赖 mtime 且走白名单 glob，不碰 settings/counter。
  **`reactions.jsonl` 不参与清理**（主数据）
- `output/image/reaction_*.jpg`：960×540 灰度曲线图（算法与原版逐像素一致）

## 6. PLC 融合与五类判定

### 6.1 五类异常（`abnormal_type`）

| 码 | 名称 | 判据 | 数据源 |
|---|---|---|---|
| 0 | 正常 | 时长在标准区间内、无超阈停顿 | 亮光 |
| 1 | 进站未球化 | 按钮后 120s 无亮光；或到位后 120s 无任何动作 | PLC + 亮光 |
| 2 | 进线中断 | 喂丝开始后 180s 无结束位（有亮光否决）；或进线 <60s | PLC + 亮光 |
| 3 | 超过标准 | 亮光时长 > 上限 | 亮光 |
| 4 | 低于标准 | 亮光时长 < 下限 | 亮光 |
| 5 | 中间停顿 | 段数 ≥2 且最大停顿 > `pause_threshold`（现场 10s） | 亮光 |
| 6 | 未判定 | 缺时长或缺标准区间 | — |

**优先级：2 > 1 > 5 > 3 > 4**（一炉只能入一个主类型，次级异常拼进 `abnormal_reason` 的「另：…」，限 200 字）。

**判定时长口径：亮光首段起 → 末段止（含中间停顿）**，不是各段相加。

### 6.2 PLC 位表

```python
("button",     200, 128, 0,  0.0)    # 按钮（点动，上升沿）
("feed_start", 200, 129, 2,  0.0)    # 喂丝开始
("feed_end",   200, 129, 3, 24.0)    # 喂丝结束 —— 物理停止后 24s 才置位
weight: DB200.DBD8  REAL             # 料罐重量，每拍都读
```

- **`feed_end` 的 24 秒回推**：事件保留 `raw_ts`（位真正跳变）与 `ts`（回推后），判定用 `ts`，超时检测用 `raw_ts`
- **“铁水到位”位现场未接线**；重量启发式（重量上涨即到位）**默认关闭**（`--plc-weight-arrival off`）——
  喂丝时重量本来就在变（1792→1800→1840），开会把一炉切成好几段
- 锚点优先级：**button > feed_start > light > arrival**，最早者胜；`detection_time` = 锚点时刻

### 6.3 关键参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `pause_threshold` | 0（现场 10） | 分段停顿阈值秒；0 = 不判类型 5 |
| `merge_gap` | 0（现场 5） | 断点续接窗口；同炉中间熄灭不被切两段 |
| `--plc-poll` | 0.1 | PLC 轮询间隔 |
| `feed_end_timeout` | 180 | 喂丝多久没结束算中断（**未校准，现场进线 86~141s，最长见过 175s**） |
| `feed_min_duration` | 60 | 进线时长下限 |
| `light_silence_sec` | 60 | 「刚熄」窗口，必须 > 24s |
| `button_dedupe_sec` | 10 | 同炉连按去抖 |

### 6.4 输出与排障

- `output/plc/plc_events.jsonl`：一变一行。**看到 `boot` = 已连上 PLC**；只有 boot 没后续 = 现场没动作；
  连 boot 都没有 = 没连上（查 IP / 网段 / snap7）
- `output/plc/heat_records.jsonl`：一炉一条，字段对齐后端 `SpheroidizationRecord`（含 `abnormal_type` /
  `abnormal_reason` / `spheroidization_segments` / `plc_anchor` 等）
- 两个文件**只追加不滚动**（滚动会让追尾读取的合并器丢同步）
- 回放验证（无硬件）：`python tests/test_plc_replay.py` / `test_fusion_replay.py` / `test_type2_veto_real.py`

> 完整设计、状态机图、坑位清单：[`docs/PLC模块.md`](docs/PLC模块.md)

## 7. 测试

```bash
# 纯 Python（无外部依赖）
python3 tests/test_parity_state_machine.py   # 状态机 12 万帧对拍
python3 tests/test_judge_types.py            # 类型 3/4/5/6 判定
python3 tests/test_segments_runner.py        # 分段生成/续接/炉边界
python3 tests/test_median_filter.py          # 因果中值滤波
python3 tests/test_heat_tracker.py           # 一炉状态机（类型 1/2）
python3 tests/test_fusion.py                 # 五类合一优先级
python3 tests/test_arrival_noise.py          # 重量启发式回归（不得再触发）
python3 tests/test_loggers.py               # 逐帧/事件 CSV 列序与格式
python3 tests/test_settings_image_store.py
python3 tests/test_roi_store.py

# 依赖真实生产数据（公开仓库不随附，缺文件时自动 SKIP）
python3 tests/test_real_frame_regression.py  # 真实逐帧：参数→起判时刻
python3 tests/test_plc_replay.py             # 1450 炉 PLC 回放
python3 tests/test_type2_veto_real.py        # 真实长炉的亮光否决
python3 tests/test_fusion_replay.py          # 回放 + 合成亮光 → 五类分布
python3 tests/test_real_types_345.py         # 50 炉 × 生产 DB 实判对比

# 真浏览器（需要 playwright；默认复用 /tmp/uitest 的 node_modules）
bash tests/browser/run.sh http://127.0.0.1:5099
#   roi_edit.js   ROI 顶点/边/角拖动、整体平移、撤销重做、双击边插点、Esc、还原
#   cors_embed.js 跨源 GET/POST（含 OPTIONS 预检）、iframe 嵌入渲染
```
