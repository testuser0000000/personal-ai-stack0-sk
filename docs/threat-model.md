# Threat model

> What this stack defends against, what it does *not* defend against,
> and why those choices make sense for a single-user personal setup.

## Threats this stack addresses

### T1 — Cloud LLM provider training on my prompts
**Threat.** Free-tier cloud LLMs (OpenRouter free models, etc.) often
reserve the right to train on user inputs.

**Defense.**
- For sensitive prompts: route to a local model (`hermes3:8b` via
  Ollama). Stays on the machine.
- For prompts that must go to the cloud: the Presidio proxy redacts
  PII (emails, phones, credit cards, IPs, API-key-shaped tokens)
  before the request leaves localhost.

**Residual risk.** Presidio's recognizers aren't perfect; novel PII
formats may slip through. Mitigation: regular review of proxy logs
in `WARN` mode periodically to catch what `REDACT` mode is missing.

### T2 — Accidental secret exfiltration from my own filesystem
**Threat.** Agent-style tools (Hermes Agent) have file-read tools.
A confused agent could read `~/.ssh/id_rsa`, `**/.env`, etc., and
include it in a cloud LLM prompt.

**Defense.** Hermes file-access deny-list hook
(`hermes-hooks/deny-list-file-access.sh`) intercepts file-read tool
calls and refuses paths matching the deny-list (`~/.ssh/`, `~/.env`,
`**/secrets/`, `**/credentials.json`, `**/.aws/`, `**/.gnupg/`,
project-specific paths).

**Residual risk.** The hook is a Hermes-specific mechanism; other
agent frameworks plugged into this stack later would need their own
ACL layer.

### T3 — OpenWebUI exposure to the local network
**Threat.** OpenWebUI by default binds to `0.0.0.0`, exposing the
whole chat history and API to anyone on the same Wi-Fi.

**Defense.** Bound explicitly to `127.0.0.1`. First-user-is-admin
combined with signup-disabled-after creates a single-user model.

**Residual risk.** Any process running on the same machine can hit
`localhost:3000`. For multi-user laptops this would need OIDC + per-
user accounts. For single-user laptops, OS-level account isolation
is the right control.

## Threats this stack does NOT address (intentionally)

- **A determined attacker with physical access to the laptop.** Out
  of scope — disk encryption (BitLocker/LUKS) is the right control,
  not application-layer defenses.
- **Compromised OpenRouter / Anthropic / Ollama upstream.** Trusting
  these vendors is a baseline assumption. Reducing that trust is
  what local-model routing is for.
- **Side-channel attacks against the local model.** A truly motivated
  attacker reading `/proc/<ollama-pid>/mem` could extract the model
  weights or running inference state. Same response as physical
  access: OS-level isolation, not app-layer.
- **Supply-chain attacks on OpenWebUI / Ollama / Presidio updates.**
  Pinning versions in the eventual `docker-compose.yml` is the
  partial mitigation; full SBOM + signature verification is out
  of scope for a personal project.

## Why this threat model is appropriate

This is a single-user developer laptop, not a production system. The
goal is **commercially reasonable privacy for personal AI use**, not
nation-state-grade secrecy. The threats addressed are the ones likely
to actually bite during ordinary use (training on prompts, accidental
key leakage, casual network exposure). The threats not addressed are
either better solved by lower-layer controls (disk encryption, OS
user accounts) or by vendor selection (don't use providers you don't
trust at all).
