"""Unit tests for the deny-list hook.

Run from this directory:

    python3 -m pytest -v

(no extra deps — stdlib only)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_HOOK_PATH = Path(__file__).resolve().parents[1] / "deny-list-file-access.py"

# Import the hook as a module so we can call its functions directly.
_spec = importlib.util.spec_from_file_location("deny_list_hook", _HOOK_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


class TestPathMatching:
    @pytest.mark.parametrize(
        "path",
        [
            "~/.ssh/id_rsa",
            "~/.ssh/id_ed25519",
            "~/.ssh/authorized_keys",
            "~/.env",
            "~/.hermes/.env",
            "~/.aws/credentials",
            "~/.gnupg/private-keys-v1.d/foo.key",
            "~/some-project/.env",
            "~/work/secrets/api_keys.txt",
            "~/projects/foo/credentials.json",
            "~/projects/google-svc/service-account.json",
            "~/.config/gh/hosts.yml",
            "~/.kube/config",
            "~/.docker/config.json",
            "~/demand-forge/.env",
        ],
    )
    def test_blocked(self, path: str) -> None:
        assert _mod.is_denied(path), f"expected DENY for {path}"

    @pytest.mark.parametrize(
        "path",
        [
            "~/Documents/notes.md",
            "~/projects/my-app/README.md",
            "~/projects/my-app/src/main.py",
            "/tmp/scratch.txt",
            # NOT a credential file — just happens to have 'env' in the name:
            "~/projects/my-app/environment.yml",
        ],
    )
    def test_allowed(self, path: str) -> None:
        assert not _mod.is_denied(path), f"expected ALLOW for {path}"

    def test_empty_string(self) -> None:
        assert not _mod.is_denied("")


class TestEndToEndViaSubprocess:
    """Invoke the script as Hermes would: pipe JSON on stdin, read stdout."""

    def _run(self, payload: dict) -> tuple[int, str]:
        proc = subprocess.run(
            [sys.executable, str(_HOOK_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode, proc.stdout

    def test_blocked_path_emits_block_json_tool_input_shape(self) -> None:
        """Hermes' production wire shape uses `tool_input`."""
        rc, out = self._run({
            "hook_event_name": "pre_tool_call",
            "tool_name": "read_file",
            "tool_input": {"path": "~/.ssh/id_rsa"},
        })
        assert rc == 0
        parsed = json.loads(out.strip())
        assert parsed["action"] == "block"
        assert "deny rule" in parsed["message"]

    def test_blocked_path_emits_block_json_args_shape(self) -> None:
        """`hermes hooks test` and older docs use `args`. Accept it too."""
        rc, out = self._run({
            "tool_name": "read_file",
            "args": {"path": "~/.ssh/id_rsa"},
        })
        assert rc == 0
        parsed = json.loads(out.strip())
        assert parsed["action"] == "block"

    def test_allowed_path_emits_nothing(self) -> None:
        rc, out = self._run({
            "tool_name": "read_file",
            "tool_input": {"path": "~/Documents/notes.md"},
        })
        assert rc == 0
        assert out.strip() == ""

    def test_unwatched_tool_emits_nothing(self) -> None:
        rc, out = self._run({
            "tool_name": "terminal",  # not in WATCHED_TOOLS
            "tool_input": {"command": "cat ~/.ssh/id_rsa"},
        })
        assert rc == 0
        assert out.strip() == ""

    def test_empty_stdin_fails_open(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_HOOK_PATH)],
            input="",
            capture_output=True,
            text=True,
            timeout=5,
        )
        # No output = no block. The tool runs normally if our hook
        # gets confused; failing closed would brick Hermes if the
        # wire format ever changes.
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
