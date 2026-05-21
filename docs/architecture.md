# Architecture

> This document expands on the high-level diagram in the README and
> records the *why* behind major design decisions.

## Data flow

```
User types in browser
       │
       ▼
OpenWebUI (FastAPI + Svelte, port 3000)
  │
  │  ┌── if model is an Ollama model ──┐
  │  ▼                                  │
  │  Ollama (port 11434) ── runs model on CPU/GPU
  │                                     │
  │  ┌── if model is a cloud model ────┐│
  │  ▼                                  ││
  │  Presidio Proxy (port 8000)         ││
  │  │  - scans messages[*].content     ││
  │  │  - REDACT / WARN / BLOCK         ││
  │  ▼                                  ││
  │  OpenRouter (api.openrouter.ai)     ││
  │                                     ││
  └──── streaming response ─────────────┘│
                                         │
                            saved to OpenWebUI's local SQLite
                            (~/.local/share/open-webui/webui.db)
```

## Why these choices

### Why OpenWebUI for the UI (not custom)
- **Saves months of work** on auth, threading, search, file upload, etc.
- The "interesting" engineering in this repo is in the *guardrails*,
  not in re-implementing what OpenWebUI already does well.
- It speaks the OpenAI wire protocol natively, which means swapping
  backends is one env-var change.

### Why a separate Presidio proxy (not an OpenWebUI plugin)
- **Defense in depth.** If OpenWebUI is ever compromised or upgraded
  in a way that bypasses its plugin chain, the proxy still runs
  out-of-process and blocks the egress.
- Makes the redaction reusable for other clients (CLI tools, the
  Hermes Agent, scripts) that all share the same proxy URL.
- Easier to test in isolation — just send it OpenAI-shaped requests.

### Why Ollama + a Nous Hermes model for the local path
- Ollama is the most mature local-LLM runtime with the lowest setup cost.
- `hermes3:8b` is from Nous Research, who also make the Hermes Agent
  framework — it's fine-tuned for the exact tool-calling format the
  agent emits, removing one variable when debugging tool calls.
- ~5 GB Q4_K_M quantization fits comfortably in 15 GB of WSL2 RAM.

### Why localhost-only binding
- This stack is for a single user on a single machine.
- LAN exposure invites attack surface for no benefit.
- If multi-device access is ever needed, the right answer is a
  Tailscale tunnel — not opening port 3000 to the LAN.

## Open questions / future work

- **Local model performance**: First-token latency on CPU is rough
  (~3 min for an 8B model with Hermes Agent's 19k-token tool schemas).
  Worth exploring smaller / quantized models, or Intel iGPU SYCL once
  Ollama supports it.
- **Streaming through Presidio**: Need to keep response streaming
  transparent (no buffer-and-replay) while still scanning the response
  text if response-side redaction is added.
- **OpenWebUI auth hardening**: Currently relies on first-signup-is-admin
  + signup-disabled-after. Should explore the OIDC integration for
  defense in depth, even on localhost.
