"""
ROI 平均灰度时间序列（guidemon10.8）
====================================

把一个球化反应过程中 ROI 的平均灰度按时间记下来，供两处使用：

    1. 网页上"实时曲线"（x = 时间，y = 平均灰度）；
    2. 每个反应结束后画成 JPG 存到 output/image/（画图在 core/gray_plot.py）。

本模块是**纯 Python**，不 import cv2 / numpy ——
所以它能在没有 OpenCV 的机器上单测（见 tools/check_gray_series.py）。
画图那部分才需要 cv2，单独放在 gray_plot.py 里。

两类东西（原版的 RingBuffer 已删：本版前置基线由 domain/curve.py 的 CurveBuffer 负责）
--------
GraySeries   一个反应的完整采样。
几何工具     nice_step / ticks / map_point —— 把 (t, 灰度) 映射到画布像素。
             放在这里而不是 gray_plot.py，是为了能离线验证映射算得对。
"""

from typing import List, Optional, Sequence, Tuple

# 灰度取值范围（8 位灰度图）
GRAY_MIN = 0.0
GRAY_MAX = 255.0


# ============================================================
# 环形缓冲：给下一个反应留前置上下文
# ============================================================

class GraySeries:
    """
    单个球化反应过程中的 (时间, 平均灰度) 序列。

    时间基准与分析引擎一致：分析开始那一刻为 0，单位秒。
    """

    def __init__(self, max_points: int = 20000, label: str = ""):
        # 上限是为了防止"最长分析时长"设得很大时把内存吃穿。
        # 到上限后按步长抽稀（不是直接丢新的），保证曲线形状不失真。
        self.max_points = max(2, int(max_points))
        self.label = label

        self._points: List[Tuple[float, float]] = []
        self.dropped = 0                 # 被抽稀丢掉的点数
        self.partial = False             # True = 分析被中途停掉，反应没判到结束

    # ---------------- 写入 ----------------

    def add(self, t: float, gray: float):
        self._points.append((float(t), float(gray)))

        if len(self._points) > self.max_points:
            self._thin_here()

    def extend(self, points: Sequence[Sequence[float]]):
        for item in points:
            self.add(float(item[0]), float(item[1]))

    def _thin_here(self):
        """抽稀到 max_points 的一半，保留首尾。"""

        target = max(2, self.max_points // 2)
        self._points = thin(self._points, target)
        self.dropped += 1

    def clear(self):
        self._points.clear()
        self.dropped = 0
        self.partial = False

    # ---------------- 读取 ----------------

    @property
    def points(self) -> List[Tuple[float, float]]:
        return list(self._points)

    def __len__(self) -> int:
        return len(self._points)

    def bounds(self) -> Tuple[float, float, float, float]:
        """
        返回 (t0, t1, gray_min, gray_max)。

        空序列返回 (0, 0, 0, 0)。
        """

        if not self._points:
            return (0.0, 0.0, 0.0, 0.0)

        times = [p[0] for p in self._points]
        grays = [p[1] for p in self._points]

        return (min(times), max(times), min(grays), max(grays))

    def duration(self) -> float:
        t0, t1, _, _ = self.bounds()

        return max(0.0, t1 - t0)

    def thinned(self, max_points: int) -> List[Tuple[float, float]]:
        """按需抽稀（画图前调用，避免一次画几万个点）。"""

        return thin(self._points, max_points)

    def gray_at(self, t: float) -> Optional[float]:
        """
        离 t 最近的那个采样点的灰度值；空序列返回 None。

        10.6 新增：画图时要在"判到开始"那一刻的曲线上标一个圆点，
        需要知道曲线在那一点的纵坐标。

        说明：判到开始那一帧本身就被采进了曲线 —— engine.process() 里
        started_now 的处理在前、采样在后，两者用的是同一个 elapsed，
        所以这里取到的就是那一刻的真实灰度，不是插值。
        """

        if not self._points:
            return None

        try:
            target = float(t)
        except (TypeError, ValueError):
            return None

        # 点数不多（上限 max_points），一次线性扫描足够；
        # 每张图只调用一次，不值得为它维护有序索引。
        nearest = min(self._points, key=lambda point: abs(point[0] - target))

        return nearest[1]


# ============================================================
# 抽稀
# ============================================================

def thin(
    points: Sequence[Tuple[float, float]],
    max_points: int,
) -> List[Tuple[float, float]]:
    """
    等步长抽稀，**保留首尾**。

    首尾必须留：它们是曲线的两个端点，丢了形状就对不上了。

    边界：
        max_points >= n  → 原样返回
        max_points == 2  → [首, 尾]
        max_points == 1  → [尾]
        max_points <= 0  → [尾]（预算非法也不能返回空）
    """

    n = len(points)

    if n == 0:
        return []

    if max_points <= 0:
        return [points[-1]]

    if n <= max_points:
        return list(points)

    if max_points == 1:
        return [points[-1]]

    step = (n - 1) / (max_points - 1)
    out: List[Tuple[float, float]] = []
    last_index = -1

    for i in range(max_points):
        index = int(round(i * step))

        if index == last_index:
            continue

        out.append(points[index])
        last_index = index

    # 保险：确保最后一个点确实是尾点
    if out and out[-1] != points[-1]:
        out[-1] = points[-1]

    return out


# ============================================================
# 坐标轴刻度
# ============================================================

def nice_step(span: float, target_ticks: int = 5) -> float:
    """
    取一个"好看"的刻度间隔：1 / 2 / 5 × 10^n。

    例：span=95、target=5 → 20；span=4.8 → 1；span=230 → 50。
    """

    span = abs(float(span))

    if span <= 0 or target_ticks <= 0:
        return 1.0

    rough = span / target_ticks
    magnitude = 10.0 ** _floor_log10(rough)
    normalized = rough / magnitude

    if normalized <= 1.0:
        nice = 1.0
    elif normalized <= 2.0:
        nice = 2.0
    elif normalized <= 5.0:
        nice = 5.0
    else:
        nice = 10.0

    return nice * magnitude


def _floor_log10(value: float) -> int:
    """向下取整的 log10，不用 math 是为了保持纯整数运算的可预测性。"""

    import math

    return int(math.floor(math.log10(value)))


def ticks(low: float, high: float, target: int = 5) -> List[float]:
    """在 [low, high] 内生成刻度值（含端点的近似值）。"""

    if high <= low:
        return [float(low)]

    step = nice_step(high - low, target)

    if step <= 0:
        return [float(low)]

    start = float(int(low / step) * step)
    out: List[float] = []
    value = start
    limit = high + step * 0.5

    while value <= limit:
        if value >= low - step * 0.5:
            out.append(round(value, 6))
        value += step

    return out


# ============================================================
# 像素映射
# ============================================================

def map_point(
    t: float,
    gray: float,
    t0: float,
    t1: float,
    g0: float,
    g1: float,
    plot: Tuple[int, int, int, int],
) -> Tuple[int, int]:
    """
    把 (t, gray) 映射到画布像素坐标。

    plot = (x_left, y_top, x_right, y_bottom)，
    **y 轴向上为正**（灰度大 = 画得高），所以内部做了翻转。

    退化情形（t0 == t1 或 g0 == g1）返回区间的中点，避免除零。
    """

    x_left, y_top, x_right, y_bottom = plot

    if t1 > t0:
        ratio_x = (float(t) - float(t0)) / (float(t1) - float(t0))
    else:
        ratio_x = 0.5

    if g1 > g0:
        ratio_y = (float(gray) - float(g0)) / (float(g1) - float(g0))
    else:
        ratio_y = 0.5

    x = x_left + ratio_x * (x_right - x_left)
    y = y_bottom - ratio_y * (y_bottom - y_top)

    return (int(round(x)), int(round(y)))


def gray_view_range(
    gray_min: float,
    gray_max: float,
    autoscale: bool = True,
    min_span: float = 40.0,
    padding_ratio: float = 0.12,
) -> Tuple[float, float]:
    """
    决定 y 轴显示范围。

    autoscale=False → 固定 0 ~ 255（不同反应的图可以直接比对）。
    autoscale=True  → 自适应，但至少留 min_span 的跨度，
                      否则一条几乎水平的曲线会被放大成噪声。
    """

    if not autoscale:
        return (GRAY_MIN, GRAY_MAX)

    low = float(gray_min)
    high = float(gray_max)

    # 留一点上下边距
    padding = max(1.0, (high - low) * padding_ratio)
    low -= padding
    high += padding

    # 跨度太小就撑开
    if high - low < min_span:
        center = (low + high) / 2.0
        low = center - min_span / 2.0
        high = center + min_span / 2.0

    # 裁到合法灰度范围内
    if low < GRAY_MIN:
        high += (GRAY_MIN - low)
        low = GRAY_MIN

    if high > GRAY_MAX:
        low -= (high - GRAY_MAX)
        high = GRAY_MAX

    low = max(GRAY_MIN, low)
    high = min(GRAY_MAX, high)

    if high <= low:
        return (GRAY_MIN, GRAY_MAX)

    return (low, high)
