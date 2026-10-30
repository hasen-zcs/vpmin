#!/usr/bin/env python3
"""按钮位只读观察（现场按一次就能定：点动/自锁、到喂丝开始的间隔、要不要补偿）。

**只读**：全程只做 snap7 的 db_read，不写 PLC、不碰任何文件数据。

现场用法（东贝服务器上）：
    /root/Get_Allinfomation/.venv/bin/python plc_button_probe.py --seconds 300
    然后让师傅按一次球化按钮。

输出：
    按下 12:03:41.082   128=00000001
    松开 12:03:41.650   128=00000000     → 按住 0.57s（看起来是点动）
    喂丝开始 12:03:53.210（比按钮晚 12.13s）
    结论：点动 / 自锁 / 未接线

判定逻辑：
    * 按下去后又回到 0        → 点动（momentary）
    * 按下去一直保持 1 不回落  → 自锁（latched）
    * 全程没有任何跳变         → 要么没接线，要么没按
"""

import argparse
import struct
import sys
import time
from datetime import datetime

BTN_BYTE = 128          # DB200.128.0 = 按钮确认
FEED_BYTE = 129         # DB200.129.2 = 喂丝开始, .3 = 喂丝结束
WEIGHT = (200, 8, 4, "REAL")


def bits(v):
    return format(v & 0xFF, "08b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="192.0.2.1")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--poll", type=float, default=0.05)
    args = ap.parse_args()

    import snap7
    c = snap7.client.Client()
    c.connect(args.ip, 0, 1)
    print("已连接 %s（只读，不写 PLC）" % args.ip, flush=True)
    print("请现场按一次球化按钮；观察 %.0f 秒，Ctrl+C 提前结束\n" % args.seconds, flush=True)

    def rd(off):
        return bytes(c.db_read(200, off, 1))[0]

    def weight():
        try:
            return round(struct.unpack(">f", bytes(c.db_read(*WEIGHT[:3])))[0], 3)
        except Exception:
            return None

    prev_btn, prev_feed = rd(BTN_BYTE), rd(FEED_BYTE)
    print("起始电平：128=%s  129=%s  重量=%s" % (bits(prev_btn), bits(prev_feed), weight()),
          flush=True)

    presses = []          # [(按下时刻, 松开时刻 or None)]
    feed_starts = []
    last_press = None
    t0 = time.time()

    try:
        while time.time() - t0 < args.seconds:
            btn, feed = rd(BTN_BYTE), rd(FEED_BYTE)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if btn != prev_btn:
                print("电平变化 %s  128 %s → %s" % (now, bits(prev_btn), bits(btn)), flush=True)
                if btn & 1 and not (prev_btn & 1):          # 0→1 按下
                    last_press = time.time()
                    presses.append([last_press, None])
                    print("  ↑ 按下", flush=True)
                elif prev_btn & 1 and not (btn & 1):        # 1→0 松开
                    print("  ↓ 松开", flush=True)
                    if presses and presses[-1][1] is None:
                        presses[-1][1] = time.time()
                prev_btn = btn

            if feed != prev_feed:
                print("电平变化 %s  129 %s → %s" % (now, bits(prev_feed), bits(feed)), flush=True)
                if feed & 4 and not (prev_feed & 4):        # bit2 喂丝开始
                    feed_starts.append(time.time())
                    print("  ↑ 喂丝开始", flush=True)
                if feed & 8 and not (prev_feed & 8):        # bit3 喂丝结束
                    print("  ↑ 喂丝结束（位，比物理停止晚 24s）", flush=True)
                prev_feed = feed

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\n手动结束", flush=True)

    print("\n===== 结论 =====")
    if not presses:
        print("没有任何按钮跳变：要么这个位没接线，要么这段时间没人按。")
        print("→ 建议：确认接线后重跑；或换一个可疑位再试。")
        return 2

    print("共检测到 %d 次按下" % len(presses))
    momentary = sum(1 for p in presses if p[1] is not None)
    latched = len(presses) - momentary
    for i, (down, up) in enumerate(presses, 1):
        held = "%.2fs" % (up - down) if up else "未回落"
        print("  第 %d 次：按下 %s，按住 %s" % (
            i, datetime.fromtimestamp(down).strftime("%H:%M:%S.%f")[:-3], held))

    if momentary and not latched:
        print("\n判定：**点动**（按下变 1、松开回 0）")
    elif latched and not momentary:
        print("\n判定：**自锁**（按下后保持 1 不回落）→ 我们的程序要先等回落才能认下一次")
    else:
        print("\n判定：混合（有的回落有的没回落）→ 需要多看几次")

    if feed_starts:
        gaps = []
        for down, _ in presses:
            nxt = [f for f in feed_starts if f >= down]
            if nxt:
                gaps.append(nxt[0] - down)
        if gaps:
            print("按钮 → 喂丝开始 的间隔：%s 秒（中位 %.2fs）"
                  % (", ".join("%.2f" % g for g in gaps), sorted(gaps)[len(gaps) // 2]))
            print("→ 这就是「判定开始」到「实际进线」的时延，用来定观察窗和是否需要补偿")
    else:
        print("这次没抓到喂丝开始，间隔量不到（下次按按钮后紧接着开机就行）")

    c.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
