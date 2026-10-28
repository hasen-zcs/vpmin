"""保留策略自测：超期删除 / 未超期保留 / 节流 / 目录不存在不炸。"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from vpmin.adapters.retention import Retention, day_stamp


def check(name, ok):
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


def touch(path, age_days):
    open(path, "w").close()
    ts = time.time() - age_days * 86400
    os.utime(path, (ts, ts))


def main():
    tmp = tempfile.mkdtemp(prefix="vpmin_retention_")
    frames = os.path.join(tmp, "frames"); os.makedirs(frames)
    image = os.path.join(tmp, "image"); os.makedirs(image)

    touch(os.path.join(frames, "old_40d.csv"), 40)
    touch(os.path.join(frames, "new_1d.csv"), 1)
    touch(os.path.join(image, "old_31d.jpg"), 31)
    touch(os.path.join(image, "new_today.jpg"), 0)

    r = Retention([(frames, "*.csv"), (image, "*.jpg")], keep_days=30, interval=0)
    removed = r.run()
    check("删除 40 天前的 CSV", "old_40d.csv" in removed)
    check("删除 31 天前的图片", "old_31d.jpg" in removed)
    check("保留 1 天前的 CSV", "new_1d.csv" not in removed and os.path.exists(os.path.join(frames, "new_1d.csv")))
    check("保留今天的图片", os.path.exists(os.path.join(image, "new_today.jpg")))

    # 节流：interval 很大时 maybe_run 第二次不应再扫
    r2 = Retention([(frames, "*.csv")], keep_days=0, interval=3600)
    first = r2.maybe_run(now=0.0)
    second = r2.maybe_run(now=1.0)
    check("节流期间不重复扫盘", isinstance(first, list) and second == [])

    # 目录不存在不炸
    r3 = Retention([(os.path.join(tmp, "nope"), "*")], keep_days=1)
    check("目录不存在返回空表", r3.run() == [])

    # 分目标保留期：frames 只留 7 天，image 留 30 天
    d2 = os.path.join(tmp, "mix"); os.makedirs(d2)
    touch(os.path.join(d2, "a_9d.csv"), 9)
    touch(os.path.join(d2, "b_2d.csv"), 2)
    r4 = Retention([(d2, "*.csv", 7)], keep_days=30)
    got = r4.run()
    check("逐目标保留期生效（9天删/2天留）",
          "a_9d.csv" in got and os.path.exists(os.path.join(d2, "b_2d.csv")))

    # 白名单：不匹配 glob 的文件绝不能被删
    touch(os.path.join(frames, "settings.json"), 40)
    Retention([(frames, "*.csv")], keep_days=30).run()
    check("非目标文件（settings.json）不删", os.path.exists(os.path.join(frames, "settings.json")))

    check("day_stamp 格式", len(day_stamp()) == 8 and day_stamp().isdigit())
    print("\n全部 PASS")


if __name__ == "__main__":
    main()
