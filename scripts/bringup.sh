#!/usr/bin/env bash
# Bring up the whole stack in the correct order:
#   1) Ollama        (local model runtime)
#   2) Presidio proxy (PII redaction)
#   3) OpenWebUI     (chat UI, routed through the proxy)
#
# Each component does its own health-check; this script just sequences them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> [1/3] Ollama"
"$SCRIPT_DIR/start_ollama.sh"

echo
echo "==> [2/3] Presidio proxy"
"$SCRIPT_DIR/start_proxy.sh"

echo
echo "==> [3/3] OpenWebUI"
"$SCRIPT_DIR/start_openwebui.sh"

echo
echo "================================================================"
echo " Stack is up."
echo "   OpenWebUI:        http://localhost:3000"
echo "   Presidio proxy:   http://127.0.0.1:8000/__health"
echo "   Ollama:           http://127.0.0.1:11434/api/version"
echo
echo " To stop everything:  $SCRIPT_DIR/teardown.sh"
echo "================================================================"
