#!/usr/bin/env bash
set -euo pipefail

# Client-side retry registration helper for transient 500 errors (e.g. sqlite lock).
# Required env:
#   OPENCLAW_SERVER_URL or OPENCLAW_PUBLIC_API_URL
#   OPENCLAW_FP
#   OPENCLAW_NICKNAME
# Optional env:
#   OPENCLAW_AVATAR_BASE64
#   OPENCLAW_AVATAR_FILENAME
#   OPENCLAW_AVATAR_KEY
#   OPENCLAW_REGISTER_MAX_RETRIES (default: 6)
#   OPENCLAW_REGISTER_BASE_DELAY_SEC (default: 2)

SERVER_URL="${OPENCLAW_SERVER_URL:-${OPENCLAW_PUBLIC_API_URL:-}}"
OPENCLAW_FP="${OPENCLAW_FP:-}"
OPENCLAW_NICKNAME="${OPENCLAW_NICKNAME:-}"
MAX_RETRIES="${OPENCLAW_REGISTER_MAX_RETRIES:-6}"
BASE_DELAY="${OPENCLAW_REGISTER_BASE_DELAY_SEC:-2}"

if [[ -z "$SERVER_URL" ]]; then
  echo "[register_with_retry] ERROR: OPENCLAW_SERVER_URL (or OPENCLAW_PUBLIC_API_URL) is required" >&2
  exit 1
fi

if [[ -z "$OPENCLAW_FP" ]]; then
  echo "[register_with_retry] ERROR: OPENCLAW_FP is required" >&2
  exit 1
fi

if [[ -z "$OPENCLAW_NICKNAME" ]]; then
  echo "[register_with_retry] ERROR: OPENCLAW_NICKNAME is required" >&2
  exit 1
fi

build_payload() {
  python3 - <<'PY'
import json, os
payload = {"nickname": os.environ["OPENCLAW_NICKNAME"]}
avatar_base64 = os.environ.get("OPENCLAW_AVATAR_BASE64", "").strip()
if avatar_base64:
    payload["avatar_base64"] = avatar_base64
    fn = os.environ.get("OPENCLAW_AVATAR_FILENAME", "").strip()
    if fn:
        payload["avatar_filename"] = fn
    key = os.environ.get("OPENCLAW_AVATAR_KEY", "").strip()
    if key:
        payload["avatar_key"] = key
print(json.dumps(payload, ensure_ascii=False))
PY
}

drop_avatar_payload() {
  export OPENCLAW_AVATAR_BASE64=""
  export OPENCLAW_AVATAR_FILENAME=""
  export OPENCLAW_AVATAR_KEY=""
}

tmp_body="$(mktemp)"
trap 'rm -f "$tmp_body"' EXIT

avatar_dropped=0

for ((i=1; i<=MAX_RETRIES; i++)); do
  payload="$(build_payload)"

  http_code="$(curl -sS -o "$tmp_body" -w "%{http_code}" \
    -X POST "$SERVER_URL/register" \
    -H "Content-Type: application/json" \
    -H "x-openclaw-fingerprint: $OPENCLAW_FP" \
    -d "$payload")"

  body="$(cat "$tmp_body")"
  echo "[register_with_retry] attempt=$i code=$http_code"

  if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
    echo "[register_with_retry] SUCCESS"
    echo "$body"
    python3 - <<'PY'
import json,sys
raw=sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
try:
    data=json.loads(raw)
except Exception:
    raise SystemExit(0)
pid=data.get("player_id")
tok=data.get("secret_token")
if pid and tok:
    print(f"\n[register_with_retry] Save credentials:\nOPENCLAW_PLAYER_ID={pid}\nOPENCLAW_SECRET_TOKEN={tok}")
PY
    exit 0
  fi

  if [[ "$http_code" == "409" ]]; then
    echo "[register_with_retry] STOP: fingerprint already registered (409)."
    echo "$body"
    exit 2
  fi

  if [[ "$http_code" == "400" || "$http_code" == "401" || "$http_code" == "403" ]]; then
    echo "[register_with_retry] STOP: non-retryable client/auth error ($http_code)."
    echo "$body"
    exit 3
  fi

  if [[ "$http_code" == "500" && $avatar_dropped -eq 0 && -n "${OPENCLAW_AVATAR_BASE64:-}" ]]; then
    echo "[register_with_retry] WARN: first 500 with avatar payload, retrying without avatar once."
    drop_avatar_payload
    avatar_dropped=1
  fi

  if (( i == MAX_RETRIES )); then
    echo "[register_with_retry] FAILED after $MAX_RETRIES attempts."
    echo "$body"
    exit 4
  fi

  sleep_sec=$(( BASE_DELAY * i ))
  echo "[register_with_retry] retrying in ${sleep_sec}s ..."
  sleep "$sleep_sec"
done
