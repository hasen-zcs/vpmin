# 任务：写一个「状态机逐帧对拍」测试

目标文件：`tests/test_parity_state_machine.py`（纯 Python，不依赖 cv2/numpy）

## 背景
- 本仓库新实现：`vpmin/domain/state_machine.py` 的 `ReactionStateMachine`（方法 `advance(now, start_ok, end_ok)`，返回 `Transition(state, started, ended)`，属性 `start_t`/`end_t`，方法 `rearm()`）
- 原版参考：`/home/hasen/vp-lab/vp10.8.3-file/core/reaction_detector.py` 的 `ReactionDetector`（方法 `update(now=..., brightness_rate=0.0, highlight_240_percent=start_pct, highlight_end_percent=end_pct)` 返回 dict，含 `state`/`started_now`/`ended_now`/`reaction_start_time`/`reaction_end_time`，方法 `rearm()`）

## 要求（严格照做）
1. 用 `sys.path.insert(0, "/home/hasen/vp-lab/vp10.8.3-file")` 导入原版 `core.reaction_detector.ReactionDetector`，不要复制它的代码。
2. 用 `random.Random(1234)` 生成 **200 条**随机指标序列，每条 **600 帧**：
   - 帧间隔 0.05~0.2 秒，`elapsed` 累加
   - `start_ok` / `end_ok` 不是独立掷骰子，而是「保持当前值若干帧后翻转」（停留 3~120 帧），这样才会真正触发状态迁移
   - 保证 start_ok=True/False 与 end_ok=True/False 四种组合都出现过
3. 每条序列用**同一对确认时长**（从 `[(1.0, 3.0), (0.2, 4.0), (0.0, 0.0)]` 里轮换），两个实现都用这组参数。
4. 逐帧同时推进，断言以下 5 项完全相等：`state`、`started`/`started_now`、`ended`/`ended_now`、`start_t`/`reaction_start_time`、`end_t`/`reaction_end_time`（浮点直接用 `==`，因为两边收到的是同一个 now）。
5. 原版在 ended 的那一帧之后调用 `detector.rearm()`，新实现调用 `machine.rearm()`（同一帧内调用，位置一致）。
6. 出现不一致立即打印：序列编号、帧号、now、start_ok、end_ok、两边的 5 个字段，然后 `raise AssertionError`。
7. 脚本结束打印 `PASS 200 traces x 600 frames`（或实际数字），失败时退出码非 0。
8. 用 `/home/hasen/miniconda3/bin/python` 直接运行 `tests/test_parity_state_machine.py`，必须跑通。

## 纪律
- 不许提问、不许等确认，直接写文件并运行。
- 只新建 `tests/test_parity_state_machine.py`（必要时建 `tests/__init__.py`），**不许改 `vpmin/` 下任何文件**。
- 如果对拍失败，先怀疑自己的测试代码；确实发现新实现与原版语义不同，把差异写进 `tests/PARITY-DIFF.md` 并说明，不要改实现。
