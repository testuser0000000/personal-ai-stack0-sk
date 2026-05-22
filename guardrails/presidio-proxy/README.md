# Presidio PII proxy

A small FastAPI service that sits between OpenWebUI (or any
OpenAI-compatible client) and a cloud LLM provider (typically OpenRouter),
and redacts PII out of outbound requests before they leave the machine.

```
  client (OpenWebUI)
        │
        ▼
  this proxy on 127.0.0.1:8000  ── scans messages[*].content
        │
        ▼
  OpenRouter (or any OpenAI-compatible endpoint)
```

## What it redacts

By default — high-signal **technical** PII, the stuff most likely to cause
real damage if it leaks:

- Email addresses
- Phone numbers
- Credit card numbers
- IP addresses
- **API-key-shaped tokens** (custom recognizer): `sk-…`, `sk-ant-…`,
  `sk-or-v1-…`, `gsk_…`, `ghp_…`, `gho_…` / `ghu_…` / `ghs_…` / `ghr_…`,
  `AKIA…`, `AIza…`, Slack `xox*-…`

Things explicitly **not** in the default list (configurable via env):

- Person names — over-triggers on normal prose; users mention "Alex" or
  "the team" without intending it as PII.
- Locations / organizations — same reason.
- Dates of birth, SSNs, MRNs — supported by Presidio but skipped here
  unless you're using this for healthcare or HR use cases. Enable via
  `PRESIDIO_ENTITIES` if you need them.

## Request-side modes (env: `PRESIDIO_MODE`)

What the proxy does with PII in the **outbound prompt** before it leaves
the machine.

| Mode | Behavior |
|---|---|
| `REDACT` *(default)* | Replace each match with `[ENTITY_TYPE]` (e.g. `[EMAIL_ADDRESS]`) and forward the cleaned request. |
| `WARN` | Log finding metadata (type + position, never the original text) and forward the **original** request unchanged. Useful during a tuning phase to see what would be redacted without breaking anything. |
| `BLOCK` | Refuse the request with HTTP 400 + a JSON explanation of what was caught. Use when "if in doubt, don't send" is more important than UX. |

## Response-side modes (env: `PRESIDIO_RESPONSE_MODE`, v0.2+)

What the proxy does with PII in the **inbound completion**. Closes the
leak path where a vision model reads PII off an uploaded receipt or
screenshot and echoes it back into the conversation. Works for both
non-streaming responses and `stream: true` SSE responses; streaming uses
an 80-char per-choice tail buffer so PII tokens that arrive across
multiple chunks are still caught.

| Mode | Behavior |
|---|---|
| `REDACT` *(default)* | Replace matches in `choices[*].message.content` (or streaming `delta.content`) with `[ENTITY_TYPE]` before sending to the client. |
| `WARN` | Log finding metadata; pass the original response through unchanged. |
| `OFF` | Skip response scanning entirely. Mirrors v0.1 behavior, marginally faster. |

`BLOCK` is intentionally not a response-side option: by the time the
proxy sees response bytes the upstream call is already paid for, and for
streaming responses earlier tokens may already be on the wire. If you
need "refuse on PII," use request-side `BLOCK` on the prompt that
*would* surface it.

## Setup

> Tested on WSL2 Ubuntu 24.04 with Python 3.11 via [`uv`](https://docs.astral.sh/uv/).

```bash
cd /mnt/c/Users/KAIZOKU/Documents/VSCode/GitHub-st-3/personal-ai-stack0-sk/guardrails/presidio-proxy

# 1. Make a virtualenv and install deps
uv venv .venv --python 3.11
uv pip install -r requirements.txt

# 2. Pull the spaCy small English model (~50 MB, one-time)
.venv/bin/python -m spacy download en_core_web_sm

# 3. Copy + edit config
cp .env.example .env
# edit .env: set OPENROUTER_API_KEY at minimum

# 4. Run
.venv/bin/uvicorn app:app --port 8000 --env-file .env
```

Quick verify (the JSON body uses placeholder text to keep this README
free of real-looking key strings):

```bash
curl http://localhost:8000/__health
# {"status":"ok","mode":"REDACT", ...}
```

## Wiring OpenWebUI to use the proxy

Stop OpenWebUI, then start it pointed at `localhost:8000` instead of
OpenRouter directly:

```bash
OPENAI_API_BASE_URL=http://localhost:8000/v1 \
OPENAI_API_KEY=anything-string \
open-webui serve --port 3000
```

The `OPENAI_API_KEY=anything-string` is intentional — the proxy strips
the client's Authorization header and substitutes its own from
`.env`. OpenWebUI never sees (or needs) the real OpenRouter key.

## Tests

```bash
.venv/bin/pytest -v
```

- `tests/test_redactor.py` — unit tests on detection / replacement.
- `tests/test_proxy.py` — proxy tests using `respx` to mock the upstream
  (so the tests never hit the network).

## Security notes

- **What the proxy logs.** Finding metadata only — entity type, start /
  end offsets, confidence score. **Never** the original sensitive text.
  This is deliberate: log files are a common leak vector, and a proxy
  that logged what it was protecting against would defeat its own
  purpose.
- **What still leaks.** The redacted text itself can still tell the
  upstream "there was an email here." For cases where even *the presence
  of* PII is sensitive, use `BLOCK` mode.
- **What this doesn't defend against.** A malicious upstream (i.e., if
  OpenRouter itself were compromised) can still see the redacted prompt
  and infer information. The threat model in
  [`../../docs/threat-model.md`](../../docs/threat-model.md) covers what
  this stack does and doesn't try to defend.
- **Network exposure.** The proxy binds to `127.0.0.1` only by default.
  If you ever bind to `0.0.0.0`, you've now made a credential-laundering
  service for anyone on your LAN — don't.

## What's not done yet (v0.2)

- Allowlist support for "I know this looks like PII but please send it
  anyway" — e.g., when intentionally asking the LLM to format an email.
- Per-conversation overrides. Currently the mode is global.
- Tail-buffer redaction in streaming responses bounds detection to PII
  tokens shorter than ~80 characters. Every entity type currently in the
  default set (emails, phones, credit cards, IPs, the listed API-key
  formats) fits comfortably inside that window — but a deliberately
  constructed longer secret could be split across the buffer boundary.
  Mitigation: keep custom entity patterns short, or raise `TAIL_KEEP` in
  `response_scanner.py` at the cost of more latency before the first
  visible token.
