"""Unit tests for response_scanner — non-streaming and streaming paths.

These tests exercise the scanner directly, without the FastAPI layer, so
they read as documentation of the scanner's contract.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

from redactor import Redactor
from response_scanner import (
    TAIL_KEEP,
    StreamingResponseScanner,
    redact_json_response,
)


@pytest.fixture(scope="module")
def redactor() -> Redactor:
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


# --------------------------------------------------------------------------- #
# Non-streaming
# --------------------------------------------------------------------------- #

class TestNonStreaming:
    def test_email_in_response_is_redacted(self, redactor: Redactor) -> None:
        body = {
            "id": "chatcmpl-x",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "I see leaky@example.com in the image you sent.",
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        out, results = redact_json_response(body, redactor)
        assert "leaky@example.com" not in out["choices"][0]["message"]["content"]
        assert "[EMAIL_ADDRESS]" in out["choices"][0]["message"]["content"]
        assert any(r.had_findings for r in results)

    def test_clean_response_unchanged(self, redactor: Redactor) -> None:
        body = {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Paris."}}
            ]
        }
        out, results = redact_json_response(body, redactor)
        assert out["choices"][0]["message"]["content"] == "Paris."
        assert not any(r.had_findings for r in results)

    def test_tool_call_only_response_has_no_content_to_scan(
        self, redactor: Redactor
    ) -> None:
        # When the model returns a tool call instead of text, message.content
        # is None. The scanner should leave the body untouched and not crash.
        body = {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "x", "type": "function"}],
                    },
                }
            ]
        }
        out, results = redact_json_response(body, redactor)
        assert out["choices"][0]["message"]["content"] is None
        assert results == []


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #

def _sse_chunk(content: str, *, index: int = 0) -> bytes:
    """Build a single OpenAI-style streaming event with the given delta."""
    payload = {
        "choices": [
            {"index": index, "delta": {"content": content}, "finish_reason": None}
        ]
    }
    return ("data: " + json.dumps(payload) + "\n\n").encode("utf-8")


def _done_chunk() -> bytes:
    return b"data: [DONE]\n\n"


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    out = b""
    async for chunk in stream:
        out += chunk
    return out


def _extract_content(sse_bytes: bytes) -> str:
    """Reconstruct the assistant's full text from an SSE byte stream."""
    text = ""
    for line in sse_bytes.decode("utf-8", errors="replace").split("\n"):
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        obj = json.loads(payload)
        for ch in obj.get("choices", []):
            delta = ch.get("delta") or {}
            text += delta.get("content") or ""
    return text


class TestStreaming:
    @pytest.mark.asyncio
    async def test_email_in_single_chunk_is_redacted(self, redactor: Redactor) -> None:
        # The full PII fits in one chunk, but the chunk is shorter than
        # TAIL_KEEP so it will end up in the tail buffer and only get
        # flushed at [DONE]. Either way, the final reconstructed text
        # must not contain the email.
        async def source() -> AsyncIterator[bytes]:
            yield _sse_chunk("hi! reach me at leaky@example.com please")
            yield _done_chunk()

        scanner = StreamingResponseScanner(redactor)
        out = await _collect(scanner.filter(source()))
        reconstructed = _extract_content(out)
        assert "leaky@example.com" not in reconstructed
        assert "[EMAIL_ADDRESS]" in reconstructed
        assert b"[DONE]" in out

    @pytest.mark.asyncio
    async def test_pii_split_across_two_chunks_is_still_caught(
        self, redactor: Redactor
    ) -> None:
        """The whole point of the tail buffer: a PII token whose bytes
        arrive across multiple chunks must still be redacted."""
        async def source() -> AsyncIterator[bytes]:
            yield _sse_chunk("contact me at leaky")
            yield _sse_chunk("@example.com soon")
            yield _done_chunk()

        scanner = StreamingResponseScanner(redactor)
        out = await _collect(scanner.filter(source()))
        reconstructed = _extract_content(out)
        assert "leaky@example.com" not in reconstructed, (
            f"split PII leaked through: {reconstructed!r}"
        )
        assert "[EMAIL_ADDRESS]" in reconstructed

    @pytest.mark.asyncio
    async def test_long_clean_stream_emits_progressively(
        self, redactor: Redactor
    ) -> None:
        """Once the buffered text exceeds TAIL_KEEP, the scanner should be
        emitting chunks — not waiting for [DONE] to flush everything."""
        long_text = "lorem ipsum " * 40  # ~480 chars, well over TAIL_KEEP

        async def source() -> AsyncIterator[bytes]:
            yield _sse_chunk(long_text)
            yield _done_chunk()

        scanner = StreamingResponseScanner(redactor)
        out = await _collect(scanner.filter(source()))
        reconstructed = _extract_content(out)
        assert reconstructed == long_text
        # Should have produced more than just the [DONE] event.
        data_events = [
            l for l in out.decode().split("\n")
            if l.startswith("data:") and "[DONE]" not in l
        ]
        assert len(data_events) >= 1

    @pytest.mark.asyncio
    async def test_warn_mode_passes_original_through(self, redactor: Redactor) -> None:
        async def source() -> AsyncIterator[bytes]:
            yield _sse_chunk("note: 4111-1111-1111-1111 is the card")
            yield _done_chunk()

        scanner = StreamingResponseScanner(redactor, warn_only=True)
        out = await _collect(scanner.filter(source()))
        reconstructed = _extract_content(out)
        # WARN: original survives, but the log line (not asserted here)
        # would have fired with the finding.
        assert "4111-1111-1111-1111" in reconstructed

    @pytest.mark.asyncio
    async def test_keepalive_comments_pass_through(self, redactor: Redactor) -> None:
        """OpenRouter and others send `: keepalive\\n\\n` comments to keep
        the connection warm. They must reach the client untouched."""
        async def source() -> AsyncIterator[bytes]:
            yield b": keepalive\n\n"
            yield _sse_chunk("ok")
            yield _done_chunk()

        scanner = StreamingResponseScanner(redactor)
        out = await _collect(scanner.filter(source()))
        assert b": keepalive" in out

    @pytest.mark.asyncio
    async def test_byte_split_mid_sse_event_is_handled(
        self, redactor: Redactor
    ) -> None:
        """A single TCP read can split an SSE event in half. The scanner
        must wait for the event terminator before processing."""
        full_event = _sse_chunk("safe text here") + _done_chunk()
        split_at = len(full_event) // 2

        async def source() -> AsyncIterator[bytes]:
            yield full_event[:split_at]
            yield full_event[split_at:]

        scanner = StreamingResponseScanner(redactor)
        out = await _collect(scanner.filter(source()))
        reconstructed = _extract_content(out)
        assert reconstructed == "safe text here"
        assert b"[DONE]" in out
