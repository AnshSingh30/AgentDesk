"""Redis sliding-window rate limits with RateLimit-* headers (PRD §16.9)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request, Response

from app.core.errors import Problem
from app.core.redis import get_redis


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int

    def headers(self) -> dict[str, str]:
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(self.remaining, 0)),
            "RateLimit-Reset": str(self.reset_seconds),
        }


async def check_rate_limit(key: str, limit: int, window_seconds: int) -> RateLimitResult:
    r = get_redis()
    now = time.time()
    member = f"{now:.6f}"
    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        _, _, count, _ = await pipe.execute()
    if count > limit:
        await r.zrem(key, member)
        return RateLimitResult(False, limit, 0, window_seconds)
    return RateLimitResult(True, limit, limit - count, window_seconds)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(scope: str, limit: int, window_seconds: int, by: str = "ip") -> Callable:
    """Dependency factory. `by` is 'ip' or 'user' (falls back to ip when unauthenticated)."""

    async def dependency(request: Request, response: Response) -> None:
        ident = client_ip(request)
        if by == "user":
            user_id = getattr(request.state, "user_id", None)
            ident = f"u:{user_id}" if user_id else f"ip:{ident}"
        result = await check_rate_limit(f"rl:{scope}:{ident}", limit, window_seconds)
        for k, v in result.headers().items():
            response.headers[k] = v
        if not result.allowed:
            raise Problem(
                429, "Rate limit exceeded", headers={**result.headers(), "Retry-After": str(window_seconds)}
            )

    return dependency
