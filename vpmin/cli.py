"""命令行入口：解析参数 → 组装各层 → 跑帧循环。"""

import argparse
import json
import sys

from .adapters.counter import CounterStore
from .adapters.painter import CurvePainter
from .adapters.probe import RoiProbe
from .adapters.roi import Roi
from .adapters.sink import JsonlSink
from .adapters.source import FrameSource
from .adapters.standard_http import HttpStandardFeed
from .domain.curve import CurveBuffer
from .domain.state_machine import ReactionStateMachine
from .domain.standard import StandardPolicy
from .domain.trigger import Thresholds
from .runner import ReactionRunner


def _roi(args) -> Roi:
    if args.roi_polygon:
        pts = [[int(v) for v in pair.split(",")]
               for pair in args.roi_polygon.split(";") if pair.strip()]
        return Roi.polygon(pts)
    if not args.roi:
        raise SystemExit("需要 --roi x,y,w,h 或 --roi-polygon \"x,y;x,y;...\"")
    return Roi.rect(*[int(v) for v in args.roi.split(",")])


def _apply_settings(args) -> None:
    """可选：直接读 参考实现的 output/settings.json。"""
    if not args.settings:
        return
    with open(args.settings, encoding="utf-8") as fh:
        cfg = json.load(fh)
    roi, params = cfg.get("roi") or {}, cfg.get("params") or {}
    if roi.get("kind") == "polygon" and roi.get("points"):
        args.roi_polygon = ";".join(f"{int(x)},{int(y)}" for x, y in roi["points"])
    elif roi.get("bbox"):
        args.roi = ",".join(str(int(v)) for v in roi["bbox"])
    args.start_pixel = int(params.get("start_pixel", args.start_pixel))
    args.end_pixel = int(params.get("end_pixel", args.end_pixel))
    args.start_threshold = float(params.get("start_threshold", args.start_threshold))
    args.end_threshold = float(params.get("end_threshold", args.end_threshold))
    args.start_confirm = float(params.get("start_confirm", args.start_confirm))
    args.end_confirm = float(params.get("end_confirm", args.end_confirm))
    args.local_min = float(params.get("standard_min", args.local_min))
    args.local_max = float(params.get("standard_max", args.local_max))
    args.tolerance = float(params.get("standard_tolerance", args.tolerance))


def build(args) -> ReactionRunner:
    feed = HttpStandardFeed(args.standard_url, ttl=args.standard_ttl) if args.standard_url else None

    standard = StandardPolicy((args.local_min, args.local_max), args.tolerance)
    if args.standard:                      # 显式区间优先（离线复现用）
        low, high = (float(v) for v in args.standard.split("-"))
        standard.update({"standard_spheroidization_time_lower": low,
                         "standard_spheroidization_time_upper": high})

    return ReactionRunner(
        probe=RoiProbe(_roi(args), args.start_pixel, args.end_pixel),
        thresholds=Thresholds(args.start_threshold, args.end_threshold),
        machine=ReactionStateMachine(args.start_confirm, args.end_confirm),
        standard=standard,
        curve=CurveBuffer(args.preroll),
        painter=CurvePainter(args.jpeg_quality) if not args.no_image else _NoImage(),
        sink=JsonlSink(args.out, embed_image=True),
        counter=CounterStore(args.counter, args.start_count),
        standard_feed=feed,
    )


class _NoImage:
    def draw(self, points, start_t=None, end_t=None, start_pixel=None) -> str:
        return ""


def run(args) -> int:
    runner = build(args)
    source = FrameSource(args.source, args.fps, args.start_sec, not args.fast,
                         args.max_frames, args.clock)

    total = 0
    for frame, elapsed in source:
        record = runner.process(frame, elapsed)
        if record:
            runner.sink.write(record)          # CLI 直接连图带记录写盘
            total += 1
            print(f"[{record['reaction_count']}] {record['actual_spheroidization_time']}  "
                  f"{record['spheroidization_abnormal']}  @ {record['detection_time']}")

    print(f"\n完成：{total} 条反应 → {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="球化亮光检测 · 极简分层实现")
    ap.add_argument("--source", required=True, help="视频文件或 RTSP 地址")
    ap.add_argument("--settings", default="", help="直接读 参考实现的 output/settings.json")
    ap.add_argument("--roi", default="", help="矩形 x,y,w,h")
    ap.add_argument("--roi-polygon", default="", help='多边形 "x,y;x,y;..."')
    ap.add_argument("--out", default="reactions.jsonl")
    ap.add_argument("--counter", default="reaction_counter.json",
                    help="序号文件（默认当前目录，传空串则不持久化）")
    ap.add_argument("--start-count", type=int, default=0)
    ap.add_argument("--standard", default="", help="固定区间，如 70-80")
    ap.add_argument("--standard-url", default="")
    ap.add_argument("--standard-ttl", type=float, default=30.0)
    ap.add_argument("--local-min", type=float, default=60.0)
    ap.add_argument("--local-max", type=float, default=120.0)
    ap.add_argument("--tolerance", type=float, default=5.0)
    ap.add_argument("--start-pixel", type=int, default=240)
    ap.add_argument("--end-pixel", type=int, default=240)
    ap.add_argument("--start-threshold", type=float, default=3.0)
    ap.add_argument("--end-threshold", type=float, default=3.0)
    ap.add_argument("--start-confirm", type=float, default=1.0)
    ap.add_argument("--end-confirm", type=float, default=3.0)
    ap.add_argument("--preroll", type=float, default=5.0)
    ap.add_argument("--jpeg-quality", type=int, default=85)
    ap.add_argument("--no-image", action="store_true", help="不画曲线图（快跑/对拍用）")
    ap.add_argument("--fps", type=float, default=0.0)
    ap.add_argument("--start-sec", type=float, default=0.0)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--fast", action="store_true", help="离线全速（默认按实时节流）")
    ap.add_argument("--clock", choices=("frame", "wall"), default="frame",
                    help="时间轴：frame=帧号/fps（离线复现）；wall=墙钟（与原版一致，生产用这个）")
    args = ap.parse_args(argv)

    _apply_settings(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
