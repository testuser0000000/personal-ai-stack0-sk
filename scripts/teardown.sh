#!/usr/bin/env bash
# Stop everything started by bringup.sh, in reverse order
# (so dependents go down before what they depend on).
set -uo pipefail   # no -e: stopping a not-running service is not an error

_stop() {
    local name="$1"
    local pidfile="$2"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        local pid
        pid=$(cat "$pidfile")
        echo "Stopping $name (pid $pid)..."
        kill "$pid" 2>/dev/null || true
        # Wait up to 10s for graceful exit
        for i in $(seq 1 10); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Force-killing $name..."
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    else
        echo "$name not running (no pid file or stale)."
    fi
}

_stop "OpenWebUI"      "$HOME/.local/share/open-webui/server.pid"
_stop "Presidio proxy" "$HOME/.local/share/presidio-proxy.pid"
_stop "Ollama"         "$HOME/.local/share/ollama/serve.pid"

# Defense in depth: if anything is still bound to our ports, log it so
# the user knows (don't kill — could be unrelated)
for port in 3000 8000 11434; do
    if ss -tlnp 2>/dev/null | grep -q ":$port "; then
        echo "Note: something is still listening on port $port (not our pid)."
    fi
done

echo "Teardown complete."
