"""End-to-end-ish tests on the proxy with a mocked upstream.

Uses respx to intercept httpx calls so the tests never hit the real
OpenRouter (or any) network.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import create_app
from config import Config, Mode, ResponseMode


def _make_config(
    mode: Mode = Mode.REDACT,
    response_mode: ResponseMode = ResponseMode.REDACT,
) -> Config:
    return Config(
        mode=mode,
        response_mode=response_mode,
        port=8000,
        upstream_url="https://upstream.test/v1",
        upstream_api_key="fake-upstream-key",
        entities=("EMAIL_ADDRESS", "PHONE_NUMBER", "API_KEY"),
        score_threshold=0.4,
    )


@pytest.fixture
def client_redact():
    app = create_app(_make_config(Mode.REDACT))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_block():
    app = create_app(_make_config(Mode.BLOCK))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_response_off():
    app = create_app(_make_config(response_mode=ResponseMode.OFF))
    with TestClient(app) as c:
        yield c


@respx.mock
def test_health_endpoint(client_redact: TestClient) -> None:
    r = client_redact.get("/__health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "REDACT"


@respx.mock
def test_email_is_redacted_before_upstream_sees_it(client_redact: TestClient) -> None:
    """The proxy MUST replace the email in the outbound request body."""
    captured: dict = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    respx.post("https://upstream.test/v1/chat/completions").mock(side_effect=upstream_handler)

    r = client_redact.post(
        "/v1/chat/completions",
        json={
            "model": "openai/gpt-4o",
            "messages": [
                {"role": "user", "content": "please email me at leaky@example.com"},
            ],
        },
        headers={"Authorization": "Bearer client-junk-token"},
    )
    assert r.status_code == 200

    # Upstream-side assertions
    sent_message = captured["body"]["messages"][0]["content"]
    assert "leaky@example.com" not in sent_message, "raw email leaked through"
    assert "[EMAIL_ADDRESS]" in sent_message

    # The proxy must replace the client's bogus auth with our real one.
    assert captured["auth"] == "Bearer fake-upstream-key"


@respx.mock
def test_block_mode_returns_400_without_calling_upstream(client_block: TestClient) -> None:
    """BLOCK mode must short-circuit; the upstream should never be reached."""
    upstream_route = respx.post("https://upstream.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    r = client_block.post(
        "/v1/chat/completions",
        json={
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "leak: sk-or-v1-aaaaaaaaaaaaaaaaaaaaaaaaa"}],
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["type"] == "presidio_block"
    assert upstream_route.call_count == 0, "BLOCK mode must not call upstream"


@respx.mock
def test_clean_prompt_passes_through_untouched(client_redact: TestClient) -> None:
    """No PII -> request body unchanged."""
    captured: dict = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    respx.post("https://upstream.test/v1/chat/completions").mock(side_effect=upstream_handler)

    original_message = "what's the capital of France?"
    client_redact.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": original_message}]},
    )
    assert captured["body"]["messages"][0]["content"] == original_message


@respx.mock
def test_list_models_endpoint_is_not_redacted(client_redact: TestClient) -> None:
    """/v1/models is a metadata endpoint; we pass it through with no parsing."""
    respx.get("https://upstream.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    r = client_redact.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == {"data": [{"id": "gpt-4o"}]}


# --------------------------------------------------------------------------- #
# Response-side scanning (v0.2)
# --------------------------------------------------------------------------- #

@respx.mock
def test_email_in_non_streaming_response_is_redacted(
    client_redact: TestClient,
) -> None:
    """An upstream that echoes PII back (e.g. a vision model reading a
    screenshot of an inbox) must not leak that PII to the client."""
    respx.post("https://upstream.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "I see the email leaky@example.com in that screenshot.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    r = client_redact.post(
        "/v1/chat/completions",
        json={
            "model": "x",
            "messages": [{"role": "user", "content": "what does the picture say?"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    assert "leaky@example.com" not in content, "email leaked back to client"
    assert "[EMAIL_ADDRESS]" in content


@respx.mock
def test_response_mode_off_passes_pii_through(
    client_response_off: TestClient,
) -> None:
    """Explicit OFF should mirror v0.1 behavior — response untouched."""
    respx.post("https://upstream.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "echo: leaky@example.com",
                        },
                    }
                ]
            },
        )
    )
    r = client_response_off.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert "leaky@example.com" in r.json()["choices"][0]["message"]["content"]


@respx.mock
def test_streaming_response_pii_is_redacted(client_redact: TestClient) -> None:
    """Streaming path: the upstream emits an email split across two SSE
    deltas; the client-facing reconstruction must not contain it."""
    chunks = [
        b'data: {"choices":[{"index":0,"delta":{"content":"contact me at leaky"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"@example.com please"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"".join(chunks),
            headers={"content-type": "text/event-stream"},
        )

    respx.post("https://upstream.test/v1/chat/completions").mock(side_effect=upstream_handler)

    with client_redact.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "x",
            "stream": True,
            "messages": [{"role": "user", "content": "say hi"}],
        },
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())

    # Reassemble the streamed content the way a client would.
    reconstructed = ""
    for line in body.decode().split("\n"):
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        obj = json.loads(payload)
        for ch in obj.get("choices", []):
            reconstructed += (ch.get("delta") or {}).get("content") or ""

    assert "leaky@example.com" not in reconstructed, (
        f"split PII leaked through streaming proxy: {reconstructed!r}"
    )
    assert "[EMAIL_ADDRESS]" in reconstructed
    assert b"[DONE]" in body
