"""Output guardrails (PRD §13.2): no-answer normalisation, prompt leakage, secret scrub, optional grounding check."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.llm.gateway import ResolvedModel, structured
from app.runtime.prompts import NO_ANSWER_TOKEN

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"(postgres(?:ql)?|mysql|mongodb(\+srv)?|redis)://[^\s:]+:[^\s@]+@[^\s]+", re.I),
    re.compile(r"\bEAA[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
]
NO_ANSWER_HINTS = re.compile(
    r"^\s*(i\s+(couldn't|could not|can't|cannot|don't|do not)\s+(find|have|see)\b.{0,80}(document|context|information|data))",
    re.I,
)


class GroundingCheck(BaseModel):
    grounded: bool
    issues: list[str] = Field(default_factory=list)


@dataclass
class OutputResult:
    answer: str
    no_answer: bool = False
    replaced: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


def _ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def leaks_system_prompt(answer: str, system_prompt: str, threshold: float = 0.4) -> bool:
    a = _ngrams(answer)
    if len(a) < 3:
        return False
    s = _ngrams(system_prompt)
    return len(a & s) / len(a) > threshold


def scrub_secrets(text: str) -> tuple[str, int]:
    count = 0
    for p in SECRET_PATTERNS:
        text, n = p.subn("[REDACTED]", text)
        count += n
    return text, count


def normalise_output(
    answer: str, *, no_answer_message: str, system_prompt: str, context_used: bool
) -> OutputResult:
    res = OutputResult(answer=(answer or "").strip())
    if (
        not res.answer
        or NO_ANSWER_TOKEN in res.answer
        or (context_used and NO_ANSWER_HINTS.match(res.answer))
    ):
        res.answer, res.no_answer, res.replaced = no_answer_message, True, True
        res.events.append({"check": "no_answer_normalisation", "action": "replace"})
        return res
    if leaks_system_prompt(res.answer, system_prompt):
        res.answer, res.replaced = (
            "I can't share my configuration or instructions, but I'm happy to help with your question.",
            True,
        )
        res.events.append({"check": "system_prompt_leakage", "action": "replace"})
        return res
    scrubbed, n = scrub_secrets(res.answer)
    if n:
        res.answer = scrubbed
        res.events.append({"check": "secret_patterns", "action": "redact", "detail": n})
    return res


async def grounding_check(
    answer: str, context: str, utility_model: ResolvedModel, metadata: dict[str, Any] | None = None
) -> tuple[bool, list[str], dict[str, Any] | None]:
    try:
        res, chat = await structured(
            utility_model,
            [
                {
                    "role": "system",
                    "content": "You verify whether an assistant's answer is fully supported by the provided context.",
                },
                {
                    "role": "user",
                    "content": f"CONTEXT:\n{context[:12000]}\n\nANSWER:\n{answer[:4000]}\n\nIs every factual claim in the answer supported by the context?",
                },
            ],
            GroundingCheck,
            max_tokens=300,
            metadata=metadata,
        )
        usage = {
            "kind": "llm",
            "model": utility_model.display,
            "provider": utility_model.provider,
            "credential_mode": utility_model.credential_mode,
            "input_tokens": chat.usage.input_tokens,
            "output_tokens": chat.usage.output_tokens,
            "est_cost_usd": chat.est_cost_usd,
            "node": "grounding_check",
            "units": 1,
        }
        return res.grounded, res.issues, usage
    except Exception as e:  # noqa: BLE001
        return True, [f"check failed: {e}"], None
