# scripts/install/

One-time setup scripts. Run these after cloning the repo to install the
components the stack depends on. After this, day-to-day use is
[`scripts/bringup.sh`](../bringup.sh) / [`scripts/teardown.sh`](../teardown.sh).

## One command

```bash
./scripts/install/all.sh
```

Installs everything in order. Idempotent — safe to re-run; already-installed
components report their version and skip.

## What each script does

### `install_ollama.sh`
Resolves the latest Ollama release from GitHub, downloads the
`ollama-linux-amd64.tar.zst` asset, decompresses it (via uv-managed
`zstandard`, no sudo needed), and symlinks the binary into `~/.local/bin/`.
Skips if `ollama` is already on PATH or at `~/.local/bin/ollama`.

### `setup_proxy.sh`
Sets up the Presidio proxy's Python virtualenv at
`guardrails/presidio-proxy/.venv`. Installs pinned `requirements.txt`,
downloads the spaCy `en_core_web_sm` model (~50 MB), and runs a smoke
import to confirm everything wires up. Skips already-completed steps
on re-run.

### `install_openwebui.sh`
`uv tool install --python 3.11 open-webui`. Skips if already installed.
Pulls in heavy deps (fastapi, torch, transformers, sentence-transformers)
— first run takes ~5 minutes.

### `all.sh`
Runs the three install scripts above in order, then installs the Hermes
file-access ACL hook if Hermes Agent is detected at `~/.hermes/`.
Prints clear next-steps at the end.

## What these scripts do NOT do (deliberate)

- **Touch `.env`.** Copy `.env.example` to `.env` and put real values
  in yourself. Scripts read it but never write to it.
- **Pull models.** `ollama pull` is a deliberate step the user chooses
  based on which models they want and how much disk they want to spend.
- **Install Docker / docker-compose.** A `docker-compose.yml` is on
  the roadmap; until then, these scripts + `bringup.sh` are the path.
- **Install system packages with sudo.** Everything goes into
  `~/.local/`. Some scripts may suggest a sudo command if a system tool
  is missing (e.g. `zstd`), but won't run it for you.

## What's verified

Each script was test-run against a machine that already had every
component installed — they correctly report "already present" and exit
0 without re-installing. The "fresh machine" path is exercised in CI
indirectly (the proxy's CI job runs the same pip install + spaCy
download as `setup_proxy.sh`) but a true bare-metal install on a
fresh WSL/VM is on the manual-test checklist.
