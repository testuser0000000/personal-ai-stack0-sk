#!/usr/bin/env bash
# Install OpenWebUI into a uv-managed isolated venv at
# ~/.local/share/uv/tools/open-webui/, with a launcher in ~/.local/bin/.
#
# Idempotent: if open-webui is already installed, reports the version and
# exits 0.
set -euo pipefail

if command -v open-webui >/dev/null 2>&1; then
    echo "open-webui already installed at $(command -v open-webui)"
    exit 0
fi
if [ -x "$HOME/.local/bin/open-webui" ]; then
    echo "open-webui already installed at $HOME/.local/bin/open-webui"
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV="$HOME/.local/bin/uv"
    else
        echo "uv not found. Install:" >&2
        echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
        exit 1
    fi
else
    UV="$(command -v uv)"
fi

# OpenWebUI requires Python >= 3.11. We pin 3.11 because the wheels for
# some of its heavier deps (torch / transformers) are slow to recompile
# on bleeding-edge Pythons.
echo "Installing open-webui (this can take a few minutes — pulls in fastapi, "
echo "torch, transformers, sentence-transformers, etc.)..."
"$UV" tool install --python 3.11 open-webui

echo
ls -la "$HOME/.local/bin/open-webui"
"$HOME/.local/bin/open-webui" --help 2>&1 | head -5
echo
echo "Note: '\$HOME/.local/bin' must be on your PATH. Add to ~/.bashrc:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
