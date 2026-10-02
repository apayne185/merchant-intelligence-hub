"""
Input guardrails for /ask: PII redaction and prompt-injection detection,
applied to the question before it reaches the router, the LLM, the cache
key or any log line.

PII: deterministic, validated patterns, not an LLM classifier. Card numbers
must pass the Luhn checksum (so a 16-digit order id is not redacted), US
SSNs exclude ranges the SSA never issues, phone numbers need 9-15 digits (so
ISO dates such as 2025-09-30, which this copilot sees constantly, survive).
Over-redaction is the safe failure direction.

Prompt injection: English and Spanish patterns, plus attempts to talk the
copilot out of its risk controls ("approve regardless of limits"). This is a
heuristic that raises the bar, not the security boundary. The boundary is
architectural: the router can only emit a closed set of tool names, no tool
executes model-generated code or SQL, pre-trade verdicts come from rules,
and every number in an answer is verified against tool evidence.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"\bdisregard\b.*\b(prompt|instructions)\b", re.IGNORECASE),
    re.compile(r"ignora(?:r)?\s+(?:todas\s+)?las\s+instrucciones\s+anteriores", re.IGNORECASE),
    re.compile(r"\bignora\b.*\b(prompt|instrucciones)\b", re.IGNORECASE),
    re.compile(
        r"\b(reveal|print|show|repeat|output)\b.{0,40}\b(system prompt|hidden prompt|your (instructions|prompt))\b",
        re.IGNORECASE,
    ),
    re.compile(r"<\|?\s*(im_start|im_end|system|endoftext)\s*\|?>", re.IGNORECASE),
    re.compile(r"\b(jailbreak|DAN mode|developer mode enabled)\b", re.IGNORECASE),
    re.compile(r"\boverride\b.{0,30}\b(safety|guardrails?|rules|restrictions)\b", re.IGNORECASE),
    re.compile(r"\byou are no longer\b", re.IGNORECASE),
    # Attempts to bypass the pre-trade controls through the prompt.
    re.compile(r"\b(approve|pass|accept)\b.{0,40}\b(regardless|anyway|ignoring)\b", re.IGNORECASE),
    re.compile(r"\b(skip|bypass|disable|ignore)\b.{0,20}\b(risk|limit|compliance)\s*(checks?|limits?|rules?)\b", re.IGNORECASE),
]


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_filter(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group(0))
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def _phone_filter(match: re.Match[str]) -> bool:
    return 9 <= sum(c.isdigit() for c in match.group(0)) <= 15


# Applied in this order, most specific first, so a card number is never
# half-consumed by the looser phone pattern (same ordering concern as
# agent.py's PII_PATTERNS comment). Each entry: (label, pattern, filter).
_PII_RULES: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool] | None]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), _card_filter),
    ("ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), None),
    # Spanish national id (DNI/NIE): 8 digits + letter, or X/Y/Z + 7 digits + letter.
    ("dni", re.compile(r"\b(?:\d{8}|[XYZ]\d{7})-?[A-Z]\b"), None),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"), None),
    ("phone", re.compile(r"(?<![\w+])\+?\d[\d\s().-]{7,17}\d(?!\d)"), _phone_filter),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def redact_pii(text: str) -> RedactionResult:
    """Replaces PII with `[LABEL]` placeholders and reports how many of each
    type were removed (counts feed metrics + the audit log; the values
    themselves are never recorded anywhere)."""
    counts: dict[str, int] = {}
    for label, pattern, keep in _PII_RULES:

        def _sub(
            m: re.Match[str], label: str = label, keep: Callable[[re.Match[str]], bool] | None = keep
        ) -> str:
            if keep is not None and not keep(m):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f"[{label.upper()}]"

        text = pattern.sub(_sub, text)
    return RedactionResult(text=text, counts=counts)


def detect_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


@dataclass(frozen=True)
class GuardrailDecision:
    blocked: bool
    reason: str | None
    redaction: RedactionResult


def check_input(text: str) -> GuardrailDecision:
    """Single entry point for /ask: injection check runs on the raw text
    (redaction can't hide an injection phrase, but checking raw avoids any
    chance a placeholder changes a match), redaction result is what flows
    downstream."""
    redaction = redact_pii(text)
    if detect_prompt_injection(text):
        return GuardrailDecision(blocked=True, reason="prompt_injection_detected", redaction=redaction)
    return GuardrailDecision(blocked=False, reason=None, redaction=redaction)
