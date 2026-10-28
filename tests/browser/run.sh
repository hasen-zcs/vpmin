#!/usr/bin/env bash
# 浏览器回归：ROI 编辑 + 跨源/嵌入。
# 前置：被测服务已启动（默认 5099）；playwright 可用（这里复用 /tmp/uitest 的 node_modules）。
# 用法：bash tests/browser/run.sh [BASE] [PLAYWRIGHT_DIR]
set -u
BASE="${1:-http://127.0.0.1:5099}"
PW_DIR="${2:-/tmp/uitest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'kill %1 2>/dev/null; rm -rf "$TMP"' EXIT

sed "s#__BASE__#${BASE}#g" "$HERE/mock_mgmt.html" > "$TMP/index.html"
( cd "$TMP" && python3 -m http.server 5097 >/dev/null 2>&1 ) &
sleep 1

fail=0
for f in roi_edit.js cors_embed.js; do
  echo "=== $f ==="
  NODE_PATH="$PW_DIR/node_modules" node "$HERE/$f" "$BASE" || fail=1
done
exit $fail
