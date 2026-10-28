"""生命周期：启停、参数热更、给 HTTP 层提供只读/写方法。

线程模型：每个机位一个取流线程；分析机位的取流线程回调本类的 on_frame。
所有跨线程读写都由 self.lock 保护（判定一帧约 1ms，不会卡住接口）。
"""

import threading
import time
from pathlib import Path

from ..adapters.formats import wall
from ..adapters.roi import Roi
from ..domain.trigger import Thresholds
from . import status


class Runtime:
    def __init__(self, *, store, cameras, analyze_id, analysis, probe, runner,
                 standard, image_store, event_log, csv_path, images_dir, history=None,
                 retention=None):
        self.store = store
        self.history = history
        self.cameras = cameras
        self.analyze_id = int(analyze_id)
        self.analysis = analysis
        self.probe = probe
        self.runner = runner
        self.standard = standard
        self.image_store = image_store
        self.event_log = event_log
        self.retention = retention
        self.csv_path = str(csv_path)
        self.images_dir = Path(images_dir)
        self.lock = threading.RLock()
        self._images = []
        feed = getattr(self.runner, "standard_feed", None)
        if feed is not None:
            self.standard.ttl = float(getattr(feed, "ttl", 0.0) or 0.0)
        self._restore_images()

    def _restore_images(self) -> None:
        """启动时把 output/image 里已有的存档读回列表（重启后页面仍有历史曲线图）。"""
        try:
            entries = self.image_store.list(200) if self.image_store else []
        except Exception:                      # 存档目录不可读时不影响启动
            entries = []
        restored = []
        for item in entries:
            name = item.get("file") or ""
            if not name:
                continue
            restored.append({
                "file": name,
                "path": str(self.images_dir / name),
                "index": item.get("index") or 0,
                "start_time": item.get("start_time") or "",
                "partial": False,
            })
        self._images = restored[:200]

    # ---------------- 生命周期 ----------------
    def start_cameras(self):
        """取流独立于分析：页面随时能看画面，点「开始分析」才做判定。"""
        with self.lock:
            for cam in self.cameras:
                if cam._thread is None or not cam._thread.is_alive():
                    cam.start()

    def start(self):
        self.start_cameras()
        with self.lock:
            self.analysis.start()

    def stop(self):
        """只停判定，取流继续（与原版一致：画面不黑）。"""
        with self.lock:
            self.analysis.stop()

    def shutdown(self):
        with self.lock:
            self.analysis.stop()
            for cam in self.cameras:
                cam.stop()

    @property
    def running(self) -> bool:
        return self.analysis.running

    # ---------------- 取流回调 ----------------
    def on_frame(self, frame):
        if self.retention is not None:
            self.retention.maybe_run()        # 内部节流（默认每小时真正扫一次盘）
        with self.lock:
            self.analysis.on_frame(frame)
            if self.analysis.last_image_path:
                self._remember_image(self.analysis.last_image_path)
                self.analysis.last_image_path = ""

    def _remember_image(self, path: str) -> None:
        entry = {
            "file": Path(path).name,
            "path": str(path),
            "index": self.runner.count,
            "start_time": wall(self.runner.base, self.runner.reaction_start or 0.0),
            "partial": self.runner.reaction_end is None,
        }
        self._images.insert(0, entry)
        del self._images[200:]

    # ---------------- 只读接口 ----------------
    def snapshot(self) -> dict:
        with self.lock:
            elapsed = self.analysis.elapsed
            window = self.standard.window()
            return status.build({
                "running": self.analysis.running,
                "state": self.runner.machine.state.value,
                "elapsed": elapsed,
                "count": self.runner.count,
                "start_wall": (wall(self.runner.base, self.runner.reaction_start)
                               if self.runner.reaction_start is not None else None),
                "end_wall": (wall(self.runner.base, self.runner.reaction_end)
                             if self.runner.reaction_end is not None else None),
                "start_t": self.runner.reaction_start,
                "end_t": self.runner.reaction_end,
                "verdict": self.runner.last_verdict,
                "window": window,
                "standard_payload": self.standard.payload,
                "tolerance": self.standard.tolerance,
                "curve_points": self.runner.curve.view(),
                "curve_stats": status.curve_stats(self.runner.curve.view()),
                "csv_path": self.download_path("frames"),
                "curve_image": self.analysis.curve_image_path,
                "frames": self.analysis.frames,
                "cameras": [c.info() for c in self.cameras],
                "standard_enabled": self.standard.payload is not None,
                "images": list(self._images),
                # ---- 排障用的少量扩展（不是对齐原版）----
                "standard_health": {
                    "ok_count": self.standard.ok_count,
                    "fail_count": self.standard.fail_count,
                    "fresh": self.standard.fresh(elapsed if self.analysis.running else None),
                    "last_error": self.standard.last_error,
                },
                "roi_summary": self.probe.roi.summary() if self.probe.roi is not None else "",
                "stop_reason": self.analysis.stop_reason,
            })

    def curve(self, since: int = 0) -> dict:
        with self.lock:
            points = self.runner.curve.view()
            reset = since > len(points)
            return {
                "reaction": self.runner.count,
                "total": len(points),
                "reset": reset,
                "points": points[0 if reset else since:],
            }

    def images(self) -> list:
        with self.lock:
            return list(self._images)

    def events(self, limit: int = 100) -> list:
        return self.event_log.recent(limit) if self.event_log else []

    def camera(self, cam_id: int = 0):
        index = (cam_id or self.analyze_id) - 1
        return self.cameras[index] if 0 <= index < len(self.cameras) else self.cameras[0]

    # ---------------- 写接口 ----------------
    def reconnect_camera(self, cam_id: int = 0) -> dict:
        """原版 POST /api/camera/reconnect：断流卡死时手动重连。"""
        cam = self.camera(cam_id)
        cam.stop()
        cam.start()
        return {"ok": True, "camera": cam.info()}

    def update_params(self, params: dict) -> dict:
        with self.lock:
            known = self.runner.thresholds
            start_pct = float(params.get("start_threshold", known.start_pct))
            end_pct = float(params.get("end_threshold", known.end_pct))
            self.runner.thresholds = Thresholds(start_pct, end_pct)

            machine = self.runner.machine
            machine.start_confirm = max(0.0, float(params.get("start_confirm", machine.start_confirm)))
            machine.end_confirm = max(0.0, float(params.get("end_confirm", machine.end_confirm)))

            self.probe.start_pixel = max(0, min(255, int(params.get("start_pixel", self.probe.start_pixel))))
            self.probe.end_pixel = max(0, min(255, int(params.get("end_pixel", self.probe.end_pixel))))

            low = float(params.get("standard_min", self.standard.fallback[0]))
            high = float(params.get("standard_max", self.standard.fallback[1]))
            self.standard.fallback = (min(low, high), max(low, high))
            self.standard.tolerance = max(0.0, float(params.get("standard_tolerance",
                                                                self.standard.tolerance)))
            self.analysis.max_seconds = max(0.0, float(params.get("max_seconds",
                                                                   self.analysis.max_seconds)))
            if self.event_log is not None and hasattr(self.event_log, "set_params"):
                self.event_log.set_params(self.params())      # 事件表元信息列跟着热更
            self.store.save(self._roi_dict(), self.params())
            return self.params()

    def update_roi(self, roi_data: dict) -> dict:
        with self.lock:
            roi = (Roi.polygon(roi_data["points"]) if roi_data.get("kind") == "polygon"
                   else Roi.rect(*[int(v) for v in roi_data["rect"]]))
            self.probe.set_roi(roi)
            shape = self._roi_dict()
            self.store.save(shape, self.params())
            if self.history is not None:
                self.history.add(shape)
            return shape

    def rois(self) -> list:
        return self.history.list() if self.history else []

    def apply_roi(self, roi_id: str) -> dict:
        """从历史里挑一个用上（与手画走同一条生效路径）。"""
        item = self.history.get(roi_id) if self.history else None
        if item is None:
            raise KeyError(f"找不到这个 ROI：{roi_id}")
        if item.get("kind") == "polygon":
            return self.update_roi({"kind": "polygon", "points": item.get("points")})
        return self.update_roi({"kind": "rect", "rect": item.get("rect")})

    def refresh_standard(self) -> dict:
        feed = getattr(self.runner, "standard_feed", None)
        if feed is not None:
            now = time.monotonic()
            refreshed = feed.force(now)
            if refreshed is not None:
                payload, ok = refreshed
                self.standard.update(payload, ok, now=now)
        return {"window_kind": self.standard.window().kind,
                "window": [self.standard.window().low, self.standard.window().high]}

    def download_path(self, target: str) -> str:
        """给下载接口用：frames / events。"""
        if target == "events":
            return getattr(self.event_log, "path", "")
        return getattr(self.analysis.frame_csv, "path", "") or self.csv_path

    # ---------------- 组装 ----------------
    def params(self) -> dict:
        return {
            "start_pixel": self.probe.start_pixel,
            "end_pixel": self.probe.end_pixel,
            "start_threshold": self.runner.thresholds.start_pct,
            "end_threshold": self.runner.thresholds.end_pct,
            "start_confirm": self.runner.machine.start_confirm,
            "end_confirm": self.runner.machine.end_confirm,
            "standard_min": self.standard.fallback[0],
            "standard_max": self.standard.fallback[1],
            "standard_tolerance": self.standard.tolerance,
            "max_seconds": self.analysis.max_seconds,
        }

    def _roi_dict(self) -> dict:
        roi = self.probe.roi
        if roi.kind == "polygon":
            return {"kind": "polygon", "points": [list(p) for p in roi.points]}
        return {"kind": "rect", "rect": list(roi.box)}

    def settings_payload(self) -> dict:
        roi = self.probe.roi
        return {"roi": self._roi_dict(), "params": self.params(),
                "roi_summary": roi.summary() if roi is not None else ""}
