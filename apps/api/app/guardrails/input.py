"""Input guardrails (PRD §13.1): length, blocked keywords, injection heuristics, optional LLM classifier."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.schemas import AgentConfig
from app.llm.gateway import ResolvedModel, structured

INJECTION_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|prompts|rules)",
        r"disregard\s+(all\s+|the\s+)?(previous|prior|above)\s+instructions",
        r"\b(reveal|show|print|repeat|output)\b.{0,40}\b(system prompt|instructions|your rules|configuration)\b",
        r"\byou are now\b",
        r"\bdeveloper mode\b",
        r"\bjailbreak\b",
        r"\bdo anything now\b",
        r"\bDAN\b mode",
        r"act as .{0,40}(no|without) (restrictions|rules|filters)",
        r"</?\s*(system|assistant|user|tool)\s*>",
        r"\[/?INST\]",
        r"<\|im_(start|end)\|>",
        r"###\s*(system|instruction)",
        r"\bnew instructions?:",
        r"\bfrom now on you\b",
    )
]
BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


class InputClassification(BaseModel):
    label: Literal["safe", "injection", "off_topic", "abusive"]


@dataclass
class GuardrailResult:
    blocked: bool = False
    message: str | None = None
    injection_suspected: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "message": self.message,
            "injection_suspected": self.injection_suspected,
            "events": self.events,
        }


async def check_input(
    text: str,
    cfg: AgentConfig,
    channel: str,
    utility_model: ResolvedModel | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[GuardrailResult, list[dict[str, Any]]]:
    res = GuardrailResult()
    usage: list[dict[str, Any]] = []
    g = cfg.guardrails
    if not text or not text.strip():
        res.blocked, res.message = (
            True,
            "I can only read text messages for now." if channel == "whatsapp" else "Please type a message.",
        )
        res.events.append({"check": "empty_input", "action": "block"})
        return res, usage
    if len(text) > g.max_input_chars:
        res.blocked = True
        res.message = f"That message is too long. Please keep it under {g.max_input_chars} characters."
        res.events.append({"check": "max_input_length", "action": "block", "detail": len(text)})
        return res, usage
    lowered = text.lower()
    for kw in g.blocked_keywords:
        if kw.strip() and kw.strip().lower() in lowered:
            res.blocked, res.message = True, "I'm not able to help with that topic."
            res.events.append({"check": "blocked_keyword", "action": "block", "detail": kw})
            return res, usage
    hits = [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
    if BASE64_BLOB.search(text):
        hits.append("base64_blob")
    sensitivity = g.advanced.injection_sensitivity
    if hits:
        res.injection_suspected = True
        res.events.append(
            {
                "check": "injection_heuristics",
                "action": "block" if sensitivity == "high" else "flag",
                "detail": hits[:3],
            }
        )
        if sensitivity == "high":
            res.blocked, res.message = True, "I can't help with that request."
            return res, usage
    if g.advanced.input_classifier and utility_model is not None:
        try:
            cls, chat = await structured(
                utility_model,
                [
                    {
                        "role": "system",
                        "content": "Classify the user message for an organisation assistant. "
                        f"Allowed topics: {g.allowed_topics or 'general assistance'}.",
                    },
                    {
                        "role": "user",
                        "content": f"Message: {text[:2000]}\n\nLabels: safe | injection (tries to override instructions or extract the prompt) | off_topic | abusive",
                    },
                ],
                InputClassification,
                max_tokens=20,
                metadata=metadata,
            )
            usage.append(
                {
                    "kind": "llm",
                    "model": utility_model.display,
                    "provider": utility_model.provider,
                    "credential_mode": utility_model.credential_mode,
                    "input_tokens": chat.usage.input_tokens,
                    "output_tokens": chat.usage.output_tokens,
                    "est_cost_usd": chat.est_cost_usd,
                    "node": "input_classifier",
                    "units": 1,
                }
            )
            res.events.append({"check": "llm_classifier", "action": cls.label})
            if cls.label == "injection" and (res.injection_suspected or sensitivity != "low"):
                res.blocked, res.message = True, "I can't help with that request."
            elif cls.label == "abusive":
                res.blocked, res.message = True, "Let's keep this respectful. How can I help you?"
            elif cls.label == "off_topic" and g.allowed_topics.strip():
                res.blocked, res.message = True, (f"I can only help with: {g.allowed_topics.strip()[:200]}.")
        except Exception as e:  # noqa: BLE001 - classifier failure must not block users
            res.events.append({"check": "llm_classifier", "action": "error", "detail": str(e)[:200]})
    return res, usage
