# vpmin · 球化亮光检测（极简分层实现）

**判定内核行为对齐参考实现**（已 12 万帧对拍 + 端到端 3/3），并补齐生产必需的周边：HTTP 服务、双机位、参数/ROI 热更新、逐帧 CSV、事件表、曲线存档、断流重连、守护单元。

- 判定内核：773 行（domain + adapters + runner），无 Web 时也能当 CLI 跑
- 含生产服务：约 2050 行（Python）+ 180 行前端（原生 JS，无构建）
- 依赖：Python 3 + OpenCV + numpy（与原版一致），**Web 服务零额外依赖**（标准库 http.server）

## 1. 分层

```
domain/                 纯逻辑，不 import cv2/numpy/urllib（可离线单测）
  models.py       4 个数据结构：帧指标 / 迁移结果 / 区间 / 判定
  trigger.py      只做百分比阈值比较
  state_machine.py 六态迁移 + rearm（COOLDOWN 迟滞）
  standard.py     三级区间回退（后端上下限 → ±容差 → 本地；取失败不清缓存）
  judge.py        闭区间判定
  curve.py        曲线点集（含 5s 前置基线；结束后保留最后一条供页面显示）

adapters/               一个外部依赖一个文件
  probe.py        像素统计（矩形/多边形同口径）
  roi.py          ROI 几何与外接框
  source.py       离线取帧（CLI 用；frame/wall 两种时钟）
  camera.py       生产取帧：断流重连 + 最新帧/JPEG（分析机位才回调）
  standard_http.py 唯一发 HTTP 的地方（TTL 30s，force 手动刷新）
  painter.py      曲线图 → JPEG data URI
  sink.py         reactions.jsonl（7 字段同名同序）
  counter.py      reaction_count 持久化
  settings.py     settings.json 读写（原子写）
  roi_store.py    ROI 历史（最近 50 条，按几何去重）
  frame_csv.py    逐帧 CSV（11 列）
  event_log.py    START/END 事件（CSV + 内存最近 500）
  image_store.py  曲线图存档 + 最近 200 张清理
  formats.py      hms() / wall()

app/                   编排（不碰像素、不发 HTTP）
  analysis.py     单机位分析循环：计时 → 判定 → 日志/存档
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
SOURCE="rtsp://admin:***@192.0.2.10:554/Streaming/Channels/102" \
SOURCE2="rtsp://admin:***@192.0.2.11:554/Streaming/Channels/102" \
BIZ_URL="http://127.0.0.1:8000/api/standards/current" \
PORT=5080 ./run.sh
```

- 打开 `http://<服务器IP>:5080/`
- 开机自启：`sudo cp deploy/vpmin.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vpmin`
- 输出：`output/reactions.jsonl`（7 字段）、`output/frames/web_*.csv`、`output/data_values/reaction_events.csv`、`output/image/*.jpg`、`output/reaction_counter.json`、`output/settings.json`

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

## 5. 与原版的有意差异

- 无登录鉴权（与原版一致，内网使用）
- ROI 支持鼠标画矩形、逐点画多边形（点回起点/双击/回车收尾，Esc 取消），坐标按 object-fit:contain 的真实显示区换算；每次生效都会记进历史，可随时套用
- 曲线图简化（刻度/网格/起止标记齐全，风格不同）
- 标准刷新按帧轮询 + TTL（原版后台线程 5s），读缓存语义相同
- 不做 `max_seconds` 自动停（原版有此参数；需要再说，10 行以内）

## 5.1 输出文件口径（本版，不照抄原版）

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
  - `spheroidization_image` **内嵌曲线图 base64**（管理系统/汇总程序直接从这一列拿图入库）
  - 单行约 80KB；一年约 8.7GB，**永不自动删除**（主数据）。
    想省体积可加 `--jsonl-no-image`（字段改记文件名，下游需自行取图）
  - 原版"满 10 条清空"的做法不采用：合并器慢一步就永久丢数
- **保留策略**：逐帧 CSV 保留 **7 天**、曲线图 **30 天**（`--keep-frame-days` / `--keep-days` 可调），
  后台每小时清理一次超期文件，只依赖 mtime 且走白名单 glob，不碰 settings/counter。
  **`reactions.jsonl` 不参与清理**（主数据）
- `output/image/reaction_*.jpg`：960×540 灰度曲线图（算法与原版逐像素一致）

## 6. 测试

```bash
# 纯 Python（无外部依赖）
python3 tests/test_parity_state_machine.py   # 状态机 12 万帧对拍
python3 tests/test_loggers.py               # 逐帧/事件 CSV 列序与格式
python3 tests/test_settings_image_store.py
python3 tests/test_roi_store.py

# 真浏览器（需要 playwright；默认复用 /tmp/uitest 的 node_modules）
bash tests/browser/run.sh http://127.0.0.1:5099
#   roi_edit.js   ROI 顶点/边/角拖动、整体平移、撤销重做、双击边插点、Esc、还原
#   cors_embed.js 跨源 GET/POST（含 OPTIONS 预检）、iframe 嵌入渲染
```
