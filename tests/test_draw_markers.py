# -*- coding: utf-8 -*-
"""2026-11-04 出图增强测试：结束标记/按钮标记/底部 time 信息条/回退逻辑。

跑法：/home/hasen/miniconda3/bin/python tests/test_draw_markers.py
"""
import base64
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import cv2
import numpy as np

from vpmin.adapters.painter import CurvePainter
from vpmin.adapters.plot_series import map_point
from vpmin.adapters.sink import JsonlSink
from vpmin.domain.curve import CurveBuffer
from vpmin.domain.models import FrameMetrics
from vpmin.domain.standard import StandardPolicy
from vpmin.domain.state_machine import ReactionStateMachine
from vpmin.domain.trigger import Thresholds
from vpmin.runner import ReactionRunner

PLOT = (82, 30, 928, 540 - 60)   # x_left, y_top, x_right, y_bottom（与 plot_render 一致）


def decode(uri):
    return cv2.imdecode(
        np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8), 1)


def gen_points(t0, t1, step=0.1, light_lo=None, light_hi=None):
    """基线 60 灰度；light 区间抬到 180。"""
    pts = []
    t = t0
    while t <= t1:
        g = 180.0 if (light_lo is not None and light_lo <= t <= light_hi) else 60.0
        pts.append((round(t, 3), g))
        t += step
    return pts


def x_of(t, t0, t1):
    x, _ = map_point(t, 0, t0, t1, 0, 1, PLOT)
    return int(round(x))


def count_color(img, x, color_test, y0=40, y1=470):
    col = img[y0:y1, max(0, x - 1):x + 2]
    mask = color_test(col.reshape(-1, 3))
    return int(mask.sum())


IS_RED = lambda v: (v[:, 2] > 150) & (v[:, 0] < 100) & (v[:, 1] < 100)
IS_GOLD = lambda v: (v[:, 2] > 140) & (v[:, 2] < 200) & (v[:, 1] > 110) & (v[:, 1] < 170) & (v[:, 0] > 30) & (v[:, 0] < 100)
IS_BLUE = lambda v: (v[:, 0] > 205) & (v[:, 1] > 115) & (v[:, 1] < 170) & (v[:, 2] < 70)   # 按钮深蓝（235,140,25），排除曲线蓝（190,110,30）


def test_a_valid_button():
    """有效按钮（gap 45s）：960×580 + 红/金/蓝三线 + 信息条"""
    painter = CurvePainter()
    pts = gen_points(43, 195, light_lo=100, light_hi=190)
    uri = painter.draw(pts, 100.0, 190.0, duration=90.0, button_t=55.0, draw_start=53.0)
    img = decode(uri)
    assert img.shape[0] == 580, "画布应 960x580: %s" % (img.shape,)
    assert img.shape[1] == 960
    # 底部 40px 是深色条
    bar = img[540:580]
    assert bar.mean() < 100, "信息条应是深色: mean=%s" % bar.mean()
    # 信息条上有文字（像素方差明显）
    assert bar.std() > 20, "信息条应有文字"
    # 三条竖线（横轴起点=trim 后的首点 53s，不是 43s）
    x_red, x_gold, x_blue = x_of(100, 53, 195), x_of(190, 53, 195), x_of(55, 53, 195)
    n_red = count_color(img, x_red, IS_RED)
    n_gold = count_color(img, x_gold, IS_GOLD)
    n_blue = count_color(img, x_blue, IS_BLUE)
    assert n_red > 200, "开始红缺失: %d" % n_red
    assert n_gold > 150, "结束金缺失: %d" % n_gold
    assert n_blue > 150, "按钮蓝缺失: %d" % n_blue
    # 截断生效：53s 之前的点没画（x< x_of(53) 区域曲线应无蓝线点）
    print("A valid_button PASS  red=%d gold=%d blue=%d" % (n_red, n_gold, n_blue))
    return img


def test_b_no_button():
    """无按钮：回退 start-5 起点，无蓝线，信息条仍在"""
    painter = CurvePainter()
    pts = gen_points(95, 195, light_lo=100, light_hi=190)
    uri = painter.draw(pts, 100.0, 190.0, duration=90.0,
                       button_t=None, draw_start=95.0)
    img = decode(uri)
    assert img.shape[0] == 580
    # 全图不应有按钮蓝线（扫描所有列）
    total_blue = 0
    for x in range(PLOT[0], PLOT[2], 4):
        total_blue += count_color(img, x, IS_BLUE)
    assert total_blue < 40, "不应有按钮线: %d" % total_blue
    print("B no_button PASS (blue=%d)" % total_blue)
    return img


def test_c_legacy():
    """不给 duration：960×540 旧画布（兼容老调用）"""
    painter = CurvePainter()
    pts = gen_points(0, 50, light_lo=10, light_hi=40)
    uri = painter.draw(pts, 10.0, 40.0)
    img = decode(uri)
    assert img.shape[0] == 540, "旧调用应 960x540: %s" % (img.shape,)
    print("C legacy PASS")
    return img


def test_d_runner_commit_button():
    """runner 级：button_wall 有效 → 图里带按钮线 + 时长条；记录字段不变"""
    class Probe:
        def measure(self, frame, elapsed):
            light = 100.0 <= elapsed <= 160.0
            # end_pct = 结束像素占比：亮时 0，灭后拉高才判结束（同 Thresholds.end_ok 语义）
            return FrameMetrics(elapsed=elapsed, mean_gray=180.0 if light else 60.0,
                                start_pct=70.0 if light else 0.0,
                                end_pct=0.0 if elapsed <= 160.0 else 100.0)

    out = "/tmp/drawtest-sink"
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(os.path.join(out, f))
    runner = ReactionRunner(
        probe=Probe(),
        thresholds=Thresholds(55.0, 55.0),
        machine=ReactionStateMachine(0.2, 4.0),
        standard=StandardPolicy((60.0, 100.0)),
        curve=CurveBuffer(preroll_sec=90.0),
        painter=CurvePainter(),
        sink=JsonlSink(out, embed_image=True),
        counter=_Counter(),
        merge_gap=0.0,
        min_duration=20.0,
    )
    # 按钮在亮光前 45s（base 时刻 + 55s；亮光 100s 起）
    runner.button_wall = runner.base.timestamp() + 55.0
    for i in range(0, 22000):
        runner.process(None, i / 10.0)
    # runner 只 build，写盘是 analysis 层的活：这里手动 drain + write
    for record, end_el, end_m in runner.drain():
        runner.sink.write(record)
    records = []
    for p in os.listdir(out):
        if p == "reactions.jsonl":
            for line in open(os.path.join(out, p), encoding="utf-8"):
                if line.strip():
                    import json
                    records.append(json.loads(line))
    assert len(records) == 1, "应落 1 条: %d" % len(records)
    rec = records[0]
    # actual_spheroidization_time 是 HH:MM:SS.ss 字符串，解析成秒
    h, m, s = rec["actual_spheroidization_time"].split(":")
    dur = int(h) * 3600 + int(m) * 60 + float(s)
    assert 55 < dur < 70, "时长应≈60s: %s" % dur
    assert rec.get("abnormal_type") in (0, 3, 4, 6), rec
    img = decode(rec["spheroidization_image"])
    assert img.shape[0] == 580, "落盘图应带信息条: %s" % (img.shape,)
    t0, t1 = 43.0, 220.0   # 曲线实际从 preroll 起点开始（本用例 10s）
    # 直接扫描蓝色像素总数 > 100（按钮线存在）
    total_blue = 0
    for x in range(PLOT[0], PLOT[2], 3):
        total_blue += count_color(img, x, IS_BLUE)
    assert total_blue > 100, "落盘图应有按钮线: %d" % total_blue
    print("D runner_commit_button PASS  dur=%s blue=%d" % (dur, total_blue))
    return img


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n

    def rollback(self):
        self.n -= 1
        return self.n


def main():
    imgs = {}
    imgs["a"] = test_a_valid_button()
    imgs["b"] = test_b_no_button()
    imgs["c"] = test_c_legacy()
    imgs["d"] = test_d_runner_commit_button()
    out = "/tmp/drawtest-out"
    os.makedirs(out, exist_ok=True)
    for k, v in imgs.items():
        cv2.imwrite(os.path.join(out, "case_%s.jpg" % k), v,
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("全部 PASS，样图 ->", out)


if __name__ == "__main__":
    main()
