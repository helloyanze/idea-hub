#!/usr/bin/env bash
# idea-hub API 便捷调用脚本（服务器端）
# 用法: ihub-api.sh <METHOD> <PATH> [JSON_BODY]
#   例: ihub-api.sh GET /api/stats
#       ihub-api.sh GET "/api/tasks?status=todo"
#       ihub-api.sh POST /api/collect
#       ihub-api.sh POST /api/tasks '{"title":"..."}'
set -euo pipefail

ENV_FILE="/home/yanze/idea-hub/.env"
BASE="http://127.0.0.1:8000"

# 加载凭据（.env 格式 KEY=VALUE）
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [ -z "${IDEAHUB_AUTH_USER:-}" ] || [ -z "${IDEAHUB_AUTH_PASS:-}" ]; then
  echo '{"error":"IDEAHUB_AUTH_USER/PASS not set in .env"}' >&2
  exit 1
fi

METHOD="${1:-GET}"
API_PATH="${2:-/api/stats}"
BODY="${3:-}"

ARGS=(-s -u "${IDEAHUB_AUTH_USER}:${IDEAHUB_AUTH_PASS}")
if [ -n "$BODY" ]; then
  ARGS+=(-H "Content-Type: application/json" -d "$BODY")
fi

# shellcheck disable=SC2086
curl "${ARGS[@]}" -X "$METHOD" "$BASE$API_PATH"
echo
