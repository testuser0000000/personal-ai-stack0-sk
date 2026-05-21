# personal-ai-stack0-sk

[![CI](https://github.com/testuser0000000/personal-ai-stack0-sk/actions/workflows/ci.yml/badge.svg)](https://github.com/testuser0000000/personal-ai-stack0-sk/actions/workflows/ci.yml)

A self-hosted, privacy-conscious AI workspace for personal use and learning.
Local LLMs (Ollama) and cloud LLMs (OpenRouter) live behind one chat UI
(OpenWebUI), with a PII-redaction proxy and file-access controls protecting
anything sensitive on the host machine.

> **Status:** Working end-to-end. All three privacy layers (local-model
> fallback, outbound PII redaction, file-read deny list) are in place and
> verified. Roadmap: one-command `docker-compose.yml`, Gmail thread export,
> OpenWebUI Workspaces walkthrough. This repo is also a portfolio piece —
> built in the open while I learn.

## Why this exists

Most chat UIs for LLMs are either:
- **Cloud SaaS** (ChatGPT, Claude.ai): polished, but every prompt leaves
  your machine and is subject to the provider's training and retention
  policies.
- **Local-only** (Ollama bare, llama.cpp, LM Studio): private, but limited
  to whatever you can run on your own hardware.

This stack tries to give the best of both: a **single UI** that lets you
choose, per conversation, whether to use a local model (private, slower)
or a cloud model (faster, more capable) — and when you do reach out to
the cloud, **PII is redacted before it leaves the machine**.

It's also a deliberate exercise in *system integration*: the value isn't
in any one component (most of them are off-the-shelf, credited below),
it's in how they're wired together and the engineering decisions in the
glue code.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │      Browser (http://localhost:3000)    │
                    └──────────────────┬──────────────────────┘
                                       │
                              ┌────────▼─────────┐
                              │    OpenWebUI     │  chat threads, workspaces,
                              │  (off-the-shelf) │  usage tracking, history
                              └────┬─────────┬───┘
                                   │         │
                                   │         │  (cloud requests)
                                   │         ▼
                                   │   ┌──────────────────┐
                                   │   │ Presidio Proxy   │  PII redaction:
                                   │   │  (FastAPI, mine) │  emails, phones,
                                   │   │                  │  API keys, IPs...
                                   │   └────────┬─────────┘
                                   │            │
                              ┌────▼───┐   ┌────▼──────────┐
                              │ Ollama │   │  OpenRouter   │
                              │ (local)│   │  (cloud LLMs) │
                              └────────┘   └───────────────┘
```

### Components

| Component | Role | Original / Off-the-shelf |
|---|---|---|
| **OpenWebUI** | Chat UI, threads, workspaces ("projects"), usage tracking | Off-the-shelf ([open-webui/open-webui](https://github.com/open-webui/open-webui)) |
| **Ollama** | Local LLM runtime (currently serving `hermes3:8b`) | Off-the-shelf ([ollama/ollama](https://github.com/ollama/ollama)) |
| **OpenRouter** | Cloud LLM gateway (free + paid models from many vendors) | External SaaS |
| **Presidio Proxy** | Intercepts outbound cloud requests, redacts PII before they leave | **Mine** — see [`guardrails/presidio-proxy/`](guardrails/presidio-proxy/) |
| **Hermes file ACLs** | Deny-list hook preventing Hermes Agent from reading `~/.ssh/`, `**/.env`, etc. | **Mine** — see [`hermes-hooks/`](hermes-hooks/) |
| **OpenWebUI Functions** | Custom plugins: Gmail thread export, etc. | **Mine** — see [`openwebui-functions/`](openwebui-functions/) |

## Repository layout

```
personal-ai-stack0-sk/
├── README.md                       — this file
├── LICENSE                         — MIT
├── .env.example                    — required env vars (no real secrets)
├── .gitignore
├── guardrails/
│   └── presidio-proxy/             — FastAPI proxy: redacts PII outbound
├── hermes-hooks/                   — Hermes Agent deny-list / ACL hooks
├── openwebui-functions/            — Custom OpenWebUI Functions (Python)
└── docs/
    ├── architecture.md             — fuller diagram + data-flow
    ├── threat-model.md             — what threats this stack defends against
    └── model-comparison.md         — my notes evaluating different LLMs
```

## Quickstart (Windows + WSL2)

> Tested on Windows 11 with WSL2 Ubuntu 24.04. A `docker-compose.yml`
> that does all of this with one command is on the roadmap.

**One-time setup** (the slow part):

```bash
# 1. Install everything — Ollama, the proxy venv + spaCy model, OpenWebUI,
#    and (if Hermes is around) the file-access ACL hook. Idempotent.
./scripts/install/all.sh

# 2. Pull a local model (your choice; ~5 GB each)
ollama pull hermes3:8b

# 3. Configure secrets — copy the template, fill in your own values
cp .env.example .env
$EDITOR .env       # at minimum, set OPENROUTER_API_KEY
```

See [`scripts/install/README.md`](scripts/install/README.md) for what each
script does individually, and which steps are deliberately left manual
(`.env` editing, model pulls) so you don't get surprised.

**Day-to-day** (after setup is done):

```bash
./scripts/bringup.sh        # start Ollama + proxy + OpenWebUI
# ... open http://localhost:3000 ...
./scripts/teardown.sh       # stop everything
```

See [`scripts/README.md`](scripts/README.md) for the per-component start
scripts and what `bringup` actually does.

## Security & secrets

- All API keys live in `.env` (which is `.gitignored`). `.env.example`
  shows the schema with placeholder values.
- The Presidio proxy redacts emails, phone numbers, credit-card numbers,
  IP addresses, and API-key-shaped tokens (`sk-*`, `gsk_*`, `ghp_*`, …)
  from outbound cloud requests by default. Mode is configurable
  (`REDACT` / `WARN` / `BLOCK`).
- File-access ACLs in the Hermes hooks deny tool-driven reads from
  `~/.ssh/`, `**/.env`, `**/secrets/`, `**/credentials.json`,
  `**/.aws/`, `**/.gnupg/`.
- OpenWebUI binds to `127.0.0.1` only (no LAN exposure).

## What I learned building this

This section will grow as the project grows. Initial notes will land in
`docs/model-comparison.md` and elsewhere — covering things like:

- Why `hermes3:8b` over `llama3.1:8b` for use with Hermes Agent
- Local 8B model performance reality on a CPU-only laptop (15 GB RAM,
  no discrete GPU)
- Tradeoffs of using IMAP/Gmail as a chat archive vs. a primary store
- Streaming response pass-through in a redaction proxy

## License

MIT. See [LICENSE](LICENSE).

## Credits

This project would not exist without the work of:

- [OpenWebUI](https://github.com/open-webui/open-webui)
- [Ollama](https://github.com/ollama/ollama)
- [Microsoft Presidio](https://github.com/microsoft/presidio)
- [Nous Research / Hermes](https://nousresearch.com/) for `hermes3:8b`
  and the Hermes Agent framework
- [OpenRouter](https://openrouter.ai/) for cloud LLM gateway access
