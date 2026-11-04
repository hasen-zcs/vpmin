"""双机位回归（--source2 查看机位）。

10-28 第二轮就有的能力，11-04 影子实接双机位前补自动化测试：
- Runtime.camera() 下标映射（1→cameras[0]、2→cameras[1]、缺省/越界→分析位）
- HTTP 层 /video_feed1 与 /video_feed2 各出各的机位画面（两帧 md5 不同）
- 只配 1 个机位时 /video_feed2 回落 1 号画面（老别名行为不变）
"""

import threading
import time
import urllib.request

import cv2
import numpy as np
import pytest

from vpmin.adapters.camera import Camera
from vpmin.app.runtime import Runtime
from vpmin.server.http_api import ApiServer


def _make_video(path, color_bgr, frames=40, size=(64, 48), fps=10.0):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for _ in range(frames):
        w.write(np.full((size[1], size[0], 3), color_bgr, dtype=np.uint8))
    w.release()
    assert path.exists() and path.stat().st_size > 0


def _first_frame_jpeg(url: str) -> bytes:
    """从 MJPEG 流里抓第一帧 JPEG（测试帧 64×48，几十 KB 内肯定读得到）。"""
    with urllib.request.urlopen(url, timeout=10) as r:
        buf = r.read(65536)
    start = buf.find(b"\r\n\r\n") + 4
    end = buf.find(b"\r\n--", start)
    return buf[start:end if end > start else None]


def _build_runtime(tmp_path, sources):
    cams = []
    for i, (src, label) in enumerate(sources, start=1):
        cam = Camera(str(src), label, publish_fps=10.0)
        cam.start()
        cams.append(cam)
    rt = Runtime(store=None, cameras=cams, analyze_id=1, analysis=None,
                 probe=None, runner=None, standard=None, image_store=None,
                 event_log=None, csv_path=str(tmp_path / "f.csv"),
                 images_dir=str(tmp_path / "img"))
    return rt, cams


def _start_api(rt, static_dir):
    srv = ApiServer(rt, static_dir, host="127.0.0.1", port=0)
    port = srv.httpd.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    return srv, port


def _stop_runtime(rt, cams):
    # 测试里没装分析循环，只停取流线程
    for c in cams:
        c.stop()


def test_camera_index_mapping(tmp_path):
    _make_video(tmp_path / "a.mp4", (0, 0, 200))
    _make_video(tmp_path / "b.mp4", (200, 0, 0))
    rt, cams = _build_runtime(tmp_path, [(tmp_path / "a.mp4", "#01"), (tmp_path / "b.mp4", "#02")])
    try:
        assert rt.camera(1) is cams[0]
        assert rt.camera(2) is cams[1]
        assert rt.camera(0) is cams[0]          # 缺省=分析位
        assert rt.camera(99) is cams[0]          # 越界回落分析位
    finally:
        _stop_runtime(rt, cams)


def test_video_feed2_serves_second_camera(tmp_path):
    _make_video(tmp_path / "red.mp4", (0, 0, 200))    # BGR 红
    _make_video(tmp_path / "blue.mp4", (200, 0, 0))   # BGR 蓝
    rt, cams = _build_runtime(tmp_path,
                              [(tmp_path / "red.mp4", "#01"), (tmp_path / "blue.mp4", "#02")])
    srv, port = _start_api(rt, tmp_path / "static")
    try:
        time.sleep(0.8)  # 等两路都出第一帧
        f1 = _first_frame_jpeg(f"http://127.0.0.1:{port}/video_feed1")
        f2 = _first_frame_jpeg(f"http://127.0.0.1:{port}/video_feed2")
        assert f1.startswith(b"\xff\xd8"), "feed1 不是 JPEG"
        assert f2.startswith(b"\xff\xd8"), "feed2 不是 JPEG"
        assert f1 != f2, "两路画面相同，feed2 没接到第 2 机位"
        # 均值颜色方向校验：feed1 偏红（BGR 蓝通道高），feed2 偏蓝
        arr1 = cv2.imdecode(np.frombuffer(f1, np.uint8), cv2.IMREAD_COLOR)
        arr2 = cv2.imdecode(np.frombuffer(f2, np.uint8), cv2.IMREAD_COLOR)
        assert arr1 is not None and arr2 is not None
        assert arr1[:, :, 2].mean() > arr1[:, :, 0].mean()   # 红 > 蓝
        assert arr2[:, :, 0].mean() > arr2[:, :, 2].mean()   # 蓝 > 红
    finally:
        srv.httpd.shutdown()
        _stop_runtime(rt, cams)


def test_video_feed2_falls_back_to_cam1_when_single_source(tmp_path):
    _make_video(tmp_path / "red.mp4", (0, 0, 200))
    rt, cams = _build_runtime(tmp_path, [(tmp_path / "red.mp4", "#01")])
    srv, port = _start_api(rt, tmp_path / "static")
    try:
        time.sleep(0.8)
        f1 = _first_frame_jpeg(f"http://127.0.0.1:{port}/video_feed1")
        f2 = _first_frame_jpeg(f"http://127.0.0.1:{port}/video_feed2")
        assert f1.startswith(b"\xff\xd8") and f2.startswith(b"\xff\xd8")
        # 单机位时两路同源（cam 2 越界回落 cameras[0]），画面允许同帧
        assert rt.camera(2) is cams[0]
    finally:
        srv.httpd.shutdown()
        _stop_runtime(rt, cams)
