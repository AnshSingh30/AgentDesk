"""Tavily web search (PRD §6.7)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger()


@dataclass
class WebResult:
    title: str
    url: str
    content: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WebSearchError(Exception):
    pass


async def search(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = 5,
    depth: str = "basic",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[WebResult]:
    key = api_key or settings.tavily_api_key
    if not key:
        raise WebSearchError("Web search is not configured (missing Tavily API key)")
    payload: dict[str, Any] = {
        "query": query[:400],
        "search_depth": "advanced" if depth == "advanced" else "basic",
        "max_results": max(1, min(max_results, 10)),
        "include_answer": False,
        "include_raw_content": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains[:50]
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains[:50]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.tavily.com/search", json=payload, headers={"Authorization": f"Bearer {key}"}
            )
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise WebSearchError(f"Tavily error {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise WebSearchError(f"Tavily unreachable: {e}") from e
    return [
        WebResult(
            title=(item.get("title") or "")[:200],
            url=item.get("url") or "",
            content=(item.get("content") or "")[:1500],
            score=float(item.get("score") or 0),
        )
        for item in r.json().get("results", [])
    ][:max_results]
