#!/usr/bin/env bash
# Start the Presidio PII-redaction proxy on 127.0.0.1:8000.
# Idempotent: a running instance reports its pid and exits 0.
# Expects: the proxy's .venv to already exist at
#   $REPO_ROOT/guardrails/presidio-proxy/.venv
# (see guardrails/presidio-proxy/README.md for one-time setup).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROXY_DIR="$REPO_ROOT/guardrails/presidio-proxy"

DATA_DIR="$HOME/.local/share"
LOG="$DATA_DIR/presidio-proxy.log"
PIDFILE="$DATA_DIR/presidio-proxy.pid"

mkdir -p "$DATA_DIR"

if [ ! -x "$PROXY_DIR/.venv/bin/uvicorn" ]; then
    echo "Proxy venv missing at $PROXY_DIR/.venv" >&2
    echo "Run the one-time setup:" >&2
    echo "  cd $PROXY_DIR && uv venv .venv --python 3.11 && uv pip install -r requirements.txt" >&2
    echo "  .venv/bin/python -m spacy download en_core_web_sm" >&2
    exit 1
fi

# Load OPENROUTER_API_KEY. Order: repo's .env (preferred — keeps the
# project self-contained) → user's ~/.hermes/.env (fallback for the
# original Hermes-Agent-first setup this stack grew out of).
set -a
if [ -f "$REPO_ROOT/.env" ]; then
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
elif [ -f "$HOME/.hermes/.env" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.hermes/.env"
fi
set +a

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "FATAL: OPENROUTER_API_KEY not found in repo .env or ~/.hermes/.env" >&2
    exit 1
fi

export PRESIDIO_MODE="${PRESIDIO_MODE:-REDACT}"
export PRESIDIO_RESPONSE_MODE="${PRESIDIO_RESPONSE_MODE:-REDACT}"
export PRESIDIO_PROXY_PORT="${PRESIDIO_PROXY_PORT:-8000}"
export PRESIDIO_UPSTREAM_URL="${PRESIDIO_UPSTREAM_URL:-https://openrouter.ai/api/v1}"
export PRESIDIO_ENTITIES="${PRESIDIO_ENTITIES:-EMAIL_ADDRESS,PHONE_NUMBER,CREDIT_CARD,IP_ADDRESS,API_KEY}"
export PRESIDIO_SCORE_THRESHOLD="${PRESIDIO_SCORE_THRESHOLD:-0.4}"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "proxy already running (pid $(cat "$PIDFILE"))"
    curl -fsS "http://127.0.0.1:$PRESIDIO_PROXY_PORT/__health" || true
    echo
    exit 0
fi

cd "$PROXY_DIR"
nohup .venv/bin/uvicorn app:app \
    --host 127.0.0.1 \
    --port "$PRESIDIO_PROXY_PORT" \
    --log-level info \
    >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "Started proxy (pid $(cat "$PIDFILE"), mode=$PRESIDIO_MODE, log $LOG)"

# Wait for /__health (Presidio takes ~12s to load spaCy on first start)
for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PRESIDIO_PROXY_PORT/__health" >/dev/null 2>&1; then
        echo "Proxy healthy after ${i}s"
        exit 0
    fi
    sleep 1
done

echo "Proxy failed health check in 60s — tail of log:" >&2
tail -40 "$LOG" >&2
exit 1
