#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CFS_ROOT="$(cd "$ROOT/../.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" && -f "$CFS_ROOT/.env" ]]; then
  ENV_FILE="$CFS_ROOT/.env"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PYTHONPATH="${CFS_ROOT}:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-${GOOGLE_CLIENT_ID:-}}"
export GOOGLE_OAUTH_CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-${GOOGLE_CLIENT_SECRET:-}}"
export APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:8090}"

cd "$ROOT"
RELOAD_ARGS=()
if [[ "${MOBILE_NO_RELOAD:-}" != "1" ]]; then
  # Only watch mobile/ so edits under app/ (desktop sync) do not restart this server.
  RELOAD_ARGS=(--reload --reload-dir mobile)
fi
if ((${#RELOAD_ARGS[@]} > 0)); then
  exec python3 -m uvicorn mobile.main:app --host 127.0.0.1 --port 8090 "${RELOAD_ARGS[@]}"
fi
exec python3 -m uvicorn mobile.main:app --host 127.0.0.1 --port 8090
