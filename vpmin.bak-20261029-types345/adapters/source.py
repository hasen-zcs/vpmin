"""取帧：视频文件或 RTSP，按「实时」或「全速」两种节奏吐帧。"""

import time

import cv2


class FrameSource:
    def __init__(self, url, fps_hint: float = 0.0, start_sec: float = 0.0,
                 realtime: bool = False, max_frames: int = 0, clock: str = "frame"):
        self.cap = cv2.VideoCapture(url)
        if not self.cap.isOpened():
            raise SystemExit(f"打不开视频源：{url}")
        self.fps = float(fps_hint or self.cap.get(cv2.CAP_PROP_FPS) or 25.0)
        self.start_sec = float(start_sec)
        self.realtime = bool(realtime)
        self.max_frames = int(max_frames)
        self.clock = clock          # "frame"=帧号/fps（离线可复现）；"wall"=墙钟（与原版一致）
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.start_sec * self.fps))

    def __iter__(self):
        index = int(self.start_sec * self.fps)
        began = time.monotonic()
        produced = 0
        try:
            while True:
                ok, frame = self.cap.read()
                if not ok:
                    break
                elapsed = (time.monotonic() - began if self.clock == "wall"
                           else index / self.fps - self.start_sec)
                if self.realtime and self.clock == "frame":
                    wait = began + elapsed - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                yield frame, elapsed
                index += 1
                produced += 1
                if self.max_frames and produced >= self.max_frames:
                    break
        finally:
            self.cap.release()
