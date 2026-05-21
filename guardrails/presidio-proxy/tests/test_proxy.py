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
from config import Config, Mode


def _make_config(mode: Mode = Mode.REDACT) -> Config:
    return Config(
        mode=mode,
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
