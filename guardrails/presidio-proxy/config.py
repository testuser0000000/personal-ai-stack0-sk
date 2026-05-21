"""Configuration loaded from environment variables.

Centralizes all env-var reads so the rest of the code never calls os.getenv.
That makes testing easy: pass a Config object built however the test wants.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    REDACT = "REDACT"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class Config:
    mode: Mode
    port: int
    upstream_url: str
    upstream_api_key: str
    entities: tuple[str, ...]
    score_threshold: float

    @classmethod
    def from_env(cls) -> "Config":
        mode_str = os.getenv("PRESIDIO_MODE", "REDACT").upper()
        try:
            mode = Mode(mode_str)
        except ValueError as e:
            raise ValueError(
                f"PRESIDIO_MODE must be one of {[m.value for m in Mode]}, got {mode_str!r}"
            ) from e

        entities_raw = os.getenv(
            "PRESIDIO_ENTITIES",
            "EMAIL_ADDRESS,PHONE_NUMBER,CREDIT_CARD,IP_ADDRESS,API_KEY",
        )
        entities = tuple(e.strip() for e in entities_raw.split(",") if e.strip())

        return cls(
            mode=mode,
            port=int(os.getenv("PRESIDIO_PROXY_PORT", "8000")),
            upstream_url=os.getenv(
                "PRESIDIO_UPSTREAM_URL", "https://openrouter.ai/api/v1"
            ).rstrip("/"),
            upstream_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            entities=entities,
            score_threshold=float(os.getenv("PRESIDIO_SCORE_THRESHOLD", "0.4")),
        )
