#!/usr/bin/env bash
# Install Ollama into ~/.local/ (no sudo, no system-wide install).
#
# Idempotent: if a working ollama binary is already present, this script
# reports the version and exits 0.
#
# Tested on WSL2 Ubuntu 24.04. Should work on any glibc Linux x86_64.
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/ollama"

if command -v ollama >/dev/null 2>&1 && ollama --version 2>&1 | head -1 | grep -q '^ollama version\|^Warning'; then
    echo "ollama already installed:"
    ollama --version 2>&1 | head -2
    exit 0
fi
if [ -x "$BIN_DIR/ollama" ]; then
    echo "ollama already installed at $BIN_DIR/ollama:"
    "$BIN_DIR/ollama" --version 2>&1 | head -2
    exit 0
fi

mkdir -p "$BIN_DIR" "$LIB_DIR"
cd "$(mktemp -d)"

echo "Resolving latest Ollama release..."
RELEASE_JSON="$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest)"
ASSET_URL="$(echo "$RELEASE_JSON" | grep -E '"browser_download_url".*ollama-linux-amd64\.tar\.zst' | head -1 | cut -d'"' -f4)"
if [ -z "$ASSET_URL" ]; then
    echo "Could not find ollama-linux-amd64.tar.zst in latest release." >&2
    exit 1
fi
TAG="$(echo "$RELEASE_JSON" | grep '"tag_name"' | head -1 | cut -d'"' -f4)"
echo "Downloading $TAG: $ASSET_URL"
curl -fL --progress-bar -o ollama.tar.zst "$ASSET_URL"

echo "Decompressing (Python zstandard, no sudo needed for system zstd)..."
# `uv` ships with most installs; use it for an ephemeral zstandard env.
# If uv isn't around, fall back to system zstd.
if command -v uv >/dev/null 2>&1; then
    uv run --quiet --with zstandard python3 -c "
import sys, zstandard
with open('ollama.tar.zst','rb') as fin, open('ollama.tar','wb') as fout:
    zstandard.ZstdDecompressor().copy_stream(fin, fout)
print('decompressed OK')
"
elif command -v unzstd >/dev/null 2>&1; then
    unzstd ollama.tar.zst
elif command -v zstd >/dev/null 2>&1; then
    zstd -d ollama.tar.zst
else
    echo "Need either uv, unzstd, or zstd to decompress. Install one of:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh   # uv (recommended, no sudo)" >&2
    echo "  sudo apt install zstd                              # system zstd" >&2
    exit 1
fi

tar -xf ollama.tar -C "$LIB_DIR"

if [ -x "$LIB_DIR/bin/ollama" ]; then
    ln -sf "$LIB_DIR/bin/ollama" "$BIN_DIR/ollama"
elif [ -x "$LIB_DIR/ollama" ]; then
    ln -sf "$LIB_DIR/ollama" "$BIN_DIR/ollama"
else
    echo "Could not find ollama binary in extracted tree:" >&2
    find "$LIB_DIR" -name ollama -type f -executable -maxdepth 3 >&2
    exit 1
fi

echo "Installed: $BIN_DIR/ollama"
"$BIN_DIR/ollama" --version 2>&1 | head -2
echo
echo "Note: '$BIN_DIR' must be on your PATH for plain 'ollama' to work."
echo "      Add to ~/.bashrc / ~/.zshrc if it isn't:"
echo "         export PATH=\"\$HOME/.local/bin:\$PATH\""
