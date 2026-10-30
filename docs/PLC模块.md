# PLC 模块详解

> 对象：`vpmin/adapters/plc.py` + `vpmin/app/plc_loop.py` + `vpmin/domain/heat.py`
>      + `vpmin/domain/fusion.py` + `vpmin/app/fusion_loop.py`
> 目标：读完这份文档，能自己加一个 PLC 位、调超时阈值、定位"某一炉为什么判成类型 2"。

---

## 1. 为什么需要 PLC 模块

亮光检测只能回答"**球化反应持续了多久**"。而五类异常里有两类**亮光根本看不出来**：

| 类型 | 现象 | 亮光能看出来吗 |
|---|---|---|
| 1 进站未球化 | 铁水来了，没喂丝/没反应 | ❌ 压根没有亮光，检测侧无从知晓"这炉来过" |
| 2 进线中断 | 喂丝开始后没正常结束 | ❌ 亮光可能一直有（长炉），也可能全程没有 |

所以必须接 PLC：**喂丝开始/结束/按钮/重量** 这几个位，是判断"人到底做了什么动作"的唯一证据源。

设计原则一句话：**亮光判 3/4/5，PLC 判 1/2，最后在一层收敛成一个类型码。**

---

## 2. 分层与数据流

```
                  ┌──────────────────────────────────────────────┐
                  │  PLC (S7-1200/1500, snap7)                    │
                  └───────────────┬──────────────────────────────┘
                                  │ 0.1s 轮询读位 + 读重量
                                  ▼
        adapters/plc.py  ┌──────────────────┐
        PlcSource        │  EdgeDetector    │  电平 → 事件（上升沿、去抖、延迟补偿）
        ReplaySource     │  （三种来源共用） │
        ScriptSource     └────────┬─────────┘
                                  │ PlcEvent(kind, ts, raw_ts, weight)
                                  ▼
        app/plc_loop.py  ┌──────────────────┐   append-only
        PlcLoop          │ PlcEventLog      │──► output/plc/plc_events.jsonl
                         └────────┬─────────┘
                                  │ 事件 + 每秒 tick
                                  ▼
        domain/heat.py   ┌──────────────────┐
        HeatTracker      │ 一炉生命周期状态机 │  判 1 / 2
                         └────────┬─────────┘
                                  │ Heat（关炉时）
                                  ▼
        app/fusion_loop.py ┌───────────────┐   亮光侧 LightResult（来自 runner/analysis）
        FusionLog          │ 配对 + 收敛    │◄──────────────────────────────┘
                           └───────┬───────┘
                                   │
                                   ▼
                    output/plc/heat_records.jsonl
                    （字段对齐后端 SpheroidizationRecord）
```

依赖方向严格单向：`adapters → 事件`、`domain → 纯逻辑`、`app → 编排`。
`domain/heat.py` 和 `domain/fusion.py` 不 import snap7、不看墙钟（时间全由调用方传入），所以能脱机回放测。

---

## 3. `adapters/plc.py` — 采集与边沿检测

### 3.1 位地址表与重量寄存器

```python
DEFAULT_POINTS = (
    # (事件名, DB号, 字节, 位, 时间补偿秒)
    ("button",     200, 128, 0,  0.0),
    ("feed_start", 200, 129, 2,  0.0),
    ("feed_end",   200, 129, 3, 24.0),
)
DEFAULT_WEIGHT = (200, 8, 4, "REAL")     # DB200.DBD8  REAL，每拍都读
```

四件事必须知道：

1. **`feed_end` 有 24 秒物理延迟**。这个位在喂丝物理停止后 24 秒才置位。
   所以事件里有两个时间：
   - `raw_ts`：位真正跳变的时刻（PLC 视角，滞后 24s）
   - `ts`：**回推 24 秒后的判定用时刻**（物理真实结束时刻）
   
   **判定一律用 `ts`，只有"超时检测"要用 `raw_ts`** —— 这个区分是进线中断判定准不准的关键。

2. **`button` 是点动（ momentary）**。按一次产生一次上升沿；`button_mode` 预留了 `latched`（自锁，
   要等回落）以备现场换成自锁按钮。

3. **重量不再只在结束时读**，每拍都读（0.1s 一次）。用途：记录每炉重量、以及可选的"到位"启发式
   （**默认关闭**，见 3.3）。

4. **字段可变**：`--plc` 只开了真机模式；改动位表就改 `DEFAULT_POINTS`，或从 `PlcSource(points=...)`
   传进来。位表是**协议地址**，不含任何敏感信息。

### 3.2 `EdgeDetector` — 三种来源共用的核心

```python
def feed(self, now, bits: Dict[str, int], weight) -> List[PlcEvent]
```

一次调用处理一拍（0.1s），最多产出 2 条事件。规则：

- **首次只记电平**：`if name not in self.prev: self.prev[name] = cur; continue`
  —— 启动那一刻如果位已经是 1，不补发事件（否则每次重启都会凭空造一条假的"喂丝开始"）
- **只认 0→1 上升沿**：`cur == 1 and self.prev[name] == 0`
- 时间补偿在这里应用：`ts = now - offset if offset else now`

`boot_event()`：连接成功后发一条 `boot` 事件。**它是判断"这个进程有没有连上 PLC"的唯一标志**——
事件文件里没有 boot，说明采集没通（现场排障第一步就看它）。

`reset()`：断线重连后调用，只清电平表不补发事件，避免重连瞬间造出假边沿。

### 3.3 🔴 "铁水到位"（arrival）与重量启发式

PLC **没有**"铁水到位"专用位。历史上有两种做法：

| 做法 | 原理 | 问题 |
|---|---|---|
| 专用位（首选） | 到位传感器/PLC 程序置位 | 现场未接线 |
| 重量启发式（兜底） | 重量上涨 ≥ 5kg 就当新炉到位 | **会误判**（见下） |

重量启发式的参数：`arrival_weight_min=100`（重量有效下限）、`arrival_weight_step=5`（涨幅阈值）、
`arrival_dedupe_sec=30`（去抖）。**默认关闭**（`--plc-weight-arrival off`）。

**为什么默认关闭（2026-10-29 现场事故）**：

- 喂球化剂时料罐重量本身在变（实测 1792 → 1800 → 1840）
- 每次"上涨"都被当成"新一炉到位" → **一炉被切成好几段**、喂丝起止错配（进线时长出现 −583s）、
  亮光串炉（出现 660s 荒谬值）、类型 1 误报 4 条
- 另外"相邻两炉重量完全相同"占 13.5%（195/1449），靠重量变化**必然漏**，本来就不该当主判据

如果要重新启用（比如现场确认重量在两炉之间会归零），加 `--plc-weight-arrival on`，
并注意上面那条"一炉被切碎"的回归用例 `test_arrival_noise.py` 会立刻变红。

### 3.4 `PlcSource` — 真机采集

```python
PlcSource(on_event, ip="192.0.2.1", rack=0, slot=1, poll=0.1, ...)
```

- **独立守护线程**，0.1s 一拍：`_read_bits()` → `_read_weight()` → `detector.feed()` → 回调
- **断线重连**：任何异常 → `_disconnect()` → sleep 2s → 重连（不刷日志，防止断网时把磁盘写满）
- **不影响视觉**：采集线程挂了只影响 1/2 判定，视频和 3/4/5 照常跑
- `status()` 给出 `connected / reads / events / last_weight / ip`，状态页直接显示

### 3.5 `ReplaySource` / `ScriptSource` — 无硬件跑全链路

`ReplaySource` 把"一炉一行"的历史数据**合成**成位电平，再走真实 `EdgeDetector`：

```
feed_start 位：[start, end) 内为 1
feed_end   位：物理结束 24s 后置位，脉冲 1s      ← 24s 补偿的来源，合成时如实模拟
arrival    位：start 前 arrival_lead 秒置位（脉冲 5s）
weight      ：到位后取本炉值，持有到下一炉
```

这就是"没有 PLC 也能验证 1/2 判定"的办法，`test_plc_replay.py` / `test_fusion_replay.py` /
`test_type2_veto_real.py` 都基于它。`ScriptSource` 更简单，直接给一串 `(时刻, 位状态)`，
用于精确构造边界用例。

---

## 4. `app/plc_loop.py` — 同进程回路

```python
plc_loop = PlcLoop(PlcSource(...), HeatTracker(...), PlcEventLog(path),
                   on_heat=fusion.add_heat, on_tick=_on_tick)
```

三件事：

1. **`handle(ev)`**：先回调炉边界钩子（`on_arrival` / `on_button`），再落事件，再喂状态机，最后把关掉的炉交给 `on_heat`
2. **`light(kind, ts)`**：亮光起止也喂进状态机（`light_start` / `light_end`），只用于**排除误判**——
   类型 2 的"有亮光就不判中断"就靠它
3. **`_tick_loop()`**：每秒一次 `tracker.tick()`（判超时/未球化/长空闲）+ `on_tick()`（结算配对、同步亮光流状态）

**`PlcEventLog` 是 append-only，永不滚动**。滚动会让"追尾读取"的下游（合并器）丢同步——
现场踩过：`reactions.jsonl` 满 10 条清空，合并器慢一步就永久丢数。所有主数据文件都遵守这条。

### 炉边界钩子的意义

```python
plc_loop.on_arrival = runtime.flush_light_pending
plc_loop.on_button  = runtime.flush_light_pending
```

PLC 出现"到位/按钮"意味着**炉子换了一炉**，必须立刻把上一炉挂起的亮光记录结算掉。
不加这个钩子，相邻两炉亮光间隔 86s < `merge_gap` 120 时会被"断点续接"合成一条跨炉记录（真踩过）。

---

## 5. `domain/heat.py` — 一炉的生命周期状态机

### 5.1 Heat：一炉的全部证据

```python
arrival_ts / anchor / anchor_ts / weight
button_ts / feed_start_ts / feed_end_ts / feed_end_raw_ts
light_start_ts / light_end_ts / light_active / last_light_ts
feed_raw_pending / judged_type / reason
```

**锚点（anchor）优先级：button > feed_start > light > arrival，谁最早谁是锚点。**
`anchor_ts` 就是这一炉的 `detection_time`（判定从哪一刻开始算）。

### 5.2 事件处理规则（逐条）

| 事件 | 处理 |
|---|---|
| `arrival` | **一炉正在进行中（还在喂丝/还在亮）时，一律不当新炉** —— 直接 return，防止重量抖动把一炉切断 |
| `button` | 去抖：同炉内 `button_dedupe_sec`（10s）内的重复按键忽略；若上一炉已有实际动作（喂丝/亮光），这次按下算**新一炉**（按钮兼作炉边界） |
| `feed_start` | 补建炉、记 `feed_start_ts`、置 `feed_raw_pending=True` |
| `feed_end` | 记 `ts` 与 `raw_ts`、清 pending；**进线时长 < `feed_min_duration`（60s）→ 立刻判类型 2** |
| `light_start` | 记亮光起、`light_active=True`、记 `last_light_ts` |
| `light_end` | 记亮光止、`light_active=False`，尝试关炉 |

### 5.3 类型 1（进站未球化）的两条判据

`tick()` 里每秒检查：

```python
# 1a 按了按钮但一直没亮光
button_no_light_sec = 120
if 有 button_ts and 没有亮光:
    if now - button_ts >= 120:  → 类型 1「按钮按下 120s 后仍未检测到球化亮光」

# 1b 铁水到位后 W1 秒内没有任何动作
no_action_sec = 120
if 有 arrival_ts and 未开始处理(无按钮无喂丝) and 无亮光:
    if now - arrival_ts >= 120:  → 类型 1「铁水到位 120.0s 后仍未检测到球化动作与亮光」
```

**已知盲区（现场接受）**：如果既没按钮、又没喂丝、也没亮光 —— 也就是"整炉什么都没干"——
**除了 arrival 没有任何信号能发现这炉存在过**。当前 arrival 位未接线，所以这条防线是空的。
补齐办法：PLC 加一个"铁水到位"位（成本很低），`DEFAULT_POINTS` 加一行即可，`HeatTracker` 无需改动。

`no_action_mode` 两种模式：
- `window`（默认）：到点即判
- `next_arrival`：等下一炉到位时才追判（适合到位信号准、但动作晚的场景）

### 5.4 类型 2（进线中断）的两条判据 + 亮光否决

```python
# 2a 喂丝开始后 N 秒没有结束位（按 raw 计时，因为 ts 已回推 24s）
feed_end_timeout = 180
if feed_raw_pending and now - feed_start_ts >= 180:
    if 亮光流不可用(相机没开/检测停) → 不判（宁可不判也不误报）
    if 还在亮 or 刚熄 ≤ light_silence_sec(60s) → 不判（这是长炉，不是中断）
    → 类型 2「喂丝开始后 180s 未收到喂丝结束且无亮光」

# 2b 进线时长短于下限
feed_min_duration = 60
if feed_end 到了 and (feed_end_ts - feed_start_ts) < 60:
    → 类型 2「进线未正常完成：进线时长 45.2s < 最短 60s」
```

**亮光否决为什么必须有**：真实数据里有 10 炉进线 >180s（最长 887s），只按超时会全部误报。
实测 10 条真实长炉：有亮光 → 0 误报；无亮光 → 10/10 正确报中断。

`light_silence_sec` 必须 > 24s（结束位延迟），默认 60s 是安全的。

### 5.5 关炉条件（容易踩的坑）

```python
def _maybe_close(now):
    if feed_end 没到 → 不关
    if 亮光结束已记 → 关
    if 整炉没亮光 and now - feed_end_ts >= light_grace(30s) → 关
```

**为什么不能"见到亮光结束就关"**：喂丝结束位比亮光结束还晚约 24 秒到，
提前关炉会把进线结束/时长丢掉，融合时就拿不到进线数据。

`close_idle_sec = 600`：太久没动静就强制关炉（炉间隔 8~12 分钟，600s 是安全上限），
防止一条炉永远挂着把后续全污染。

---

## 6. `domain/fusion.py` — 五类合一

### 6.1 为什么必须有这一层

数据库 `abnormal_type` **只有一列**，而 1/2 只有 PLC 能判、3/4/5 只有亮光能判。
一炉可能同时命中多类（例如"中间停顿 42s"导致"总时长超标准"），必须收敛成一个主类型 + 完整原因。

### 6.2 优先级

```python
PRIORITY = {2: 0, 1: 1, 5: 2, 3: 3, 4: 4, 0: 9, 6: 9}
#           进线中断 > 进站未球化 > 中间停顿 > 超过标准 > 低于标准
```

**次级异常不丢**：拼进 reason 的「另：…」，总长截断到 200 字（后端字段限制）。

例：

```
球化过程中间亮光停顿：第 3 段前停顿 42.633s
```

### 6.3 `build_final()` — 生成最终记录

```python
detection_time = anchor_ts or button_ts or feed_start_ts or arrival_ts
```

字段名**尽量对齐后端 `SpheroidizationRecord`**，方便以后直接推送：

| 输出字段 | 来源 |
|---|---|
| `reaction_count` | 本进程第几条记录 |
| `detection_time` | 锚点时刻 |
| `spheroidization_start_time` / `_end_time` | 亮光起 / 止 |
| `actual_spheroidization_time` | `HH:MM:SS.ss`，**首段起→末段止** |
| `spheroidization_abnormal` | 0 成功 / 1 失败 / null 未判定 |
| `abnormal_type` / `abnormal_reason` | 五类合一结果 |
| `spheroidization_wire_entry_start_time` / `_end_time` / `_total_duration` | PLC 进线（用回推后的 ts） |
| `spheroidization_weight` | 该炉重量 |
| `plc_button_time` / `plc_arrival_time` / `plc_anchor` | 锚点与按钮证据 |
| `spheroidization_segments` | 仅多段时输出（2/5 的证据） |

### 6.4 时间轴：`shift_segments()` 的坑

亮光侧的分段是**相对 elapsed 秒**，PLC 侧是 **epoch 秒**。
`serve.py` 接线时必须把分段平移到 epoch 轴：

```python
segs = tuple(Segment(base_ts + s.start, base_ts + s.end, s.gap_before) for s in verdict.segments)
```

**只在接线口换一次，别在别处再换**。漏了这一步，记录里的分段会显示成 1970 年（真踩过，回归用例已钉住）。

---

## 7. `app/fusion_loop.py` — 配对与落盘

### 7.1 配对规则

```python
matches(heat, light, tolerance=5.0, light_grace=30.0)
# 亮光起点落在 [炉起 - 5, 炉止 + 30] 内 → 算这一炉的
```

双向配对：
- `add_heat()`：新炉到了，先看看有没有"already 到场但没配上"的亮光
- `add_light()`：亮光到了，先看看有没有"已到场等它"的炉

一炉只配一次。配不上的亮光进 `_lights` 缓冲，超过 `light_ttl`（900s）丢弃并计入 `unmatched_lights`。

### 7.2 落盘时机

```python
item["ready"] = 炉止 + match_grace(30s)      # 等亮光落定（喂丝结束后亮光还要 3~4s 才结束）
settle(now)  # 每秒调一次，到期就出记录
flush(now)   # 停机时把 pending 全出掉，不丢数据
```

### 7.3 输出

`output/plc/heat_records.jsonl` —— 一行一炉，字段见 6.3。

**两个文件都只追加、不滚动**：`plc_events.jsonl`（事件流，排障用）、`heat_records.jsonl`（业务主数据）。
重启后 `seq` 从 1 重新计数是正常的（不同 boot 用时间戳区分）。

---

## 8. 线程模型与失效模式

```
主线程        HTTP 服务（ThreadingHTTPServer）
 ├─ 相机线程  RTSP 取流 + 断流重连（分析机位才回调 on_frame）
 ├─ 分析线程  逐帧 process()（跑核心检测模块）
 ├─ PLC 线程  0.1s 轮询读位/重量 → EdgeDetector → PlcLoop.handle()
 └─ PLC tick  1s 一次 → HeatTracker.tick() + FusionLog.settle()
```

- 全部后台线程都是 `daemon`（主线程退出即全部退出）
- `HeatTracker` 的跨线程访问由 `PlcLoop._lock` 保护
- 任何一侧异常都不拖垮另一侧：PLC 断了视觉照跑，相机断了 1/2 判定照跑（`light_stream_ok=False`
  时类型 2 直接不判，宁可漏报不误报）

**"没连上 PLC"的识别**：事件文件里只看到 `boot`、没有后续事件 → PLC 连上了但现场没动作；
**连 boot 都没有** → 压根没连上（检查 IP / 网段 / snap7 是否安装）。

---

## 9. 阈值与校准状态

| 参数 | 默认 | 含义 | 校准状态 |
|---|---|---|---|
| `no_action_sec` | 120 | 到位后多久没动作算未球化 | 出厂值，未校准（等真到位位） |
| `button_no_light_sec` | 120 | 按按钮后多久没亮光算未球化 | 现场验证有效 |
| `feed_end_timeout` | 180 | 喂丝开始后多久没结束算中断 | **未校准**（现场正常进线 86~141s，最长见过 175s，离红线很近） |
| `feed_min_duration` | 60 | 进线时长下限 | 未校准 |
| `light_silence_sec` | 60 | 「刚熄」窗口，必须 > 24s | 安全 |
| `light_grace` | 30 | 喂丝结束后等亮光的宽限 | 安全 |
| `close_idle_sec` | 600 | 长空闲强制关炉 | 安全 |
| `button_dedupe_sec` | 10 | 同炉连按去抖 | 现场验证有效 |
| `match_grace` | 30 | 融合配对宽限 | 安全 |
| `arrival_weight_step` | 5 | 重量涨幅阈值（启发式已关） | — |

> ⚠️ `feed_end_timeout=180` 是最需要盯着的一个数。现场 2026-10-30 实测进线时长
> 上午 86~105s、午后 121~141s（最长 175.7s）。正常炉一旦接近 180s 就会误报类型 2。
> 建议做法：攒 2~3 天进线时长分布，取 P99 再上浮 20% 作为阈值。

---

## 10. 现场踩过的坑（都是真事故）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 进线时长 −583s / −667s | 重量启发式把喂丝当到位，feed_start 与 feed_end 来自不同"炉" | 关闭重量启发式（默认 off） |
| 2 | 一炉被切成多段，亮光 660s | 同上：喂丝中重量反复上涨触发 arrival | 同上 + `arrival` 在一炉进行中不做新炉 |
| 3 | 类型 1 误报 4 条 | 启动首拍重量就被当"到位"，停机时段 120s 后必误报 | 首拍只记基线；到位不接重量 |
| 4 | 相邻两炉被合成一条跨炉记录 | 两炉亮光间隔 86s < `merge_gap` 120 | PLC 到位/按钮 = 炉边界，强制 `flush_light_pending()` |
| 5 | 记录里分段显示 1970 年 | 相对秒当 epoch 秒渲染 | `shift_segments()` 在接线口平移 |
| 6 | 关炉太早丢失进线数据 | 结束位比亮光晚 24s 到 | 「喂丝结束 + 亮光结束」两样齐才关炉 |
| 7 | 长炉（>180s）被误报中断 | 只看超时 | 亮光否决：还在亮/刚熄 60s 内不判 |
| 8 | 相机停时批量误报类型 2 | 没有亮光信息 | `light_stream_ok=False` 时不判 2a |
| 9 | 重启后所有墙钟时间倒回 | `runner.base` 只在建服务时设一次 | 重启时重设 base（`test_analysis_restart.py` 钉住） |

---

## 11. 怎么扩展

### 加一个 PLC 位（例如真正的"铁水到位"位）

1. `DEFAULT_POINTS` 加一行：`("arrival", 200, 130, 0, 0.0)`（DB 号/字节/位按现场图纸）
2. 确认 `EdgeDetector` 的 `weight_arrival` 自动关闭（有位就不用重量猜：`not any(p[0]=="arrival" ...)`）
3. `HeatTracker` 不用改（`on_event` 里已经有 `KIND_ARRIVAL` 分支）
4. 回归：`test_heat_tracker.py`（24 项）+ `test_arrival_noise.py`

### 加一个新的事件类型

`adapters/plc.py` 位表 → `domain/heat.py` 加 `KIND_*` 常量和分支 → `Heat` 加字段 →
`fusion.build_final()` 加输出字段 → 后端表加列。**五处同批改，漏一处会静默丢字段。**

### 校准阈值

```bash
# 1) 回放历史数据算分布（不接现场 PLC）
python tests/test_plc_replay.py
# 2) 现场影子跑 2~3 天，读 heat_records.jsonl 统计进线时长分位
python - <<'PY'
import json, statistics
d=[json.loads(l) for l in open('output/plc/heat_records.jsonl')]
f=[x['spheroidization_wire_entry_total_duration'] for x in d if x.get('spheroidization_wire_entry_total_duration')]
print(len(f), 'P50', statistics.median(f), 'P99', sorted(f)[int(len(f)*0.99)])
PY
```

---

## 12. 测试对照表

| 用例 | 钉住什么 |
|---|---|
| `test_heat_tracker.py` | 24 项：按钮开炉/后到不改锚点/先喂丝后补按/连按去抖/按钮后无亮光/arrival 不切割 |
| `test_arrival_noise.py` | 重量抖动不产生 arrival、首拍只记基线 |
| `test_plc_replay.py` | 1450 炉回放：事件数、进线时长、24s 补偿 |
| `test_type2_veto_real.py` | 10 条真实长炉：有亮光 0 误报、无亮光 10/10 报、相机停不判 |
| `test_fusion.py` | 优先级、次级异常拼进 reason、200 字截断 |
| `test_fusion_replay.py` | 1450 炉 + 合成亮光 → 五类分布、零未配对 |
| `tests/e2e_drill.py` | 6 炉覆盖全五类，类型序列 0,3,4,2,1,5 |
