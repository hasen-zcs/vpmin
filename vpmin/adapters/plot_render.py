"""
灰度曲线画图（guidemon10.8）—— vpmin 精简移植版

本文件由 vp10.8.3 的 core/gray_plot.py 原样移植后**只做减法**，绘图主流程一字未改，
因此出图与原版逐像素一致（4 组固定样例 PNG diff 必须 0 差异，见 tests/verify_plot_parity.py）。

相对原版砍掉的三块（都是本项目永不触达的路径，删了不影响任何输出）：
  1. show_info 信息块：标题 / run 编号 / 底部明细 / 灰度范围 —— 原版默认关闭，
     本版根本没有开关，永远不画。连带 _render_info / _meta_lines / _fmt_duration /
     _fmt_number / _result_text 与两个 WITH_INFO 边距常量一起删。
  2. save_image()：本项目用 imencode + 自己写文件（painter 负责），从不调用。
  3. COLOR_BG 常量：原版也没用上（背景直接 np.full 填白）。

保留的仍然是原版行为：
960×540 + 白底 + 网格 + 刻度 + 坐标轴名 + 蓝曲线(2px)+淡色填充 + 浅灰像素下限虚线
+ 鲜红开始三件套（3px 实线 / 顶部三角 / 曲线上空心圆点）+ 灰色结束虚线（6/6 细虚线）。

============================

把 core/gray_series.py 里的 GraySeries 画成 **JPG**，存到 output/image/。

**用 cv2 画，不引入 matplotlib** —— 服务器上 pip 装不上东西时照样能用，
而且 cv2 本来就是这个项目的硬依赖，等于零新增依赖。

10.6 相对 10.5 的三点改动
-------------------------
1. **出图尺寸 1000×470 → 960×540**（精确 16:9）。
2. **默认不再在图上写信息文字**：标题、run 编号、底部信息块、
   「start/end pixel」文字、灰度范围文字全部去掉，
   图上只剩 —— 坐标轴名 + 刻度数字 + 网格 + 曲线。
   （要看信息就设 `GRAY_CURVE_SHOW_INFO=1`，那套文字会回来。）
3. **反应开始处加醒目标注**：3px 鲜红实线 + 顶部向下三角箭头
   + 曲线上的空心圆点。反应结束仍用细灰虚线，只作背景参考。

⚠ 图上只能用 ASCII 文字
-----------------------
cv2.putText 不支持中文（画出来是 ???），而除了 putText 没有别的办法
在不引入新依赖的前提下写中文。默认保留的那两处文字（Time (s) / Mean Gray）
本来就是英文；`GRAY_CURVE_SHOW_INFO=1` 打开的信息块也是英文。

曲线本身是**平均灰度**（0~255），而"开始/结束阈值"是**像素占比%**，
两者不是同一个量纲，所以阈值不画成横线；画成横线的是
"开始/结束像素下限"（默认 240）—— 那个是灰度值，同量纲。
"""

from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from .plot_series import (
    GraySeries,
    gray_view_range,
    map_point,
    ticks,
)

# ============================================================
# 画布尺寸与边距
# ============================================================

# 960×540 = 精确 16:9
WIDTH = 960
HEIGHT = 540

# 不带信息文字时的边距。
# 左边要放得下 y 轴刻度数字，下面要放得下 x 轴刻度数字 + "Time (s)"。
MARGIN_LEFT = 82
MARGIN_RIGHT = 32
MARGIN_TOP = 30
MARGIN_BOTTOM = 60

# ============================================================
# 配色（BGR）
# ============================================================

COLOR_FRAME = (170, 170, 170)
COLOR_GRID = (232, 232, 232)
COLOR_AXIS = (90, 90, 90)
COLOR_TEXT = (60, 60, 60)
COLOR_DIM = (140, 140, 140)
COLOR_CURVE = (190, 110, 30)          # 蓝：曲线本体
COLOR_FILL = (245, 225, 200)          # 曲线下方淡色填充
COLOR_PIXEL_THR = (210, 210, 210)     # 浅灰：开始/结束像素下限
COLOR_END_MARK = (150, 150, 150)      # 灰：反应结束（背景参考）
COLOR_START_MARK = (30, 30, 235)      # 鲜红：反应开始，最醒目

FONT = cv2.FONT_HERSHEY_SIMPLEX

# ============================================================
# 小工具
# ============================================================

def _dashed_line(
    img: np.ndarray,
    p1: Tuple[int, int],
    p2: Tuple[int, int],
    color,
    thickness: int = 1,
    dash: int = 9,
    gap: int = 7,
):
    """画虚线（cv2 没有现成的虚线）。"""

    x1, y1 = p1
    x2, y2 = p2

    length = float(np.hypot(x2 - x1, y2 - y1))

    if length <= 0:
        return

    step = dash + gap
    n = int(length // step) + 1

    for i in range(n):
        a = i * step / length
        b = min(1.0, (i * step + dash) / length)

        if a >= 1.0:
            break

        pa = (int(round(x1 + (x2 - x1) * a)), int(round(y1 + (y2 - y1) * a)))
        pb = (int(round(x1 + (x2 - x1) * b)), int(round(y1 + (y2 - y1) * b)))

        cv2.line(img, pa, pb, color, thickness, cv2.LINE_AA)


def _put(
    img: np.ndarray,
    text: str,
    org: Tuple[int, int],
    scale: float = 0.45,
    color=COLOR_TEXT,
    thickness: int = 1,
):
    cv2.putText(img, text, org, FONT, scale, color, thickness, cv2.LINE_AA)


def _meta_time(meta: Dict, key: str) -> Optional[float]:
    """从 meta 里取一个时间点（秒），非法返回 None。"""

    value = meta.get(key)

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================================
# 画图
# ============================================================

def render(
    series: GraySeries,
    meta: Optional[Dict] = None,
    width: int = WIDTH,
    height: int = HEIGHT,
    autoscale: bool = True,
    max_draw_points: int = 4000,
) -> np.ndarray:
    """
    把曲线画成一张图（BGR ndarray）。

    参数：
        series: GraySeries
        meta:   附注信息；只有 t_start / t_end / start_pixel 影响画面
        autoscale: y 轴是否自适应（False = 固定 0~255）
        max_draw_points: 最多画多少个点（多了先抽稀）

    画面构成：
        白底 + 网格 + 坐标轴名 + 刻度数字 + 曲线（带淡色填充）
        + 浅灰虚线的像素下限横线
        + **鲜红粗实线的反应开始标注**（含顶部三角与曲线上的圆点）
        + 灰色细虚线的反应结束参考线
    """

    meta = dict(meta or {})

    img = np.full((height, width, 3), 255, dtype=np.uint8)

    m_top = MARGIN_TOP
    m_bottom = MARGIN_BOTTOM

    x_left = MARGIN_LEFT
    x_right = width - MARGIN_RIGHT
    y_top = m_top
    y_bottom = height - m_bottom
    plot = (x_left, y_top, x_right, y_bottom)

    # ---------------- 坐标范围 ----------------
    t0, t1, gmin, gmax = series.bounds()

    if t1 <= t0:
        t1 = t0 + 1.0

    g0, g1 = gray_view_range(gmin, gmax, autoscale=autoscale)

    # ---------------- 网格与刻度 ----------------
    for value in ticks(g0, g1, 5):
        _, y = map_point(t0, value, t0, t1, g0, g1, plot)

        if y_top <= y <= y_bottom:
            cv2.line(img, (x_left, y), (x_right, y), COLOR_GRID, 1)
            _put(img, f"{value:.0f}", (x_left - 34, y + 5), 0.42, COLOR_DIM)

    for value in ticks(t0, t1, 6):
        x, _ = map_point(value, g0, t0, t1, g0, g1, plot)

        if x_left <= x <= x_right:
            cv2.line(img, (x, y_top), (x, y_bottom), COLOR_GRID, 1)

            label = f"{value:.0f}"
            size = cv2.getTextSize(label, FONT, 0.42, 1)[0]
            _put(img, label, (x - size[0] // 2, y_bottom + 20), 0.42, COLOR_DIM)

    # ---------------- 像素下限横线（同量纲才画）----------------
    pixel_thr = meta.get("start_pixel")

    if pixel_thr is not None:
        try:
            threshold = float(pixel_thr)
        except (TypeError, ValueError):
            threshold = None

        if threshold is not None and g0 <= threshold <= g1:
            _, y = map_point(t0, threshold, t0, t1, g0, g1, plot)
            _dashed_line(img, (x_left, y), (x_right, y), COLOR_PIXEL_THR)

    # ---------------- 曲线 ----------------
    draw_points = series.thinned(max_draw_points)

    if draw_points:
        t_lo, t_hi, _, _ = series.bounds()

        pixel_points = [
            map_point(t, g, t_lo, t_hi if t_hi > t_lo else t_lo + 1.0,
                      g0, g1, plot)
            for (t, g) in draw_points
        ]

        # 曲线下方淡色填充，读起来更清楚
        if len(pixel_points) >= 2:
            polygon = (
                [(x_left, y_bottom)]
                + pixel_points
                + [(pixel_points[-1][0], y_bottom)]
            )

            overlay = img.copy()
            cv2.fillPoly(overlay, [np.array(polygon, dtype=np.int32)], COLOR_FILL)
            cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)

        cv2.polylines(
            img,
            [np.array(pixel_points, dtype=np.int32).reshape((-1, 1, 2))],
            False,
            COLOR_CURVE,
            2,
            cv2.LINE_AA,
        )

    # ---------------- 边框（先画，免得压住下面的标注）----------------
    cv2.rectangle(img, (x_left, y_top), (x_right, y_bottom), COLOR_FRAME, 1)

    # ---------------- 反应结束：细灰虚线，只作背景参考 ----------------
    t_end = _meta_time(meta, "t_end")

    if t_end is not None and t0 <= t_end <= t1:
        x, _ = map_point(t_end, g0, t0, t1, g0, g1, plot)
        _dashed_line(img, (x, y_top), (x, y_bottom), COLOR_END_MARK, 1, 6, 6)

    # ---------------- 反应开始：显著标注 ----------------
    #
    # 这是 10.6 的重点：一眼就能看出"反应从哪一刻开始"。
    # 配合前置采样（默认开始前 5 秒），反应前后的灰度变化一目了然。
    # 三件套：粗实线 + 顶部向下三角 + 曲线上的空心圆点。
    t_start = _meta_time(meta, "t_start")

    if t_start is not None and t0 <= t_start <= t1:
        x, _ = map_point(t_start, g0, t0, t1, g0, g1, plot)

        # ① 3px 鲜红实线，贯穿整个绘图区
        cv2.line(img, (x, y_top), (x, y_bottom), COLOR_START_MARK, 3, cv2.LINE_AA)

        # ② 顶部向下的实心三角，像个指针
        half = 10
        cv2.fillPoly(
            img,
            [np.array(
                [
                    (x - half, y_top - 3),
                    (x + half, y_top - 3),
                    (x, y_top + 13),
                ],
                dtype=np.int32,
            )],
            COLOR_START_MARK,
        )

        # ③ 曲线在开始时刻的采样点：先铺白底再画红圈，
        #    保证它落在曲线或淡色填充上都看得见
        gray_at_start = series.gray_at(t_start)

        if gray_at_start is not None:
            cx, cy = map_point(t_start, gray_at_start, t0, t1, g0, g1, plot)

            cv2.circle(img, (cx, cy), 7, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(img, (cx, cy), 7, COLOR_START_MARK, 3, cv2.LINE_AA)

    # ---------------- 坐标轴名（始终保留）----------------
    _put(img, "Time (s)", (x_right - 56, y_bottom + 42), 0.46, COLOR_AXIS)

    axis_label = "Mean Gray"
    size = cv2.getTextSize(axis_label, FONT, 0.46, 1)[0]
    _put(img, axis_label, (x_left - size[0] // 2 - 26, y_top - 8), 0.46, COLOR_AXIS)

    return img
