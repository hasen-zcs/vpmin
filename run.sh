#!/usr/bin/env bash
# 生产启动：一个命令起服务（读环境变量或 .env）
set -e
cd "$(dirname "$0")"
SOURCE="${SOURCE:?需要设置 SOURCE=rtsp://...}"
SOURCE2="${SOURCE2:-}"
BIZ_URL="${BIZ_URL:-}"
PORT="${PORT:-5080}"
mkdir -p output
PY="${PYTHON:-python3}"
exec "$PY" -u serve.py \
  --source "$SOURCE" ${SOURCE2:+--source2 "$SOURCE2"} \
  --settings "${SETTINGS:-output/settings.json}" \
  ${BIZ_URL:+--biz-url "$BIZ_URL"} --port "$PORT"
