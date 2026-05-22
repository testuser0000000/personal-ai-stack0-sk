"""Response-side PII scanning.

v0.1 only scanned outbound prompts. This module closes the gap for inbound
completions, which matters when:

  - A vision model reads PII off an uploaded image (a receipt, a screenshot
    of an email inbox, a photo of a credit card) and echoes it back in
    plain text.
  - A model hallucinates or repeats a token that pattern-matches PII.
  - A model is asked to "summarize the document" and reproduces an email
    address that the user thought was sandboxed.

Two response shapes to handle:

  1. **Non-streaming** — the response body is one JSON object,
     ``{"choices": [{"message": {"content": "..."}}]}``. Easy: parse,
     redact, re-serialize.

  2. **Streaming** (server-sent events, ``"stream": true``) — bytes arrive
     as ``data: {...delta...}\\n\\n`` events. The model emits short text
     fragments one at a time. We can't buffer the whole stream (it would
     kill the typing-indicator UX), but we also can't redact each chunk
     in isolation, because a single email like ``leaky@example.com`` might
     arrive across two chunks.

     Strategy: per-choice **tail buffer**. We accumulate text in a small
     per-choice string buffer, redact everything except the last
     ``TAIL_KEEP`` chars on each tick, emit the redacted prefix, and keep
     the tail for the next round. On end-of-stream we flush whatever's left
     through the redactor one last time. This bounds latency to "one tail
     window" and catches any PII whose length fits in the buffer — which
     is true for every entity type we currently detect (longest API-key
     pattern is ~50 chars).

Trade-off acknowledged: an attacker who deliberately constructs PII longer
than ``TAIL_KEEP`` could split it across the buffer boundary. We're not
defending against the upstream model being adversarial in that specific
sense — the documented threat model is "upstream behaves but produces PII
incidentally." See ``../../docs/threat-model.md``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable

from redactor import RedactionResult, Redactor


log = logging.getLogger("presidio-proxy")


# Big enough to contain any single PII token we detect (longest API-key
# pattern ~50 chars; emails/phones/IPs all well under that). Small enough
# that the user sees tokens arrive ~immediately. Tuned by feel; if you raise
# the entity list to include longer formats, raise this too.
TAIL_KEEP = 80


# --------------------------------------------------------------------------- #
# Non-streaming
# --------------------------------------------------------------------------- #

def redact_json_response(
    body: dict[str, Any], redactor: Redactor
) -> tuple[dict[str, Any], list[RedactionResult]]:
    """Mutate a non-streaming chat-completion body in place, redacting any
    PII in ``choices[*].message.content``. Returns the body and a list of
    per-choice redaction results for logging."""
    results: list[RedactionResult] = []
    for choice in body.get("choices", []):
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content:
            r = redactor.redact(content)
            results.append(r)
            msg["content"] = r.redacted
        # Tool-call only responses have content=None — nothing to scan.
    return body, results


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #

@dataclass
class _ChoiceBuffer:
    """Per-choice tail buffer. OpenAI streaming can interleave multiple
    choices in one stream (``n>1``); each one has its own ``index`` and
    needs its own buffer."""
    text: str = ""
    findings: list[RedactionResult] = field(default_factory=list)


class StreamingResponseScanner:
    """Filter an SSE stream of OpenAI chat-completion deltas, redacting PII.

    Usage::

        scanner = StreamingResponseScanner(redactor)
        async for chunk in scanner.filter(upstream.aiter_raw()):
            yield chunk

    Emits SSE-formatted chunks downstream (same wire format the upstream
    sends, with delta.content rewritten where needed). Non-data lines
    (``: keepalive`` comments, blank separators) are passed through
    unchanged so the downstream client's parser is unaffected.
    """

    def __init__(self, redactor: Redactor, *, warn_only: bool = False):
        self._redactor = redactor
        self._warn_only = warn_only
        # Per-choice index → tail buffer. Created lazily as choices appear.
        self._buffers: dict[int, _ChoiceBuffer] = {}
        # Incomplete bytes left over from the previous chunk read — SSE
        # events are delimited by `\n\n`, but a single TCP read can split
        # an event in half.
        self._pending: bytes = b""

    # -- public ------------------------------------------------------------ #

    async def filter(self, source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        async for raw in source:
            self._pending += raw
            # Split off any complete SSE events. An event ends at b"\n\n".
            while b"\n\n" in self._pending:
                event_bytes, self._pending = self._pending.split(b"\n\n", 1)
                out = self._handle_event(event_bytes)
                if out is not None:
                    yield out + b"\n\n"

        # Source exhausted. Anything left in self._pending is either a
        # trailing event with no terminator (rare but possible) or junk.
        if self._pending.strip():
            out = self._handle_event(self._pending)
            if out is not None:
                yield out
            self._pending = b""

        # Flush remaining buffered text per choice. By definition this
        # text is shorter than TAIL_KEEP, so we redact whatever's left
        # and emit it as one final synthetic delta per choice.
        for idx, buf in self._buffers.items():
            if buf.text:
                result = self._redactor.redact(buf.text)
                buf.findings.append(result)
                emitted = buf.text if self._warn_only else result.redacted
                if emitted:
                    yield _format_sse_delta(idx, emitted)

        # Log any findings we collected during the stream. Same logging
        # discipline as the request side: types + positions only.
        all_findings = [
            f
            for buf in self._buffers.values()
            for f in buf.findings
            if f.had_findings
        ]
        if all_findings:
            log.info(
                "response-redaction-event mode=%s findings=%s",
                "WARN" if self._warn_only else "REDACT",
                [self._redactor.describe_findings(f.findings) for f in all_findings],
            )

    # -- internal --------------------------------------------------------- #

    def _handle_event(self, event_bytes: bytes) -> bytes | None:
        """Process one complete SSE event. Return its replacement bytes,
        or None if the event was consumed (e.g. delta absorbed into buffer)."""
        text = event_bytes.decode("utf-8", errors="replace")
        # SSE events can have multiple `field: value` lines. For OpenAI
        # streaming, the line we care about is `data: {...}`.
        lines = text.split("\n")
        out_lines: list[str] = []
        absorbed = False

        for line in lines:
            if not line.startswith("data:"):
                # `: keepalive` comments, blank lines, etc. — pass through.
                out_lines.append(line)
                continue

            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                # End-of-stream sentinel. Emit synthetic flush deltas for
                # any buffered text, THEN forward [DONE]. We can't append
                # to out_lines mid-iteration cleanly, so we leave the flush
                # to the caller (the outer `filter` loop handles it after
                # source exhausts) — but [DONE] comes BEFORE source
                # exhaustion in the stream. So we flush here too.
                flush_events = self._flush_all_buffers()
                # Stitch: flush events first, then this DONE line.
                joined = b""
                for ev in flush_events:
                    joined += ev + b"\n\n"
                joined += line.encode("utf-8")
                return joined

            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                # Unparseable data line — pass through unmodified.
                out_lines.append(line)
                continue

            replacement = self._process_delta(obj)
            if replacement is None:
                # Delta absorbed entirely into the tail buffer. Don't emit
                # an event for it now; the next chunk's flushable prefix
                # will carry whatever's safe to send.
                absorbed = True
                continue

            out_lines.append("data: " + json.dumps(replacement, separators=(",", ":")))

        if absorbed and not any(l.strip() for l in out_lines):
            return None

        return "\n".join(out_lines).encode("utf-8")

    def _process_delta(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        """Update buffers from an OpenAI streaming chunk; return a rewritten
        chunk to forward now, or None if everything was absorbed into the
        buffer for later flushing."""
        choices = obj.get("choices")
        if not choices:
            # Non-delta event (e.g. usage stats, role announcement with no
            # content). Forward unmodified.
            return obj

        any_text_seen = False
        any_text_emitted = False
        new_choices: list[dict[str, Any]] = []

        for ch in choices:
            idx = int(ch.get("index", 0))
            delta = ch.get("delta") or {}
            content = delta.get("content")

            if not isinstance(content, str) or not content:
                # No text in this choice (could be role, finish_reason,
                # tool_calls, etc.) — forward as-is.
                new_choices.append(ch)
                continue

            any_text_seen = True
            buf = self._buffers.setdefault(idx, _ChoiceBuffer())
            buf.text += content

            # Decide what's safe to flush: everything except the last
            # TAIL_KEEP chars.
            if len(buf.text) <= TAIL_KEEP:
                emit = ""
            else:
                emit = buf.text[:-TAIL_KEEP]
                buf.text = buf.text[-TAIL_KEEP:]

            if not emit:
                # Whole content absorbed — drop the content field from this
                # choice's delta. Keep other delta fields (role,
                # finish_reason) intact.
                ch_copy = dict(ch)
                delta_copy = dict(delta)
                delta_copy.pop("content", None)
                ch_copy["delta"] = delta_copy
                new_choices.append(ch_copy)
                continue

            # Redact the emit slice.
            result = self._redactor.redact(emit)
            if result.had_findings:
                buf.findings.append(result)

            ch_copy = dict(ch)
            delta_copy = dict(delta)
            delta_copy["content"] = emit if self._warn_only else result.redacted
            ch_copy["delta"] = delta_copy
            new_choices.append(ch_copy)
            any_text_emitted = True

        if any_text_seen and not any_text_emitted:
            # All text was absorbed into tail buffers — no point emitting
            # an event with empty content.
            return None

        out = dict(obj)
        out["choices"] = new_choices
        return out

    def _flush_all_buffers(self) -> list[bytes]:
        """Flush every choice's tail buffer as a synthetic delta event.
        Used at [DONE]."""
        events: list[bytes] = []
        for idx, buf in self._buffers.items():
            if not buf.text:
                continue
            result = self._redactor.redact(buf.text)
            buf.findings.append(result)
            emitted = buf.text if self._warn_only else result.redacted
            buf.text = ""
            if emitted:
                events.append(_format_sse_delta(idx, emitted))
        return events


def _format_sse_delta(index: int, content: str) -> bytes:
    """Build a minimal OpenAI-style streaming delta event for the given
    choice index. Used to flush leftover tail-buffer text at end of stream."""
    payload = {
        "choices": [
            {"index": index, "delta": {"content": content}, "finish_reason": None}
        ]
    }
    return ("data: " + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
