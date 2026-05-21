#!/usr/bin/env bash
# Start OpenWebUI on 127.0.0.1:3000, routed through the Presidio proxy.
# Refuses to start if the proxy isn't already up — silently bypassing the
# guardrail would defeat the whole point.
set -euo pipefail

DATA_DIR="$HOME/.local/share/open-webui"
SECRET_FILE="$DATA_DIR/.webui_secret_key"
LOG="$DATA_DIR/server.log"
PIDFILE="$DATA_DIR/server.pid"
PROXY_PORT="${PRESIDIO_PROXY_PORT:-8000}"

mkdir -p "$DATA_DIR"

# Generate (and persist) a stable secret key so login sessions survive restarts.
if [ ! -f "$SECRET_FILE" ]; then
    head -c 32 /dev/urandom | base64 > "$SECRET_FILE"
    chmod 600 "$SECRET_FILE"
fi

if ! curl -fsS "http://127.0.0.1:$PROXY_PORT/__health" >/dev/null 2>&1; then
    echo "FATAL: Presidio proxy not reachable at http://127.0.0.1:$PROXY_PORT" >&2
    echo "Start it first: ./scripts/start_proxy.sh   (or run ./scripts/bringup.sh)" >&2
    exit 1
fi

# Find the open-webui binary
OPENWEBUI="$(command -v open-webui || true)"
if [ -z "$OPENWEBUI" ] && [ -x "$HOME/.local/bin/open-webui" ]; then
    OPENWEBUI="$HOME/.local/bin/open-webui"
fi
if [ -z "$OPENWEBUI" ]; then
    echo "open-webui not found in PATH or ~/.local/bin/. Install with:" >&2
    echo "  uv tool install open-webui" >&2
    exit 1
fi

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "open-webui already running (pid $(cat "$PIDFILE"))"
    echo "Open: http://localhost:3000"
    exit 0
fi

export PORT="${OPENWEBUI_PORT:-3000}"
export DATA_DIR
export WEBUI_SECRET_KEY="$(cat "$SECRET_FILE")"
export WEBUI_AUTH=true
export ENABLE_SIGNUP=true            # disable in UI after first signup
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export ENABLE_OLLAMA_API=true
# Cloud traffic goes through the proxy; the dummy key gets stripped/replaced.
export OPENAI_API_BASE_URL="http://127.0.0.1:$PROXY_PORT/v1"
export OPENAI_API_KEY="proxy-strips-this-and-substitutes-the-real-key"
export ENABLE_OPENAI_API=true
export HOST=127.0.0.1

nohup "$OPENWEBUI" serve --port "$PORT" --host 127.0.0.1 >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "Started open-webui (pid $(cat "$PIDFILE"), log $LOG)"

for i in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
        echo "OpenWebUI up after ${i}s"
        echo "Open in your browser: http://localhost:$PORT"
        exit 0
    fi
    sleep 1
done

echo "OpenWebUI failed to come up in 120s — tail of log:" >&2
tail -40 "$LOG" >&2
exit 1
