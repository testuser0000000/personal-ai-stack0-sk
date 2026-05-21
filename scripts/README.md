# scripts/

Operational scripts for bringing the stack up and down. Run them from
WSL2 (Ubuntu); they expect the user-space installs documented in the
top-level [README](../README.md) (Ollama at `~/.local/bin/`, OpenWebUI
installed via `uv tool install`, the proxy's `.venv` set up under
`guardrails/presidio-proxy/`).

## Usage

One-shot bring-up of everything:

```bash
./scripts/bringup.sh
```

Stop everything:

```bash
./scripts/teardown.sh
```

Start a single component (if you already have the others up):

```bash
./scripts/start_ollama.sh        # local model runtime
./scripts/start_proxy.sh         # PII redaction proxy
./scripts/start_openwebui.sh     # chat UI
```

## Per-script details

### `start_ollama.sh`
Starts `ollama serve` in the background on `127.0.0.1:11434`. Models are
kept under `~/.local/share/ollama/models/`. Idempotent — re-running just
reports the existing pid.

### `start_proxy.sh`
Starts the Presidio FastAPI proxy on `127.0.0.1:8000`. Loads the
`OPENROUTER_API_KEY` from `$REPO_ROOT/.env` first, falling back to
`~/.hermes/.env` for the original Hermes-Agent-first setup. The
proxy's `.venv` must already exist — see
[`../guardrails/presidio-proxy/README.md`](../guardrails/presidio-proxy/README.md)
for one-time setup.

### `start_openwebui.sh`
Starts OpenWebUI on `127.0.0.1:3000`, pointed at the proxy
(`OPENAI_API_BASE_URL=http://127.0.0.1:8000/v1`). **Refuses to start if
the proxy isn't up** — silently bypassing the guardrail would defeat
the point. Local Ollama traffic is unaffected; only cloud requests
flow through the proxy.

### `bringup.sh`
Runs the three start scripts in order (Ollama → proxy → OpenWebUI),
waiting for each component's health check before proceeding to the
next. Total time on a warm machine: ~10s. On the first start of the
day (cold cache): up to a minute, because Presidio loads spaCy on
boot.

### `teardown.sh`
Stops everything in reverse order, with a graceful kill (up to 10s)
followed by force-kill if needed. Reports if anything is still bound
to our ports after we're done.

## What these scripts do NOT do (intentional)

- **Install the components.** They expect Ollama, the proxy `.venv`,
  and OpenWebUI to already be set up. See the top-level README and
  per-component READMEs for one-time installation. (A `docker-compose.yml`
  that does end-to-end install is on the roadmap.)
- **Auto-start on WSL boot.** WSL doesn't run systemd unit files for
  the default user without a session, and we don't want to require
  root or wsl.conf edits. You currently run `./scripts/bringup.sh`
  once per WSL session.
- **Manage `.env`.** Copy `.env.example` to `.env` and fill in your
  keys yourself. Scripts read the file but never write to it.
