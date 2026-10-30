"""生产入口：装配所有层 → 起 HTTP 服务。

    python serve.py --source "rtsp://admin:***@192.0.2.10:554/Streaming/Channels/102" \
        --source2 "rtsp://admin:***@192.0.2.11:554/Streaming/Channels/102" \
        --settings output/settings.json --biz-url http://127.0.0.1:8000/api/standards/current
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vpmin.adapters.camera import Camera                    # noqa: E402
from vpmin.adapters.counter import CounterStore             # noqa: E402
from vpmin.adapters.event_log import EventLog               # noqa: E402
from vpmin.adapters.frame_csv import FrameCsv               # noqa: E402
from vpmin.adapters.image_store import ImageStore          # noqa: E402           # noqa: E402
from vpmin.adapters.painter import CurvePainter             # noqa: E402
from vpmin.adapters.probe import RoiProbe                   # noqa: E402
from vpmin.adapters.roi import Roi                          # noqa: E402
from vpmin.adapters.roi_store import RoiHistory              # noqa: E402
from vpmin.adapters.settings import SettingsStore           # noqa: E402
from vpmin.adapters.sink import JsonlSink                   # noqa: E402
from vpmin.adapters.retention import Retention              # noqa: E402
from vpmin.adapters.standard_http import HttpStandardFeed    # noqa: E402
from vpmin.app.analysis import AnalysisLoop                  # noqa: E402
from vpmin.app.fusion_loop import FusionLog                   # noqa: E402
from vpmin.app.plc_loop import PlcEventLog, PlcLoop           # noqa: E402
from vpmin.domain.fusion import LightResult, shift_segments    # noqa: E402
from vpmin.domain.models import Segment                       # noqa: E402
from vpmin.app.runtime import Runtime                        # noqa: E402
from vpmin.domain.heat import HeatConfig, HeatTracker         # noqa: E402
from vpmin.domain.curve import CurveBuffer                   # noqa: E402
from vpmin.domain.state_machine import ReactionStateMachine  # noqa: E402
from vpmin.domain.standard import StandardPolicy             # noqa: E402
from vpmin.domain.trigger import Thresholds                  # noqa: E402
from vpmin.runner import ReactionRunner                      # noqa: E402
from vpmin.server.http_api import ApiServer                   # noqa: E402


def _roi(cfg: dict, args) -> Roi:
    if args.roi:
        return Roi.rect(*[int(v) for v in args.roi.split(",")])
    roi = cfg.get("roi") or {}
    if roi.get("kind") == "polygon" and roi.get("points"):
        return Roi.polygon(roi["points"])
    if roi.get("rect") or roi.get("bbox"):
        return Roi.rect(*(roi.get("rect") or roi.get("bbox")))
    return Roi.rect(0, 0, 1, 1)          # 未配置：占位，等网页上改


def build(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "image").mkdir(exist_ok=True)
    (out_dir / "data_values").mkdir(exist_ok=True)

    store = SettingsStore(args.settings)
    cfg = store.load()
    params = cfg["params"]

    probe = RoiProbe(_roi(cfg, args), params["start_pixel"], params["end_pixel"])
    standard = StandardPolicy((params["standard_min"], params["standard_max"]),
                              params["standard_tolerance"])
    painter = CurvePainter(args.curve_quality)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / "frames").mkdir(exist_ok=True)
    csv_path = str(out_dir / "frames" / f"web_{stamp}_reaction.csv")
    frame_csv = FrameCsv(csv_path)

    def new_frame_csv():
        path = str(out_dir / "frames" / f"web_{datetime.now():%Y%m%d_%H%M%S}_reaction.csv")
        return FrameCsv(path), path
    event_log = EventLog(str(out_dir / "data_values" / "reaction_events_history_10_8.csv"),
                         meta={"run_id": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "video": args.source,
                               "params": dict(params)})
    image_store = ImageStore(str(out_dir / "image"))

    # 只清两类一次性产物；reactions.jsonl 是主数据，绝不自动删（一年才 35MB）
    retention = Retention([(out_dir / "frames", "*.csv", args.keep_frame_days),
                           (out_dir / "image", "*.jpg", args.keep_days)],
                          keep_days=args.keep_days)

    runner = ReactionRunner(
        probe=probe,
        thresholds=Thresholds(params["start_threshold"], params["end_threshold"]),
        machine=ReactionStateMachine(params["start_confirm"], params["end_confirm"]),
        standard=standard,
        curve=CurveBuffer(args.preroll),
        painter=painter,
        sink=JsonlSink(str(out_dir), embed_image=not args.jsonl_no_image),
        counter=CounterStore(str(out_dir / "reaction_counter.json")),
        standard_feed=HttpStandardFeed(args.biz_url, ttl=args.standard_ttl) if args.biz_url else None,
        merge_gap=float(params.get("merge_gap", 0.0) or 0.0),
        guard_window=float(params.get("guard_window", 0.0) or 0.0),
        guard_peak=float(params.get("guard_peak", 80.0) or 80.0),
        min_duration=float(params.get("min_duration", 0.0) or 0.0),
        pause_threshold=float(params.get("pause_threshold", 0.0) or 0.0),
        median_frames=int(params.get("median_frames", 0) or 0),
    )

    analysis = AnalysisLoop(probe=probe, runner=runner, painter=painter,
                            image_store=image_store, frame_csv=frame_csv,
                            event_log=event_log, frame_csv_factory=new_frame_csv)

    cam1 = Camera(args.source, "浇注工位 #01", jpeg_quality=args.jpeg_quality,
                  publish_fps=args.publish_fps,
                  reconnect_attempts=args.reconnect_attempts)
    cameras = [cam1]
    if args.source2:
        cameras.append(Camera(args.source2, "浇注工位 #02", jpeg_quality=args.jpeg_quality,
                              publish_fps=args.publish_fps,
                              reconnect_attempts=args.reconnect_attempts))

    # ---- PLC 融合采集（A 方案）：--plc 给了 IP 才启用，没装 snap7 只告警不影响视觉 ----
    # 产出两样东西（都在本模块自己的目录里，服务器上既有文件一律不碰）：
    #   output/plc/plc_events.jsonl  —— 一变一行的事件流
    #   output/plc/heat_records.jsonl —— 五类合一后的每炉记录
    plc_loop = None
    fusion = None
    if args.plc:
        try:
            from vpmin.adapters.plc import PlcSource
            events_path = args.plc_events or str(out_dir / "plc" / "plc_events.jsonl")
            records_path = args.plc_records or str(out_dir / "plc" / "heat_records.jsonl")
            (out_dir / "plc").mkdir(parents=True, exist_ok=True)
            fusion = FusionLog(records_path, clock=time.time)
            source = PlcSource(on_event=lambda ev: None, ip=args.plc,
                               poll=args.plc_poll,
                               weight_arrival=(args.plc_weight_arrival == "on"))
            tracker = HeatTracker(HeatConfig())

            def _on_tick():                     # 每秒一拍：同步亮光流状态 + 结算配对
                tracker.light_stream_ok = bool(analysis.running)
                fusion.settle()

            plc_loop = PlcLoop(source, tracker, PlcEventLog(events_path),
                               on_heat=fusion.add_heat, on_tick=_on_tick)
            # 亮光起止喂给一炉状态机（排除「未球化」误判）
            analysis.on_light = lambda kind, elapsed: plc_loop.light(
                kind, runner.base.timestamp() + elapsed)

            def _on_record(record, verdict, start_el, end_el):
                if verdict is None or start_el is None:
                    return
                base_ts = runner.base.timestamp()
                # 分段起止是「相对 elapsed」的，必须换算到和 PLC 同一条时间轴，
                # 否则记录里的分段墙钟会显示成 1970 年（实测踩过）
                segs = tuple(Segment(base_ts + s.start, base_ts + s.end, s.gap_before)
                             for s in (verdict.segments or ()))
                fusion.add_light(LightResult(
                    start_ts=base_ts + start_el, end_ts=base_ts + end_el,
                    duration=float(verdict.duration),
                    abnormal_type=verdict.abnormal_type,
                    reason=verdict.abnormal_reason,
                    ok=verdict.ok, segments=segs))

            analysis.on_record = _on_record
        except Exception as exc:
            print("[warn] PLC 未启用：%s" % exc, file=sys.stderr)
            plc_loop, fusion = None, None

    runtime = Runtime(store=store, cameras=cameras, analyze_id=1, analysis=analysis,
                      probe=probe, runner=runner, standard=standard,
                      image_store=image_store, event_log=event_log, retention=retention,
                      csv_path=csv_path, images_dir=out_dir / "image",
                      history=RoiHistory(str(out_dir / "roi_history.json")), plc=plc_loop,
                      fusion=fusion)
    if plc_loop is not None:                  # PLC 到位/按按钮 = 炉边界 → 先结算上一炉亮光
        plc_loop.on_arrival = runtime.flush_light_pending
        plc_loop.on_button = runtime.flush_light_pending
    cam1.on_frame = runtime.on_frame          # 只有分析机位喂判定
    return runtime


def main() -> int:
    ap = argparse.ArgumentParser(description="球化亮光检测（极简生产版）")
    ap.add_argument("--source", required=True, help="分析机位（RTSP 或视频文件）")
    ap.add_argument("--source2", default="", help="查看机位（只看画面）")
    ap.add_argument("--settings", default="output/settings.json")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--biz-url", default=os.environ.get("BIZ_URL", ""))
    ap.add_argument("--standard-ttl", type=float, default=30.0)
    ap.add_argument("--roi", default="", help="矩形 x,y,w,h（覆盖 settings）")
    ap.add_argument("--preroll", type=float, default=5.0)
    ap.add_argument("--jpeg-quality", type=int, default=80, help="视频画面 JPEG 质量")
    ap.add_argument("--curve-quality", type=int, default=92,
                    help="曲线图 JPEG 质量（原版 GRAY_CURVE_JPEG_QUALITY 默认 92）")
    ap.add_argument("--publish-fps", type=float, default=10.0)
    ap.add_argument("--reconnect-attempts", type=int, default=0, help="0=无限重试")
    ap.add_argument("--jsonl-no-image", action="store_true",
                    help="reactions.jsonl 不内嵌曲线图（默认内嵌，管理系统靠它拿图）")
    ap.add_argument("--keep-days", type=int, default=30,
                    help="曲线图保留天数（reactions.jsonl 是主数据，不参与清理）")
    ap.add_argument("--keep-frame-days", type=int, default=7,
                    help="逐帧 CSV 保留天数（体积最大：生产约 70MB/天）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5080)
    ap.add_argument("--no-autostart", action="store_true", help="起来后不自动开始分析")
    ap.add_argument("--plc", default=os.environ.get("PLC_IP", ""),
                    help="PLC IP（如 192.0.2.1）；留空不启用 PLC 融合采集")
    ap.add_argument("--plc-poll", type=float, default=0.1, help="PLC 轮询间隔（秒）")
    ap.add_argument("--plc-weight-arrival", default="off", choices=["on", "off"],
                    help="没有「铁水到位」位时，是否用重量变化猜到位。"
                         "现场实测重量喂丝时就在抖（1792→1800→1840），会把一炉切碎，默认 off")
    ap.add_argument("--plc-events", default="", help="事件落盘路径（默认 output/plc/plc_events.jsonl）")
    ap.add_argument("--plc-records", default="",
                    help="五类合一记录路径（默认 output/plc/heat_records.jsonl）")
    ap.add_argument("--cors-origin", default=os.environ.get("CORS_ORIGIN", "*"),
                    help='跨源白名单（默认 *；管理页面要直接 fetch 时用，逗号分隔可填多个）')
    args = ap.parse_args()

    runtime = build(args)
    api = ApiServer(runtime, HERE / "vpmin" / "server" / "static", args.host, args.port,
                    allowed_origin=args.cors_origin)
    runtime.start_cameras()          # 画面先出来；判定由「开始分析」控制
    runtime.start_plc()              # PLC 采集与一炉状态机（独立线程，断了不影响视觉）
    if not args.no_autostart:
        runtime.start()
    print(f"服务已启动：http://{args.host}:{args.port}/  分析机位 {args.source}")
    try:
        api.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.shutdown()
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
