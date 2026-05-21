#!/usr/bin/env bash
# Start Ollama in the background, listening on 127.0.0.1:11434.
# Idempotent: a running instance reports its pid and exits 0.
set -euo pipefail

DATA_DIR="$HOME/.local/share/ollama"
MODEL_DIR="$DATA_DIR/models"
LOG="$DATA_DIR/serve.log"
PIDFILE="$DATA_DIR/serve.pid"

mkdir -p "$DATA_DIR" "$MODEL_DIR"

# Find the ollama binary — PATH first, then the user-space install location
# documented in this repo's quickstart.
OLLAMA="$(command -v ollama || true)"
if [ -z "$OLLAMA" ] && [ -x "$HOME/.local/bin/ollama" ]; then
    OLLAMA="$HOME/.local/bin/ollama"
fi
if [ -z "$OLLAMA" ]; then
    echo "ollama not found in PATH or ~/.local/bin/. Install it first." >&2
    exit 1
fi

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "ollama already running (pid $(cat "$PIDFILE"))"
    exit 0
fi

export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="$MODEL_DIR"

# Clean any stale process bound to 11434 (e.g. orphaned after wsl --shutdown)
pkill -f 'ollama serve' 2>/dev/null || true
sleep 1

nohup "$OLLAMA" serve >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "Started ollama (pid $(cat "$PIDFILE"), log $LOG)"

# Wait for the HTTP API to come up
for i in $(seq 1 30); do
    if curl -fsS "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
        echo "Ollama API up after ${i}s"
        exit 0
    fi
    sleep 1
done

echo "ollama API failed to come up in 30s — tail of log:" >&2
tail -20 "$LOG" >&2
exit 1
