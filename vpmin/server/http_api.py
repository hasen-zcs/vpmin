"""HTTP 层：只做路由分发与序列化，不含业务。

接口（与原版 参考实现 对齐的部分尽量同名）：
    GET  /                      单页前端
    GET  /api/status            状态（reaction / cameras / standard / curve_images）
    GET  /api/curve?since=N     实时曲线增量
    GET  /api/curve/images      曲线图清单
    GET  /images/<文件名>       曲线图 JPG
    GET  /api/events?limit=N    START/END 事件
    GET  /api/settings          ROI + 参数
    GET  /api/snapshot.jpg?cam=N
    GET  /api/video.mjpg?cam=N  MJPEG（/video_feed1、/video_feed2 为别名）
    POST /api/start|stop
    POST /api/params            参数热更（下一帧生效，并落盘）
    GET  /api/rois              ROI 历史
    POST /api/rois/apply        套用历史 ROI
    POST /api/roi               ROI 热更
    POST /api/standard/refresh  立刻重读后端标准
"""

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BOUNDARY = "vpminframe"
MJPEG_FPS = 12.0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    runtime = None
    static_dir = Path(".")
    allowed_origin = "*"        # "*" 或逗号分隔白名单（serve.py 用 CORS_ORIGIN 环境变量覆盖）

    # ---------------- 基础 ----------------
    def log_message(self, fmt, *args):
        pass                                    # 关闭访问日志（前端 300ms 轮询会刷屏）

    def _cors(self):
        """跨源支持：管理页面（另一端口/域名）要直接 fetch 本服务时需要。
        白名单模式下只回显允许的 Origin；不在白名单则不发头（浏览器自会拦）。"""
        allow = (self.allowed_origin or "").strip()
        origin = self.headers.get("Origin") or ""
        if allow == "*":
            self.send_header("Access-Control-Allow-Origin", "*")
        elif allow and origin:
            names = [o.strip() for o in allow.split(",") if o.strip()]
            if origin in names:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def do_OPTIONS(self):
        self._read_body()
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        """把请求体读干净。必须读，即使这条路由用不上 —— 否则残留字节会被
        HTTP/1.1 长连接当成下一条请求行（表现为 501 Unsupported method ('{}GET')）。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(min(length, 65536))

    def _body(self, raw: bytes = b"") -> dict:
        try:
            return json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _file(self, path: Path):
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------- GET / POST 路由表 ----------------
    # 加接口 = 表里加一行 + 一个 _route_* 方法；do_GET/do_POST 不再增长。
    GET_ROUTES = {
        "/": "_route_index",
        "/index.html": "_route_index",
        "/app.js": "_route_static",
        "/roi.js": "_route_static",
        "/api/status": "_route_status",
        "/api/curve": "_route_curve",
        "/api/curve/images": "_route_curve_images",
        "/api/events": "_route_events",
        "/api/settings": "_route_settings",
        "/api/rois": "_route_rois",
        "/download/frames.csv": "_route_download",
        "/download/events.csv": "_route_download",
        "/api/snapshot.jpg": "_route_snapshot",
        "/api/video.mjpg": "_route_mjpeg",
        "/video_feed1": "_route_mjpeg",
        "/video_feed2": "_route_mjpeg",
    }

    POST_ROUTES = {
        "/api/start": "_route_start",
        "/api/stop": "_route_stop",
        "/api/camera/reconnect": "_route_camera_reconnect",
        "/api/params": "_route_params",
        "/api/roi": "_route_roi",
        "/api/rois/apply": "_route_rois_apply",
        "/api/standard/refresh": "_route_standard_refresh",
    }

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = self.GET_ROUTES.get(url.path)
        if route is not None:
            return getattr(self, route)(url.path, query)
        if url.path.startswith("/images/"):
            return self._file(self.runtime.images_dir / Path(url.path).name)
        return self._json({"error": "not found"}, 404)

    def _route_index(self, path, query):
        return self._file(self.static_dir / "index.html")

    def _route_static(self, path, query):
        if self.static_dir.joinpath(path.lstrip("/")).is_file():
            return self._file(self.static_dir / path.lstrip("/"))
        return self._json({"error": "not found"}, 404)

    def _route_status(self, path, query):
        return self._json(self.runtime.snapshot())


    def _route_curve(self, path, query):
        return self._json(self.runtime.curve(int((query.get("since") or ["0"])[0])))

    def _route_curve_images(self, path, query):
        return self._json({"images": self.runtime.images()})

    def _route_events(self, path, query):
        limit = int((query.get("limit") or ["100"])[0])
        return self._json({"events": self.runtime.events(limit)})

    def _route_settings(self, path, query):
        return self._json(self.runtime.settings_payload())

    def _route_rois(self, path, query):
        return self._json({"rois": self.runtime.rois()})

    def _route_download(self, path, query):
        target = "events" if path.endswith("events.csv") else "frames"
        file_path = Path(self.runtime.download_path(target))
        if not file_path.is_file():
            return self._json({"error": "还没有数据"}, 404)
        return self._file(file_path)

    def _route_snapshot(self, path, query):
        return self._snapshot(int((query.get("cam") or ["0"])[0]))

    def _route_mjpeg(self, path, query):
        cam = 2 if path == "/video_feed2" else int((query.get("cam") or ["0"])[0])
        return self._mjpeg(cam)

    def _snapshot(self, cam: int):
        jpeg = self.runtime.camera(cam).latest_jpeg()
        if not jpeg:
            return self._json({"error": "no frame"}, 503)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def _mjpeg(self, cam: int):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        camera = self.runtime.camera(cam)
        interval = 1.0 / MJPEG_FPS
        try:
            while True:
                jpeg = camera.latest_jpeg()
                if jpeg:
                    self.wfile.write(b"--" + BOUNDARY.encode() + b"\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg + b"\r\n")
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError):
            return

    # ---------------- POST ----------------
    def do_POST(self):
        path = urlparse(self.path).path
        raw = self._read_body()
        route = self.POST_ROUTES.get(path)
        if route is None:
            return self._json({"error": "not found"}, 404)
        return getattr(self, route)(raw)

    def _route_start(self, raw):
        self.runtime.start()
        return self._json({"running": self.runtime.running})

    def _route_camera_reconnect(self, raw):
        cam = int((self._body(raw) or {}).get("cam") or 0)
        return self._json(self.runtime.reconnect_camera(cam))

    def _route_stop(self, raw):
        self.runtime.stop()
        return self._json({"running": self.runtime.running})

    def _route_params(self, raw):
        return self._json({"params": self.runtime.update_params(self._body(raw))})

    def _route_roi(self, raw):
        try:
            return self._json({"roi": self.runtime.update_roi(self._body(raw))})
        except (KeyError, ValueError, TypeError) as exc:
            return self._json({"error": f"ROI 无效：{exc}"}, 400)

    def _route_rois_apply(self, raw):
        try:
            return self._json({"roi": self.runtime.apply_roi(self._body(raw).get("id", ""))})
        except (KeyError, ValueError) as exc:
            return self._json({"error": str(exc)}, 404)

    def _route_standard_refresh(self, raw):
        return self._json(self.runtime.refresh_standard())



class ApiServer:
    """把 Runtime 挂到标准库 HTTP 服务上。"""

    def __init__(self, runtime, static_dir, host: str = "0.0.0.0", port: int = 5080,
                 allowed_origin: str = "*"):
        handler = type("Handler", (_Handler,), {
            "runtime": runtime,
            "static_dir": Path(static_dir),
            "allowed_origin": allowed_origin or "*",
        })
        self.httpd = ThreadingHTTPServer((host, int(port)), handler)
        self.httpd.daemon_threads = True

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
