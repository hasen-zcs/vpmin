"""单机位取流：只负责「拿帧 + 出 JPEG」，不参与判定。

- 分析机位：每帧回调 on_frame（由 app 层决定怎么用）
- 观看机位：不传 on_frame，只广播画面
- 断流自动重连（次数可配，0 = 无限重试）

RTSP 低延迟与超时参数对齐原版 vp10.8.3（core/video_source.py）：
  * OPENCV_FFMPEG_CAPTURE_OPTIONS = rtsp_transport;tcp（海康默认走 UDP 会花屏/丢包）
  * CAP_PROP_BUFFERSIZE = 1（最低延迟，避免堆积旧帧）
  * OPEN/READ_TIMEOUT_MSEC（断流时 read 不会永久阻塞）
  * 显式 CAP_FFMPEG 后端
本地视频文件则相反：必须 pop 掉该环境变量，否则 FFmpeg 报错；并按 fps 原速回放，
否则用 time.monotonic 算出的反应时长会被压缩。
"""

import os
import socket
import threading
import time

import cv2

_NET_PREFIXES = ("rtsp://", "rtsps://", "rtmp://", "http://", "https://")


class Camera:
    def __init__(self, url: str, label: str, jpeg_quality: int = 80,
                 publish_fps: float = 10.0, reconnect_attempts: int = 0,
                 reconnect_delay: float = 2.0, on_frame=None, realtime: bool = True,
                 transport: str = "tcp", buffer_size: int = 1,
                 open_timeout_ms: int = 5000, read_timeout_ms: int = 5000):
        self.url = url
        self.label = label
        self.jpeg_quality = int(jpeg_quality)
        self.publish_interval = 1.0 / max(1.0, float(publish_fps))
        self.reconnect_attempts = int(reconnect_attempts)
        self.reconnect_delay = float(reconnect_delay)
        self.on_frame = on_frame
        self.realtime = bool(realtime)      # 文件源按 fps 节流，RTSP 本来就实时
        self.transport = transport or "tcp"
        self.buffer_size = max(1, int(buffer_size))
        self.open_timeout_ms = int(open_timeout_ms)
        self.read_timeout_ms = int(read_timeout_ms)
        self._is_net = str(url).lower().startswith(_NET_PREFIXES)
        self.reconnect_count = 0

        self._frame = None
        self._jpeg = b""
        self._size = (0, 0)
        self._fps = 0.0
        self._frames = 0
        self._connected = False
        self._last_error = ""
        self._message = ""            # 取流状态提示（原版 camera.message）
        self._stop = threading.Event()
        self._thread = None

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # ---------------- 打开 ----------------
    def _prepare_env(self) -> None:
        """建 capture 之前设好 FFmpeg 传输参数（网络流）/ 清掉（文件）。"""
        if self._is_net:
            # stimeout 单位是微秒：不加它，摄像头不可达时 VideoCapture() 会阻塞几十秒，
            # 取流线程整个卡住（页面表现为"没连上也没报错"）——真机实测出来的坑
            micros = max(1000, int(self.open_timeout_ms * 1000))
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                f"rtsp_transport;{self.transport}|stimeout;{micros}|timeout;{micros}")
        else:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

    def _port_reachable(self) -> bool:
        """建流前先探 TCP 端口。

        为什么需要：摄像头断电/断网时 cv2.VideoCapture() 会阻塞十几到几十秒
        （FFmpeg 的 stimeout 只管 RTSP 握手，管不了 TCP connect），
        取流线程整个卡死，界面上表现为"没连上也不报错"。
        预探测用一个 open_timeout 的 connect 就地失败，重连逻辑才能按秒级跑起来。
        """
        if not self._is_net:
            return True
        try:
            host = self.url.split("://", 1)[1].split("/", 1)[0]
            host = host.rsplit("@", 1)[-1]
            port = 554
            if ":" in host:
                host, _, port_text = host.rpartition(":")
                port = int(port_text or 554)
        except (IndexError, ValueError):
            return True                      # 地址自己都不规范，交给 VideoCapture 报错
        try:
            with socket.create_connection((host, port), timeout=self.open_timeout_ms / 1000.0):
                return True
        except OSError:
            return False

    def _open_capture(self):
        if not self._port_reachable():
            raise OSError("端口不可达（摄像头断电或断网）")
        self._prepare_env()
        if self._is_net:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        else:
            cap = cv2.VideoCapture(self.url)
        self._apply_options(cap)
        return cap

    def _apply_options(self, cap) -> None:
        """缓冲 1 + 打开/读取超时；后端不支持时静默跳过（与原版一致）。"""
        if not self._is_net:
            return
        for prop, value in (
            (cv2.CAP_PROP_BUFFERSIZE, self.buffer_size),
            (cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms),
            (cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms),
        ):
            try:
                cap.set(prop, value)
            except Exception:
                pass

    # ---------------- 读取循环 ----------------
    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if attempt:
                self.reconnect_count += 1
            try:
                cap = self._open_capture()
            except Exception as exc:                      # 建 capture 就抛异常也要重连
                cap, self._last_error = None, f"建流异常：{exc}"
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                attempt += 1
                self._connected = False
                if not self._last_error:          # 保留具体原因（端口不可达 / 握手失败）
                    self._last_error = "打不开视频源"
                self._message = f"正在连接（已重试 {attempt} 次）…"
                if self.reconnect_attempts and attempt >= self.reconnect_attempts:
                    return
                self._stop.wait(self.reconnect_delay)
                continue

            attempt = 0
            self._connected = True
            self._last_error = ""
            self._message = "已连接"
            self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self._pump(cap)
            cap.release()
            self._connected = False
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_delay)

    def _pump(self, cap) -> None:
        last_jpeg = 0.0
        began = time.monotonic()
        local_frames = 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                self._last_error = "读帧失败（断流）"
                self._message = "读帧失败（断流），准备重连"
                return

            self._frame = frame
            self._frames += 1
            self._size = (frame.shape[1], frame.shape[0])

            now = time.monotonic()
            if now - last_jpeg >= self.publish_interval:
                last_jpeg = now
                ok_jpg, buf = cv2.imencode(".jpg", frame,
                                           [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                if ok_jpg:
                    self._jpeg = buf.tobytes()

            if self.on_frame is not None:
                try:
                    self.on_frame(frame)
                except Exception as exc:                      # 判定异常不能拖垮取流
                    self._last_error = f"on_frame 异常：{exc}"

            local_frames += 1
            if self.realtime and not self.url.startswith("rtsp"):
                wait = began + local_frames / max(1.0, self._fps) - time.monotonic()
                if wait > 0:
                    self._stop.wait(min(wait, 0.5))

    # ---------------- 读取接口 ----------------
    def latest_jpeg(self) -> bytes:
        return self._jpeg

    def info(self) -> dict:
        return {
            "label": self.label,
            "connected": self._connected,
            "width": self._size[0],
            "height": self._size[1],
            "source_fps": round(self._fps, 2),
            "frames": self._frames,
            "message": self._message,
            "last_error": self._last_error,
        }
