#!/usr/bin/env bash
# One-shot setup of every component on a fresh machine.
#
# Steps (each is idempotent — safe to re-run):
#   1) Ollama (binary install to ~/.local/)
#   2) Presidio proxy (venv + pinned deps + spaCy model)
#   3) OpenWebUI (uv tool install)
#   4) Hermes file-access ACL hook (if Hermes is installed)
#
# Does NOT pull any models — that's a later, deliberate step the user
# chooses based on which models they actually want. Suggested follow-up:
#
#   ollama pull hermes3:8b      # ~5 GB
#   ollama pull llama3.1:8b     # ~5 GB
#
# Does NOT touch your .env — copy .env.example to .env yourself and put
# real values in. We never write secrets into config files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> [1/4] Ollama"
"$SCRIPT_DIR/install_ollama.sh"

echo
echo "==> [2/4] Presidio proxy venv"
"$SCRIPT_DIR/setup_proxy.sh"

echo
echo "==> [3/4] OpenWebUI"
"$SCRIPT_DIR/install_openwebui.sh"

echo
echo "==> [4/4] Hermes file ACL hook (optional — only if you use Hermes Agent)"
HOOK_INSTALLER="$(cd "$SCRIPT_DIR/../.." && pwd)/hermes-hooks/install.sh"
if [ -f "$HOME/.hermes/config.yaml" ]; then
    "$HOOK_INSTALLER"
else
    echo "  Hermes Agent not detected (~/.hermes/config.yaml missing) — skipping."
    echo "  If you install Hermes later, re-run: $HOOK_INSTALLER"
fi

echo
echo "================================================================"
echo " Setup complete."
echo
echo " Next steps (manual — these are deliberate so you don't surprise"
echo " yourself):"
echo "   1. cp .env.example .env       # then edit and add real keys"
echo "   2. ollama pull hermes3:8b     # or whichever models you want"
echo "   3. ./scripts/bringup.sh       # start the stack"
echo "   4. http://localhost:3000      # in your browser; first signup is admin"
echo "================================================================"
