"""FastAPI proxy that redacts PII before forwarding to an OpenAI-compatible
upstream (typically OpenRouter).

Design notes
------------
- **One responsibility.** This module wires HTTP — request parsing, mode
  dispatch, upstream forwarding, response streaming. The actual detection
  and redaction lives in redactor.py.
- **Streaming pass-through.** When the client requests `"stream": true`,
  we stream the upstream response back chunk-by-chunk. We do NOT buffer
  the full response, because that would break the typing-indicator UX
  that users care about. Outbound redaction is sufficient for v1; we
  leave response-side redaction for later.
- **Stateless.** No DB, no session state. Every request is independent.
- **Auth model.** The proxy holds the real upstream API key in env. The
  client (OpenWebUI) talks to the proxy with no credential, or any
  placeholder string — we strip whatever Authorization header it sends
  and substitute our own. This makes the proxy the single thing on the
  machine that holds the OpenRouter token.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from config import Config, Mode
from redactor import RedactionResult, Redactor


log = logging.getLogger("presidio-proxy")


# --------------------------------------------------------------------------- #
# Redaction over a chat-completions payload
# --------------------------------------------------------------------------- #

def _redact_messages(
    body: dict[str, Any], redactor: Redactor
) -> tuple[dict[str, Any], list[RedactionResult]]:
    """Walk messages[*].content and redact in place.

    OpenAI message content can be either:
      - a plain string, OR
      - a list of content parts: [{"type": "text", "text": "..."}, {"type": "image_url", ...}]

    We redact text parts only; images / other modalities pass through unchanged.
    Returns the (mutated) body and a list of per-message results for logging.
    """
    results: list[RedactionResult] = []
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            r = redactor.redact(content)
            results.append(r)
            msg["content"] = r.redacted
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    r = redactor.redact(part.get("text", ""))
                    results.append(r)
                    part["text"] = r.redacted
        # else: ignore (no content / unknown shape — nothing to redact)
    return body, results


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #

def create_app(config: Config | None = None) -> FastAPI:
    """Construct the FastAPI app. Factory pattern so tests can inject a
    customized Config without touching env vars."""
    cfg = config or Config.from_env()
    redactor = Redactor(entities=cfg.entities, score_threshold=cfg.score_threshold)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Long-lived httpx client — connection pooling matters for latency.
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=600.0),  # generous read timeout for long completions
            follow_redirects=True,
        )
        yield
        await app.state.http.aclose()

    app = FastAPI(
        title="Presidio PII Proxy",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/__docs",  # not /docs — keep that namespace free for the upstream
    )

    @app.get("/__health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": cfg.mode.value,
            "upstream": cfg.upstream_url,
            "entities": list(cfg.entities),
        }

    # OpenWebUI also probes /v1/models on every page-load to populate the
    # picker. We pass that through unredacted (no user data in it).
    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
    async def proxy(path: str, request: Request) -> Response:
        upstream_path = f"{cfg.upstream_url}/{path}"

        # Read the body if there is one (POST/PUT/PATCH).
        body_bytes = await request.body()
        body_json: dict[str, Any] | None = None
        if body_bytes and request.headers.get("content-type", "").startswith("application/json"):
            try:
                body_json = httpx.Response(200, content=body_bytes).json()
            except Exception:
                body_json = None

        # Only chat/completion-style endpoints get the redaction treatment.
        # The list-models endpoint, embeddings probes, etc. flow through untouched.
        is_completion = path in ("chat/completions", "completions") and body_json is not None
        findings_for_log: list[RedactionResult] = []
        if is_completion:
            assert body_json is not None  # for the type checker
            body_json, findings_for_log = _redact_messages(body_json, redactor)
            any_findings = any(r.had_findings for r in findings_for_log)

            if any_findings:
                if cfg.mode is Mode.BLOCK:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": {
                                "type": "presidio_block",
                                "message": "PII detected; refused to forward (mode=BLOCK).",
                                "findings": [
                                    redactor.describe_findings(r.findings)
                                    for r in findings_for_log if r.had_findings
                                ],
                            }
                        },
                    )
                # In REDACT and WARN modes we log the (anonymized) finding metadata.
                # Crucially we log entity TYPES and POSITIONS, never the
                # original sensitive text — defense in depth in case the log
                # ever leaks.
                log.info(
                    "redaction-event mode=%s findings=%s",
                    cfg.mode.value,
                    [redactor.describe_findings(r.findings) for r in findings_for_log if r.had_findings],
                )
                if cfg.mode is Mode.WARN:
                    # WARN means we logged it but forward the ORIGINAL. Swap back.
                    for msg, r in zip(body_json.get("messages", []), findings_for_log):
                        if isinstance(msg.get("content"), str):
                            msg["content"] = r.original

        # Build outbound request: substitute our key, drop the client's.
        outbound_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("authorization", "host", "content-length")
        }
        if cfg.upstream_api_key:
            outbound_headers["Authorization"] = f"Bearer {cfg.upstream_api_key}"

        # Decide outbound body: JSON if we parsed it, otherwise raw bytes.
        if body_json is not None:
            content = httpx.Response(200, json=body_json).content
            outbound_headers["content-type"] = "application/json"
        else:
            content = body_bytes

        upstream_req = request.app.state.http.build_request(
            request.method, upstream_path, headers=outbound_headers, content=content
        )

        is_streaming = bool(body_json and body_json.get("stream"))

        if is_streaming:
            # Send the request, stream chunks back as they arrive.
            upstream_resp = await request.app.state.http.send(upstream_req, stream=True)
            return StreamingResponse(
                _stream_with_cleanup(upstream_resp),
                status_code=upstream_resp.status_code,
                headers=_filter_response_headers(upstream_resp.headers),
                media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
            )

        # Non-streaming: read fully, return.
        upstream_resp = await request.app.state.http.send(upstream_req)
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=_filter_response_headers(upstream_resp.headers),
            media_type=upstream_resp.headers.get("content-type"),
        )

    return app


async def _stream_with_cleanup(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """Yield upstream bytes, ensure connection closes even on client disconnect."""
    try:
        async for chunk in upstream.aiter_raw():
            yield chunk
    finally:
        await upstream.aclose()


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Strip hop-by-hop and length headers that would confuse our own server."""
    skip = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    return {k: v for k, v in headers.items() if k.lower() not in skip}


# Default instance for `uvicorn app:app` invocation.
app = create_app()
