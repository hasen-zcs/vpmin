"""反应状态机：只负责状态迁移，不碰像素、不碰判定。

六态与参考实现 完全一致；COOLDOWN 是迟滞，防止同一反应的尾巴被判成第二次开始。
"""

from enum import Enum

from .models import Transition


class State(str, Enum):
    WAITING = "WAITING"
    CANDIDATE_START = "CANDIDATE_START"
    STARTED = "STARTED"
    CANDIDATE_END = "CANDIDATE_END"
    ENDED = "ENDED"
    COOLDOWN = "COOLDOWN"


class ReactionStateMachine:
    """逐帧推进；确认时长取「条件成立持续 >= 阈值」的那一帧。"""

    def __init__(self, start_confirm: float = 1.0, end_confirm: float = 3.0):
        self.start_confirm = float(start_confirm)
        self.end_confirm = float(end_confirm)
        self.reset()

    def reset(self) -> None:
        self.state = State.WAITING
        self.start_cond = None      # 开始条件首次满足时刻
        self.end_cond = None        # 结束条件首次满足时刻
        self.start_t = None         # 确认开始的帧时刻
        self.end_t = None           # 确认结束的帧时刻

    def rearm(self) -> bool:
        """ENDED → COOLDOWN（保留起止时刻给界面显示）。幂等。"""
        if self.state is not State.ENDED:
            return False
        self.state = State.COOLDOWN
        self.start_cond = self.end_cond = None
        return True

    def advance(self, now: float, start_ok: bool, end_ok: bool) -> Transition:
        started = ended = False
        s = self.state

        if s is State.WAITING:
            self.end_cond = None
            if start_ok:
                self.start_cond, self.state = now, State.CANDIDATE_START

        elif s is State.CANDIDATE_START:
            if start_ok:
                if self.start_cond is None:
                    self.start_cond = now
                if now - self.start_cond >= self.start_confirm:
                    self.state, self.start_t, self.end_cond = State.STARTED, now, None
                    started = True
            else:
                self.start_cond, self.state = None, State.WAITING

        elif s is State.STARTED:
            self.start_cond = None
            if end_ok:
                self.end_cond, self.state = now, State.CANDIDATE_END
            else:
                self.end_cond = None

        elif s is State.CANDIDATE_END:
            if end_ok:
                if self.end_cond is None:
                    self.end_cond = now
                if now - self.end_cond >= self.end_confirm:
                    self.state, self.end_t = State.ENDED, now
                    ended = True
            else:
                self.end_cond, self.state = None, State.STARTED

        elif s is State.COOLDOWN:
            self.end_cond = None
            if not start_ok:
                self.state = State.WAITING

        # ENDED：停在原地，等外部 rearm()（与原版一致）
        return Transition(self.state.value, started, ended)

    @property
    def duration(self) -> float:
        if self.start_t is None:
            return 0.0
        end = self.end_t if self.end_t is not None else self.start_t
        return max(0.0, end - self.start_t)
