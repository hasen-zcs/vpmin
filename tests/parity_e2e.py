"""端到端对拍：原版 10.8.3 引擎 vs 极简实现，同一段视频逐帧双跑。

用法（在 minimal/ 目录）：
    python3 tests/parity_e2e.py --video <mp4> \
        --settings <vp10.8.3-file/output/settings.json> --max-frames 0
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "/path/to/vp10.8.3-production"

os.environ.setdefault("BIZ_ENABLED", "0")
os.environ.setdefault("GRAY_CURVE_AUTOSAVE", "0")
sys.path.insert(0, REF)
sys.path.insert(0, HERE)

import cv2                                    # noqa: E402

from core.engine import AnalysisEngine        # noqa: E402

from vpmin.adapters.counter import CounterStore          # noqa: E402
from vpmin.adapters.painter import CurvePainter          # noqa: E402
from vpmin.adapters.probe import RoiProbe                # noqa: E402
from vpmin.adapters.roi import Roi                       # noqa: E402
from vpmin.adapters.sink import JsonlSink                # noqa: E402
from vpmin.domain.curve import CurveBuffer               # noqa: E402
from vpmin.domain.state_machine import ReactionStateMachine  # noqa: E402
from vpmin.domain.standard import StandardPolicy         # noqa: E402
from vpmin.domain.trigger import Thresholds              # noqa: E402
from vpmin.runner import ReactionRunner                   # noqa: E402


class _Collect:
    """承接 sink 的输出到内存，不落盘。"""

    def __init__(self):
        self.rows = []
        self._schema = JsonlSink("")

    def build(self, *args):
        return self._schema.build(*args)

    def write(self, record):
        self.rows.append(record)


def _capture_finish(runner):
    """包一层 _finish，顺手记下这条反应的起止时刻（数值，便于逐条对拍）。"""
    mine = []
    original = runner._finish

    def finish():
        record = original()
        mine.append({
            "start": runner.machine.start_t,
            "end": runner.machine.end_t,
            "count": record["reaction_count"],
            "verdict": record["spheroidization_abnormal"],
            "duration": record["actual_spheroidization_time"],
        })
        return record

    runner._finish = finish
    return mine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    cfg = json.load(open(args.settings, encoding="utf-8"))
    roi_cfg, params = cfg["roi"], cfg["params"]

    # ---- 原版引擎 ----
    engine = AnalysisEngine()
    engine.set_roi(roi_cfg)
    engine.set_params(params)
    engine.start(now=0.0)

    # ---- 极简实现 ----
    roi = Roi.polygon(roi_cfg["points"])
    standard = StandardPolicy((params["standard_min"], params["standard_max"]),
                              params["standard_tolerance"])
    standard.update({"standard_spheroidization_time_lower": params["standard_min"],
                     "standard_spheroidization_time_upper": params["standard_max"]})
    sink = _Collect()
    runner = ReactionRunner(
        probe=RoiProbe(roi, params["start_pixel"], params["end_pixel"]),
        thresholds=Thresholds(params["start_threshold"], params["end_threshold"]),
        machine=ReactionStateMachine(params["start_confirm"], params["end_confirm"]),
        standard=standard,
        curve=CurveBuffer(5.0),
        painter=CurvePainter(),
        sink=sink,
        counter=CounterStore(""),
    )
    mine = _capture_finish(runner)

    cap = cv2.VideoCapture(args.video)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    print(f"video {total} frames @ {fps}fps")

    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = index / fps
            engine.process(frame, now=now)
            runner.process(frame, now)

            index += 1
            if index % 3000 == 0:
                print(f"  ...{index}/{total}  ref={len(engine.events)} mine={len(mine)}")
            if args.max_frames and index >= args.max_frames:
                break
    finally:
        cap.release()

    ref = []
    for event in engine.events:
        if event["event_type"] == "START":
            ref.append({"start": event["time_sec"], "end": None})
        elif event["event_type"] == "END" and ref and ref[-1]["end"] is None:
            ref[-1]["end"] = event["time_sec"]

    print(f"\n原版：{len(ref)} 条；极简：{len(mine)} 条")

    fails = 0
    if len(ref) != len(mine):
        print(f"✗ 条数不一致：ref={len(ref)} mine={len(mine)}")
        fails += 1

    for i, (a, b) in enumerate(zip(ref, mine)):
        same = a["end"] is not None and abs(a["start"] - b["start"]) < 1e-6 \
            and abs(a["end"] - b["end"]) < 1e-6
        if not same:
            fails += 1
        print(f"  #{i + 1} ref {a['start']:.3f}~{a['end']}  mine {b['start']:.3f}~"
              f"{b['end']:.3f}  {b['duration']} {b['verdict']}  {'OK' if same else 'DIFF'}")

    print("PASS" if fails == 0 else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
