#!/usr/bin/env python3
"""Hermes Agent pre_tool_call hook: deny reads of sensitive paths.

Registered against Hermes' `pre_tool_call` event with a matcher of
`read_file|search_files`. Reads the event payload from stdin, inspects
the file-tool args, and emits `{"action": "block", ...}` to stdout if
the requested path matches a deny rule.

Wire protocol (from agent/shell_hooks.py::_serialize_payload):

    stdin payload (one JSON object):
        {
          "hook_event_name": "pre_tool_call",
          "tool_name": "read_file",
          "tool_input": {"path": "/home/user/.ssh/id_rsa"},
          "session_id": "...",
          "cwd": "...",
          "extra": {...}
        }

    Note the field name is `tool_input`, NOT `args` — Hermes' serializer
    rewrites the kwargs it gets from invoke_hook into the shape above
    before invoking the script. Earlier versions of this hook read
    `args` and silently no-op'd; we now read both for robustness.

    stdout to block:
        {"action": "block", "message": "Reason..."}

    stdout to allow (or no output / non-block JSON):
        (nothing)

The script is conservative — false positives (over-blocking) are
preferable to false negatives (key exfiltration).

This hook is a complement to Hermes' built-in WRITE denylist in
agent/file_safety.py. That covers write/patch tools; this covers reads.
"""
from __future__ import annotations

import json
import os
import sys
from fnmatch import fnmatch
from pathlib import Path


# Patterns expressed two ways:
#   "~/foo" — home-relative, expanded at runtime
#   "**/foo" or absolute  — fnmatch-style glob matched against the
#                            resolved absolute path
DENY_PATTERNS: tuple[str, ...] = (
    # SSH key material
    "~/.ssh",
    "~/.ssh/*",
    "**/id_rsa",
    "**/id_ed25519",
    "**/id_ecdsa",
    "**/id_dsa",
    "**/authorized_keys",
    # Environment files
    "~/.env",
    "~/.hermes/.env",
    "**/.env",
    "**/.env.*",
    # AWS credentials
    "~/.aws",
    "~/.aws/*",
    "**/.aws/credentials",
    "**/.aws/config",
    # GnuPG keyring
    "~/.gnupg",
    "~/.gnupg/*",
    # Generic credential dumps
    "**/credentials.json",
    "**/service-account*.json",
    "**/secrets",
    "**/secrets/*",
    # GitHub / npm / pypi tokens
    "~/.config/gh",
    "~/.config/gh/*",
    "**/.npmrc",
    "**/.pypirc",
    "**/.netrc",
    "**/.pgpass",
    # Docker / kube auth
    "~/.docker/config.json",
    "~/.kube/config",
    "~/.kube",
    "~/.kube/*",
    # Demand-forge repo — user's commercial project, isolate from
    # personal experimentation per their stated preference.
    "~/demand-forge",
    "~/demand-forge/*",
    "**/demand-forge/.env",
    "**/demand-forge/.env.*",
)

# Tools we care about. Other tools (terminal, web_fetch, etc.) can also
# leak filesystem content but this hook focuses on the structured file
# tools; terminal-based reads (`cat ~/.ssh/id_rsa`) are a documented gap
# — see README for the threat-model discussion.
WATCHED_TOOLS: frozenset[str] = frozenset({"read_file", "search_files"})


def _normalize(path: str) -> tuple[str, str]:
    """Return (absolute, home-relative-or-absolute) variants for matching.

    home-relative form is `~/foo/bar` when the resolved path is under
    the user's home directory, else the same as absolute. We match
    against both so patterns can be written either way.
    """
    p = Path(os.path.expanduser(path))
    try:
        abs_path = str(p.resolve(strict=False))
    except (OSError, RuntimeError):
        abs_path = str(p.absolute())
    home = str(Path.home())
    if abs_path == home:
        home_rel = "~"
    elif abs_path.startswith(home + os.sep):
        home_rel = "~" + abs_path[len(home):]
    else:
        home_rel = abs_path
    return abs_path, home_rel


def _matches(path: str, pattern: str) -> bool:
    """fnmatch with `**` support for arbitrary-depth matching.

    fnmatch by itself treats `**` like a single `*` (one path segment).
    We implement the recursive-glob semantic by checking against both
    forms: the original pattern, and a "**" -> "*/*" expansion at every
    depth up to a reasonable bound. Good enough for path matching;
    avoids pulling in pathlib's globbing which only works against real
    filesystem entries.
    """
    if fnmatch(path, pattern):
        return True
    if "**" in pattern:
        # Try a few depths of expansion. Practical paths rarely go
        # deeper than 12 components.
        for depth in range(1, 13):
            expanded = pattern.replace("**", "/".join(["*"] * depth))
            if fnmatch(path, expanded):
                return True
    return False


def is_denied(path: str) -> bool:
    """Return True if `path` matches any deny pattern."""
    if not path:
        return False
    abs_path, home_rel = _normalize(path)
    home = str(Path.home())
    for pat in DENY_PATTERNS:
        # Pattern may itself contain `~/` — expand for absolute matching.
        if pat.startswith("~"):
            pat_abs = home + pat[1:]
        else:
            pat_abs = pat
        if _matches(abs_path, pat_abs) or _matches(home_rel, pat):
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # No payload means we have nothing to inspect — fail open so the
        # tool runs normally. (Failing closed here would brick Hermes if
        # the harness ever changed the wire format.)
        return 0

    tool = payload.get("tool_name", "")
    if tool not in WATCHED_TOOLS:
        return 0

    # Hermes' production wire shape uses `tool_input`. `hermes hooks test`
    # and some older docs use `args`. Accept either.
    args = payload.get("tool_input") or payload.get("args") or {}
    if not isinstance(args, dict):
        return 0

    # Different file tools may name the arg differently. Cover the
    # known names; an arg we don't recognize is left untouched.
    path = (
        args.get("path")
        or args.get("file")
        or args.get("filename")
        or args.get("file_path")
        or args.get("root")
        or ""
    )
    if not path:
        return 0

    if is_denied(path):
        sys.stdout.write(json.dumps({
            "action": "block",
            "message": (
                f"Access denied by personal-ai-stack file ACL: "
                f"'{path}' matches a deny rule. Protected categories "
                "include ~/.ssh, .env, **/secrets/**, AWS/GCP/Slack/GitHub "
                "credentials, GnuPG keyring, and the demand-forge repo. "
                "Edit hermes-hooks/deny-list-file-access.py if this is a "
                "legitimate request being mis-flagged."
            ),
        }))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
