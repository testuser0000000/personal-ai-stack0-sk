"""PII detection and redaction.

Wraps Microsoft Presidio with one custom recognizer for API-key-shaped
tokens (sk-*, gsk_*, ghp_*, AKIA*, AIza*, etc.) — Presidio doesn't ship
one for these, and they're the highest-value class of secret to keep
out of cloud LLM prompts.

Why separate from app.py: the redaction logic is the security-critical
part. Isolating it makes it independently testable, and a reviewer can
audit redactor.py without reading the FastAPI plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from presidio_analyzer import (
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


# Patterns for common API-key formats. Score ≥0.85 reflects high confidence:
# these patterns are specific enough that false positives are rare.
# When adding new providers, prefer narrower regexes — false positives are
# more annoying than a slightly more permissive pattern, but be aware that
# "more permissive" means accidentally redacting things like git hashes.
_API_KEY_PATTERNS: list[Pattern] = [
    Pattern("openai-key",      r"\bsk-[A-Za-z0-9_-]{20,}\b",        0.85),
    Pattern("anthropic-key",   r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",    0.9),
    Pattern("openrouter-key",  r"\bsk-or-v\d-[A-Za-z0-9_-]{20,}\b", 0.9),
    Pattern("groq-key",        r"\bgsk_[A-Za-z0-9]{30,}\b",         0.9),
    Pattern("github-pat",      r"\bghp_[A-Za-z0-9]{30,}\b",         0.9),
    Pattern("github-other",    r"\bgh[osur]_[A-Za-z0-9]{30,}\b",    0.85),
    Pattern("aws-access-key",  r"\bAKIA[0-9A-Z]{16}\b",             0.9),
    Pattern("google-key",      r"\bAIza[0-9A-Za-z_-]{30,}\b",       0.85),
    Pattern("slack-token",     r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b", 0.85),
]


def _build_api_key_recognizer() -> PatternRecognizer:
    """A single recognizer that fires on any of the API-key formats."""
    return PatternRecognizer(
        supported_entity="API_KEY",
        name="ApiKeyRecognizer",
        patterns=_API_KEY_PATTERNS,
        context=["api", "key", "token", "secret", "auth"],
    )


def _build_analyzer() -> AnalyzerEngine:
    """Construct an AnalyzerEngine with the small English spaCy model.

    We use 'sm' instead of 'lg' to keep the container small (~50 MB vs
    ~750 MB). The regex-based recognizers (emails, phones, credit cards,
    IPs, our API-key recognizer) don't depend on spaCy quality. Only NER
    entities (PERSON, LOCATION, ORG) lose accuracy with 'sm', and those
    are off by default in our config.
    """
    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
    analyzer.registry.add_recognizer(_build_api_key_recognizer())
    return analyzer


@dataclass
class RedactionResult:
    """What happened to one string."""
    original: str
    redacted: str
    findings: tuple[RecognizerResult, ...]

    @property
    def had_findings(self) -> bool:
        return len(self.findings) > 0


class Redactor:
    """Stateless wrapper around Presidio analyzer + anonymizer."""

    def __init__(self, entities: Iterable[str], score_threshold: float):
        self._analyzer = _build_analyzer()
        self._anonymizer = AnonymizerEngine()
        self._entities = list(entities)
        self._threshold = score_threshold

    def redact(self, text: str) -> RedactionResult:
        if not text or not text.strip():
            return RedactionResult(text, text, ())

        findings = tuple(
            self._analyzer.analyze(
                text=text,
                language="en",
                entities=self._entities,
                score_threshold=self._threshold,
            )
        )
        if not findings:
            return RedactionResult(text, text, ())

        # Replace each detected entity with [ENTITY_TYPE], e.g. [EMAIL_ADDRESS].
        # The exact placeholder doesn't matter for security; what matters is
        # that the upstream LLM sees a clearly marked redaction rather than
        # the original sensitive value.
        operators = {
            entity_type: OperatorConfig("replace", {"new_value": f"[{entity_type}]"})
            for entity_type in {f.entity_type for f in findings}
        }
        anonymized = self._anonymizer.anonymize(
            text=text, analyzer_results=list(findings), operators=operators
        )
        return RedactionResult(
            original=text,
            redacted=anonymized.text,
            findings=findings,
        )

    def describe_findings(
        self, findings: Iterable[RecognizerResult]
    ) -> list[dict[str, object]]:
        """Convert findings to a plain-dict structure for logs / error responses."""
        return [
            {
                "entity_type": f.entity_type,
                "start": f.start,
                "end": f.end,
                "score": round(f.score, 3),
            }
            for f in findings
        ]
