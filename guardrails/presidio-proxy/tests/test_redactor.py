"""Unit tests on the Redactor — no HTTP, no app, no upstream.

These tests should also read as documentation of what the redactor does
and doesn't do.
"""
from __future__ import annotations

import pytest

from redactor import Redactor


@pytest.fixture(scope="module")
def redactor() -> Redactor:
    # Default entity set + threshold matching production defaults.
    return Redactor(
        entities=(
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "IP_ADDRESS",
            "API_KEY",
        ),
        score_threshold=0.4,
    )


class TestEmails:
    def test_simple_email_is_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("contact me at sujeev@example.com please")
        assert "sujeev@example.com" not in result.redacted
        assert "[EMAIL_ADDRESS]" in result.redacted

    def test_text_without_pii_passes_through(self, redactor: Redactor) -> None:
        result = redactor.redact("hello, what is the capital of france?")
        assert result.redacted == result.original
        assert not result.had_findings

    def test_empty_string(self, redactor: Redactor) -> None:
        result = redactor.redact("")
        assert result.redacted == ""
        assert not result.had_findings


class TestApiKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "sk-or-v1-abc123def456ghi789jkl012mno345pqr",
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
            "gsk_AbCdEf1234567890qrstuvwxyzABCDEF12",
            "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_api_key_formats_are_redacted(self, redactor: Redactor, key: str) -> None:
        text = f"my key is {key} use it carefully"
        result = redactor.redact(text)
        assert key not in result.redacted, f"key {key[:10]}... leaked through"
        assert "[API_KEY]" in result.redacted

    def test_git_sha_is_not_mistaken_for_a_key(self, redactor: Redactor) -> None:
        # 40-char hex SHAs should NOT be flagged as API keys.
        text = "see commit a1b2c3d4e5f6789012345678901234567890abcd"
        result = redactor.redact(text)
        assert "a1b2c3d4" in result.redacted


class TestIPAndPhone:
    def test_ipv4_is_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("the server at 192.168.1.42 is unreachable")
        assert "192.168.1.42" not in result.redacted
        assert "[IP_ADDRESS]" in result.redacted

    def test_phone_us_format_is_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("call me at (555) 123-4567 thanks")
        assert "(555) 123-4567" not in result.redacted


class TestEntityScoping:
    def test_disabled_entity_does_not_redact(self) -> None:
        # If a user explicitly disables EMAIL_ADDRESS, an email should pass.
        r = Redactor(entities=("IP_ADDRESS",), score_threshold=0.4)
        result = r.redact("send to nobody@example.com")
        assert "nobody@example.com" in result.redacted

    def test_describe_findings_omits_original_text(self, redactor: Redactor) -> None:
        # Defense in depth: log structures should never include the redacted
        # text, only the *position* and *type*. A leaked log shouldn't
        # leak the secret it was protecting against.
        result = redactor.redact("api key sk-or-v1-abcdefghijklmnopqrstuvwxyz0123")
        described = redactor.describe_findings(result.findings)
        assert described, "expected at least one finding"
        for d in described:
            assert "text" not in d
            assert "sk-or-v1" not in str(d)
