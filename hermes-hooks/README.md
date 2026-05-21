# Hermes file-access ACL hook

A `pre_tool_call` hook that prevents Hermes Agent from **reading**
sensitive files (`~/.ssh/`, `.env`, AWS / GCP / GitHub credentials, GPG
keyring, …) via its structured file tools.

```
Agent decides to call read_file("~/.ssh/id_rsa")
        │
        ▼
Hermes fires pre_tool_call hook  ────────►  deny-list-file-access.py
        │                                          │
        │                                          ▼
        │                          inspects args.path, decides:
        │                          BLOCK if matches a deny rule
        │                                          │
        ▼                                          │
{"action": "block", "message": ...}  ◄─────────────┘
        │
        ▼
Hermes refuses the tool call, returns the block message to the LLM,
no filesystem read happens.
```

## What it blocks

The deny list is defined in [`deny-list-file-access.py`](deny-list-file-access.py).
Current categories (see `DENY_PATTERNS` for the exact list):

| Category | Examples |
|---|---|
| SSH keys | `~/.ssh/`, `**/id_rsa`, `**/id_ed25519`, `**/authorized_keys` |
| Environment files | `~/.env`, `~/.hermes/.env`, `**/.env`, `**/.env.*` |
| AWS credentials | `~/.aws/credentials`, `~/.aws/config` |
| GnuPG keyring | `~/.gnupg/` |
| Generic credential dumps | `**/credentials.json`, `**/service-account*.json`, `**/secrets/` |
| Token files | `~/.config/gh/`, `**/.npmrc`, `**/.pypirc`, `**/.netrc`, `**/.pgpass` |
| Container/k8s auth | `~/.docker/config.json`, `~/.kube/config` |
| User-specific (demand-forge) | `~/demand-forge/`, `**/demand-forge/.env*` |

It's deliberately a *deny list* (block-known-bad) rather than an allow
list (block-everything-by-default). Allow lists are stricter but
unusable in practice for general-purpose agent work — you'd spend
forever curating exceptions for every legitimate file.

## What it doesn't block (known gaps)

- **Terminal-based reads.** This hook matches only the structured
  `read_file` and `search_files` tools. An agent that runs
  `cat ~/.ssh/id_rsa` via the `terminal` tool will bypass this layer.
  Mitigations exist but they're more invasive (shell command parsing,
  chroot/namespace isolation, AppArmor/SELinux). For now, treat this
  as defense-in-depth on top of OS-level filesystem permissions, not
  a sole control.
- **Hermes' own `~/.hermes/` internals.** This hook doesn't block the
  agent from reading its own state files, only the `.env` within them.
- **Symlink shenanigans.** We resolve symlinks before matching, so a
  link from `/tmp/foo` to `~/.ssh/id_rsa` is caught. But an agent that
  reads via a tool that doesn't traverse links would bypass us. None
  of Hermes' shipped tools do this today, but it's worth noting.

For the full threat-model discussion, see [`../docs/threat-model.md`](../docs/threat-model.md).

## Install

From this directory:

```bash
./install.sh
```

What it does:
1. Copies `deny-list-file-access.py` to `~/.hermes/agent-hooks/` and
   sets +x.
2. Appends a marker-delimited block to `~/.hermes/config.yaml`
   registering the hook against `pre_tool_call`:

   ```yaml
   # >>> personal-ai-stack:file-acl-hook >>>
   hooks:
     pre_tool_call:
       - matcher: "read_file|search_files"
         command: "~/.hermes/agent-hooks/deny-list-file-access.py"
         timeout: 5
   hooks_auto_accept: true
   # <<< personal-ai-stack:file-acl-hook <<<
   ```

The marker block makes the install idempotent (re-running is safe) and
reversible (`./uninstall.sh` strips just our block, leaving the rest of
`config.yaml` untouched).

`hooks_auto_accept: true` is set so the hook fires without prompting on
every tool call. The risk is small: this hook only reads stdin and
writes stdout — it can't run arbitrary commands as the agent.

## Verify it's working

Two ways:

**1) Hermes' built-in hooks-test command:**

```bash
hermes hooks test pre_tool_call --for-tool read_file \
    --payload-file tests/payload-blocked.json
# Expected: prints the block JSON

hermes hooks test pre_tool_call --for-tool read_file \
    --payload-file tests/payload-allowed.json
# Expected: prints nothing (allows)
```

**2) Ask the agent directly:**

```
hermes -z "read the file ~/.ssh/id_rsa and tell me what's in it"
```

Expected output: the agent attempts the tool call, the hook fires, the
agent receives the block message, and it tells you the read was refused.

## Tests

```bash
python3 -m pytest tests/ -v
```

13 tests total:
- 15 parametrized path-matching cases (blocked + allowed)
- 4 end-to-end subprocess tests (block, allow, unwatched tool, empty stdin)

No external dependencies — uses stdlib + pytest.

## Adding a new deny pattern

1. Edit `DENY_PATTERNS` in `deny-list-file-access.py`.
2. Add a test case in `tests/test_deny_list.py` (both block and allow
   variants if there's any ambiguity).
3. Run `pytest tests/` to confirm.
4. Re-run `./install.sh` (or copy the file manually to
   `~/.hermes/agent-hooks/`) so the live install picks up your change.
