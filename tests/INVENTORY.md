# tests/ 测试文件盘点（T-007a · 2026-11-01）

范围：`tests/` 下 22 个 `.py` 文件 + `tests/browser/` 4 个前端手验文件（browser 不算 Python 测试，仅列出不盘点状态）。
盘点口径：pytest 9.2.0（`.venv-test`，仅装 pytest），`python3 = 3.12.3`，纯 stdlib 环境（无 cv2/numpy）。
领域层 `vpmin/domain/` 与 `vpmin/app/{analysis,plc_loop,fusion_loop}`、`vpmin/adapters/{formats,frame_csv,event_log,plc,retention,roi_store,settings,image_store,counter,sink}` 全部纯 stdlib，可无依赖运行；**需要 cv2 的只有** `vpmin/adapters/{source,camera,roi,probe,painter,plot_series,plot_render}.py`。

## 一、本次已可收集（pytest 原生，改造前就绿）

| 文件 | 状态 | 说明 |
|---|---|---|
| `test_arrival_noise.py` | ✅ 已可收集（11 例） | `unittest.TestCase`，重量到位启发式噪声回归；stdlib（`adapters.plc` + `domain.heat`） |
| `test_settings_image_store.py` | ✅ 已可收集（5 例） | SettingsStore/ImageStore 读写；stdlib + tempfile |

## 二、第一批改造完成（脚本式 → pytest 包装，原脚本保留不动）

包装约定：新文件 `test_py_<原名>.py` 直接 import 原脚本模块（复用其 `run()` / `heat()` / `replay()` / `build_series()` 等驱动函数），逐条用例改写为 pytest 断言，口径与原脚本一致。

| 原脚本 | 包装文件 | 例数 | 覆盖内容 |
|---|---|---|---|
| `test_judge_types.py` | `test_py_judge_types.py` | 19 | 判定 0/3/4/5/6、优先级、闭区间边界、停顿阈值边界、浮点舍入 |
| `test_segments_runner.py` | `test_py_segments_runner.py` | 11 | 分段（类型5）与 3/4 在真实 ReactionRunner 里的行为、merge_gap、炉边界强制结算、老 7 字段兼容 |
| `test_merge_gap.py` | `test_py_merge_gap.py` | 7 | 断点续接：合并/不合并、时长、序号、落盘字段、曲线接龙 |
| `test_heat_tracker.py` | `test_py_heat_tracker.py` | 15 | 一炉状态机类型 1/2、next_arrival 模式、锚点、按钮去抖、空闲关炉 |
| `test_fusion.py` | `test_py_fusion.py` | 16 | 五类合并优先级、次级异常拼接、配对窗口、FusionLog 迟到配对、落盘字段、1970 时间单位回归 |
| `test_type2_veto_real.py` | `test_py_type2_veto_real.py` | 3（`@pytest.mark.slow`） | 真实 PLC 回放：长进线炉（>180s）亮光否决 / 无亮光必报 / 亮光流不可用不判 |

> 备注：`test_type2_veto_real.py` 原脚本在数据文件缺失时会 `sys.exit(0)`（模块级），
> 若该文件被直接 import 会中断 pytest 进程；当前仓库有数据文件，无影响。
> 数据缺失场景由包装侧 `pytest.skip` 兜底（但原脚本的 sys.exit 会先触发，属已知小瑕疵，记录不修）。

## 三、脚本式、可收集模块但暂无 test 函数（纯 stdlib，建议第二批）

导入无报错（不装 cv2 也能 import），但没有 `test_*` 函数 → pytest 收集 0 例，不影响全绿。

| 文件 | 跳过原因 / 第二批备注 |
|---|---|
| `test_analysis_restart.py` | 脚本式；停/再启后 base 墙钟重置回归，stdlib，可包装 |
| `test_loggers.py` | 脚本式；FrameCsv/EventLog 落盘，stdlib + tempfile，可包装 |
| `test_median_filter.py` | 脚本式；中值滤波 + 真实帧回归（`data/real_r51_frames.csv` 在库），可包装 |
| `test_spurious_filter.py` | 脚本式；guard 窗口杂光过滤，合成数据，可包装 |
| `test_real_frame_regression.py` | 脚本式；start_confirm 起判时刻回归（`data/real_r51_frames.csv` 在库），可包装 |
| `test_real_types_345.py` | 脚本式；真实 50 炉 + R51 判定 3/4/5（两个真实 CSV 在库），可包装 |
| `test_retention.py` | 脚本式；adapters.retention 清理策略，stdlib，可包装 |
| `test_roi_store.py` | 脚本式；RoiHistory 落盘，stdlib，可包装 |
| `test_plc_replay.py` | 脚本式；1452 炉真实 PLC 位级/事件级回放（数据在库），**慢**，第二批应标 `slow` |
| `test_fusion_replay.py` | 脚本式；1450 炉融合回放（数据在库），偏慢，第二批可标 `slow` |

## 四、跳过（不收集 / 不改造）及原因

| 文件 | 原因 |
|---|---|
| `test_parity_state_machine.py` | 脚本式；硬依赖**旧仓库** `/home/hasen/vp-lab/vp10.8.3-file/core/reaction_detector.py`（sys.path 写死），本仓库隔离环境跑不了。当前靠宿主机路径恰好可 import（收集 0 例不报错），不进 pytest 化 |
| `parity_e2e.py` | 文件名不匹配 `test_*.py`，pytest 不收集；且 import `cv2` + 旧仓库 `core/`，双依赖 |
| `verify_plot_parity.py` | 文件名不匹配；import `vpmin.adapters.painter`（需 cv2） |
| `e2e_drill.py` | 文件名不匹配；六炉全链路演练脚本（假时钟，可跑），定位是手动演练，不进 CI |
| `tests/browser/*`（cors_embed.js / mock_mgmt.html / roi_edit.js / run.sh） | 前端手验页面，非 Python 测试 |

摄像头：`tests/` 下**没有**任何需要摄像头的测试（全部用合成帧流 / CSV / JSONL 回放驱动）；
`vpmin/adapters/camera.py`（需 cv2）目前只被 `serve.py` 生产链路引用。

## 五、发现的代码问题（只记录，不修）

1. **测试注释瑕疵**（非业务代码）：`test_heat_tracker.py` 第 5 节 check 标签写「239s 时还不报」，
   实际调用 `tr.tick(244.0)`（deadline = feed_start 5.0 + 240 = 245s）。逻辑正确、用例通过，仅标签数字对不上。
2. **业务代码 bug：未发现**。六个原脚本改造前后全部用例均通过，判定（judge）、一炉状态机（heat）、
   融合（fusion）、runner 分段/merge_gap 行为与 2026-10-29 定的口径一致。

## 六、结果

```
.venv-test/bin/python -m pytest tests -q
→ 87 passed（含 3 例 slow，数据在库直接跑）
.venv-test/bin/python -m pytest tests --collect-only -q | tail -3
→ 87 tests collected
```

收集 87 / 通过 87 / 跳过 0（skip 仅数据缺失时触发，当前 `tests/data/` 齐全）。
