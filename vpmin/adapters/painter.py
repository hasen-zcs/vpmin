"""曲线图绘制：直接复用原版 vp10.8.3 的绘图算法，保证图与生产一模一样。

plot_series.py / plot_render.py 是从 vp10.8.3 的 core/gray_series.py、
core/gray_plot.py 原样移植（只改了 import），所以像素级一致：
960×540、蓝线+淡色填充、浅灰像素下限虚线、鲜红开始三件套（粗线+三角+空心圆点）、
灰色结束虚线、网格+刻度、JPEG 质量 92、默认不写信息文字。

唯一区别：原版的 GraySeries 由 engine 直接维护；这里每次出图时用曲线点集重建一个，
所以"抽稀策略"仍以 CurveBuffer 的上限为准（画图时再按 4000 点二次抽稀，同原版）。
"""

import base64

import cv2

from .plot_render import render
from .plot_series import GraySeries


class CurvePainter:
    """灰度-时间曲线（原版算法），返回 data URI。"""

    def __init__(self, quality: int = 92, autoscale: bool = True,
                 max_draw_points: int = 4000):
        self.quality = int(quality)
        self.autoscale = bool(autoscale)
        self.max_draw_points = int(max_draw_points)

    def draw(self, points, start_t=None, end_t=None, start_pixel=None) -> str:
        series = GraySeries()
        series.extend(points or [])
        meta = {"t_start": start_t, "t_end": end_t}
        if start_pixel is not None:
            meta["start_pixel"] = start_pixel
        try:
            img = render(series, meta, autoscale=self.autoscale,
                         max_draw_points=self.max_draw_points)
        except Exception:                     # 画图失败绝不能影响分析
            return ""
        return self._encode(img)

    def _encode(self, img) -> str:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(buf).decode()
