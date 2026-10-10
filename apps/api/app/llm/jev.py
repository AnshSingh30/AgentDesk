"""TypeSafe JEV (System One): typed judgments (noul / choice / score) for routing and classification.

Cheap and fast compared to a chat model: $0.042 per million input tokens, output tokens free.
POST {base}/v1/systemone with {state, model, questions} -> {model, answers, usage}. Docs: docs.typesafe.ai
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.usage.service import UsageRecord

PRICE_IN_PER_M = 0.042


class JevError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.jev_api_key)


async def ask(
    state: str | dict[str, Any] | list[str], questions: dict[str, dict[str, Any]], *, node: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (answers by question id, usage record dict). Retries 429/529 with backoff."""
    body = {"state": state, "model": settings.jev_model, "questions": questions}
    headers = {"Authorization": f"Bearer {settings.jev_api_key}"}
    async with httpx.AsyncClient(base_url=settings.jev_base_url, timeout=10.0) as client:
        for attempt in range(3):
            try:
                resp = await client.post("/v1/systemone", json=body, headers=headers)
            except httpx.HTTPError as e:
                raise JevError(f"unreachable: {e}") from e
            if resp.status_code in (429, 529) and attempt < 2:
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code != 200:
                raise JevError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            break
    u = data.get("usage") or {}
    tokens_in, tokens_out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
    usage = UsageRecord(
        kind="llm",
        model=str(data.get("model") or settings.jev_model),
        provider="typesafe",
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        est_cost_usd=tokens_in * PRICE_IN_PER_M / 1e6,
        node=node,
    ).as_dict()
    return data["answers"], usage
