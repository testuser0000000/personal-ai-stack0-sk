#!/usr/bin/env bash
# Set up the Presidio proxy's Python virtualenv + dependencies.
#
# Idempotent: if .venv already has working uvicorn + presidio + spacy
# model, just reports versions and exits 0.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROXY_DIR="$REPO_ROOT/guardrails/presidio-proxy"

if [ ! -d "$PROXY_DIR" ]; then
    echo "Expected proxy code at $PROXY_DIR — repo layout looks wrong." >&2
    exit 1
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

cd "$PROXY_DIR"

# Step 1 — venv
if [ -x .venv/bin/python ]; then
    echo "venv already exists at $PROXY_DIR/.venv ($(.venv/bin/python --version))"
else
    echo "Creating venv with Python 3.11..."
    "$UV" venv .venv --python 3.11 --seed
fi

# Step 2 — requirements (uv pip detects if everything's already installed and
# is fast as a no-op when there's nothing to do)
echo "Installing pinned requirements (idempotent)..."
"$UV" pip install -r requirements.txt --python .venv/bin/python --quiet
# Use importlib.metadata — works for any installed package, doesn't depend on
# whether the package author chose to expose a __version__ attribute (some
# don't, e.g. presidio-analyzer at the time of writing).
_pkg_ver() {
    .venv/bin/python -c "import importlib.metadata as m; print(m.version('$1'))" 2>/dev/null || echo 'not installed'
}
echo "  presidio-analyzer: $(_pkg_ver presidio-analyzer)"
echo "  fastapi          : $(_pkg_ver fastapi)"

# Step 3 — spaCy small English model (Presidio's NER backend)
if .venv/bin/python -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null; then
    echo "spaCy en_core_web_sm already installed"
else
    echo "Downloading spaCy en_core_web_sm (~50 MB, one-time)..."
    .venv/bin/python -m spacy download en_core_web_sm 2>&1 | tail -3
fi

# Step 4 — smoke test the imports we care about
echo "Smoke test:"
.venv/bin/python -c "
import sys
sys.path.insert(0, '.')
from config import Config, Mode
from redactor import Redactor
from app import create_app
print('  imports OK')
"

echo
echo "Proxy setup complete. Next:"
echo "  ./scripts/start_proxy.sh        # start it"
echo "  cd $PROXY_DIR && .venv/bin/pytest -v   # run the test suite"
