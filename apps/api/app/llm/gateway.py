"""LLM gateway over LiteLLM: model resolution (platform / BYO / custom), chat, streaming,
structured output with repair, error mapping, usage capture, per-org concurrency (PRD §12)."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import litellm
import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.errors import Problem
from app.core.redis import get_redis
from app.core.ssrf import validate_public_url
from app.db.models import ProviderCredential
from app.llm import fake
from app.llm.catalog import default_model_for, model_info
from app.usage.service import UsageRecord

log = structlog.get_logger()
litellm.drop_params = True
litellm.suppress_debug_info = True
litellm.set_verbose = False
if settings.langsmith_tracing and settings.langsmith_api_key:
    import os

    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    litellm.success_callback = ["langsmith"]

T = TypeVar("T", bound=BaseModel)
PREFIXES = {
    "openai": "openai/",
    "anthropic": "anthropic/",
    "gemini": "gemini/",
    "groq": "groq/",
    "openrouter": "openrouter/",
    "custom": "openai/",
    "fake": "fake/",
}


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


@dataclass
class ResolvedModel:
    model: str
    provider: str
    credential_mode: str
    api_key: str | None = None
    api_base: str | None = None
    context_window: int = 128_000
    supports_tools: bool = True
    supports_json: bool = True
    price_in_per_m: float | None = None
    price_out_per_m: float | None = None

    @property
    def is_fake(self) -> bool:
        return self.provider == "fake"

    @property
    def display(self) -> str:
        return (
            self.model.split("/", 1)[-1]
            if self.provider != "custom"
            else f"custom:{self.model.split('/', 1)[-1]}"
        )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatResult:
    content: str
    usage: Usage
    model: str
    est_cost_usd: float = 0.0
    finish_reason: str | None = None


def _platform_key(provider: str) -> str | None:
    return {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
        "groq": settings.groq_api_key,
        "openrouter": settings.openrouter_api_key,
    }.get(provider)


def _split(model_string: str) -> tuple[str, str]:
    if "/" in model_string:
        p, m = model_string.split("/", 1)
        if p in PREFIXES:
            return p, m
    return "openai", model_string


def platform_model(utility: bool = False) -> ResolvedModel:
    provider, model_id = _split(settings.utility_model if utility else settings.default_chat_model)
    info = model_info(provider, model_id) or {}
    return ResolvedModel(
        model=f"{PREFIXES[provider]}{model_id}",
        provider=provider,
        credential_mode="platform",
        api_key=_platform_key(provider),
        context_window=info.get("context", 128_000),
        supports_tools=info.get("supports_tools", True),
        supports_json=info.get("supports_json_schema", True),
    )


async def resolve_model(
    session: AsyncSession | None,
    org_id: uuid.UUID | None,
    model_cfg: dict[str, Any] | None,
    *,
    utility: bool = False,
) -> ResolvedModel:
    cfg = model_cfg or {}
    mode = cfg.get("mode", "platform")
    if mode == "platform":
        rm = platform_model(utility)
        if utility and cfg.get("utility_model"):
            u_provider, u_model = _split(cfg["utility_model"])
            rm = ResolvedModel(
                model=f"{PREFIXES[u_provider]}{u_model}",
                provider=u_provider,
                credential_mode="platform",
                api_key=_platform_key(u_provider),
            )
        return rm
    cred: ProviderCredential | None = None
    if cfg.get("credential_id") and session is not None:
        cred = await session.get(ProviderCredential, uuid.UUID(str(cfg["credential_id"])))
        if cred is None or (org_id is not None and cred.org_id != org_id):
            raise LLMError("invalid_key", "The selected API key no longer exists")
    api_key = decrypt_secret(cred.encrypted_api_key) if cred else None
    if mode == "byo":
        provider = str(cfg.get("provider") or (cred.provider if cred else "") or "")
        if provider not in PREFIXES or provider in ("custom", "fake"):
            raise LLMError("model_not_found", "Choose a provider for the bring-your-own-key model")
        chosen = (
            cfg.get("utility_model") if utility and cfg.get("utility_model") else cfg.get("model")
        ) or default_model_for(provider)
        if not chosen:
            raise LLMError("model_not_found", "Choose a model")
        model_id = str(chosen)
        info = model_info(provider, model_id) or {}
        return ResolvedModel(
            model=f"{PREFIXES[provider]}{model_id}",
            provider=provider,
            credential_mode="byo",
            api_key=api_key,
            api_base=cred.api_base if cred else None,
            context_window=info.get("context", 128_000),
            supports_tools=info.get("supports_tools", True),
            supports_json=info.get("supports_json_schema", True),
        )
    if mode == "custom":
        api_base = cfg.get("api_base") or (cred.api_base if cred else None)
        if not api_base:
            raise LLMError("unreachable", "Custom endpoint base URL is required")
        validate_public_url(api_base)
        custom_model = cfg.get("model")
        if not custom_model:
            raise LLMError("model_not_found", "Model name is required for custom endpoints")
        return ResolvedModel(
            model=f"openai/{custom_model}",
            provider="custom",
            credential_mode="custom",
            api_key=api_key or "sk-no-key",
            api_base=api_base,
            context_window=int(cfg.get("context_window") or 32_000),
            supports_tools=False,
            supports_json=False,
            price_in_per_m=cfg.get("price_in_per_m"),
            price_out_per_m=cfg.get("price_out_per_m"),
        )
    raise LLMError("model_not_found", f"Unknown model mode '{mode}'")


def map_exception(e: Exception) -> LLMError:
    name = type(e).__name__
    msg = str(e)[:300]
    if isinstance(e, litellm.AuthenticationError) or "AuthenticationError" in name:
        return LLMError("invalid_key", "The API key was rejected by the provider")
    if isinstance(e, litellm.NotFoundError) or "NotFoundError" in name:
        return LLMError("model_not_found", "The model was not found at the provider")
    if isinstance(e, litellm.RateLimitError) or "RateLimit" in name:
        return LLMError("rate_limited", "The provider is rate limiting requests", retryable=True)
    if (
        isinstance(e, litellm.Timeout | litellm.APIConnectionError)
        or "Timeout" in name
        or "Connection" in name
    ):
        return LLMError("unreachable", f"Could not reach the model provider ({msg})", retryable=True)
    if isinstance(e, litellm.ContextWindowExceededError) or "ContextWindow" in name:
        return LLMError("context_exceeded", "The prompt is too long for this model")
    if isinstance(e, litellm.BadRequestError) or "BadRequest" in name:
        return LLMError("bad_request", f"The provider rejected the request ({msg})")
    if isinstance(e, litellm.InternalServerError | litellm.ServiceUnavailableError):
        return LLMError("provider_error", "The provider had an internal error", retryable=True)
    return LLMError("provider_error", f"{name}: {msg}", retryable=False)


def _cost(resp: Any, rm: ResolvedModel, usage: Usage) -> float:
    if rm.provider == "custom":
        if rm.price_in_per_m or rm.price_out_per_m:
            return (
                usage.input_tokens * (rm.price_in_per_m or 0)
                + usage.output_tokens * (rm.price_out_per_m or 0)
            ) / 1e6
        return 0.0
    try:
        return float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:  # noqa: BLE001 - unknown model in the price map
        return 0.0


def _usage_from(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if not u:
        return Usage()
    return Usage(int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0))


def usage_record(rm: ResolvedModel, result: ChatResult, node: str | None = None) -> UsageRecord:
    return UsageRecord(
        kind="llm",
        model=rm.display,
        provider=rm.provider,
        credential_mode=rm.credential_mode,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        est_cost_usd=result.est_cost_usd,
        node=node,
    )


def _kwargs(
    rm: ResolvedModel,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    metadata: dict[str, Any] | None,
    timeout: float | None,
) -> dict[str, Any]:
    kw: dict[str, Any] = dict(
        model=rm.model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout or settings.llm_timeout_seconds,
        num_retries=2,
        metadata=metadata or {},
    )
    if rm.api_key:
        kw["api_key"] = rm.api_key
    if rm.api_base:
        kw["api_base"] = rm.api_base
    return kw


async def chat(
    rm: ResolvedModel,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    response_format: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> ChatResult:
    if rm.is_fake:
        content = fake.fake_chat(messages)
        return ChatResult(
            content=content, usage=Usage(len(json.dumps(messages)) // 4, len(content) // 4), model=rm.model
        )
    kw = _kwargs(rm, messages, temperature, max_tokens, metadata, timeout)
    if response_format and rm.supports_json:
        kw["response_format"] = response_format
    try:
        resp = await litellm.acompletion(**kw)
    except Exception as e:  # noqa: BLE001
        raise map_exception(e) from e
    usage = _usage_from(resp)
    choice = resp.choices[0]
    return ChatResult(
        content=choice.message.content or "",
        usage=usage,
        model=rm.model,
        est_cost_usd=_cost(resp, rm, usage),
        finish_reason=getattr(choice, "finish_reason", None),
    )


@dataclass
class StreamDone:
    result: ChatResult


async def stream_chat(
    rm: ResolvedModel,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    metadata: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> AsyncIterator[str | StreamDone]:
    """Yields text deltas, then a final StreamDone with usage."""
    if rm.is_fake:
        content = fake.fake_chat(messages)
        for i in range(0, len(content), 12):
            yield content[i : i + 12]
            await asyncio.sleep(0.01)
        yield StreamDone(
            ChatResult(
                content=content,
                usage=Usage(len(json.dumps(messages)) // 4, len(content) // 4),
                model=rm.model,
            )
        )
        return
    kw = _kwargs(rm, messages, temperature, max_tokens, metadata, timeout)
    parts: list[str] = []
    usage = Usage()
    try:
        stream = await litellm.acompletion(stream=True, stream_options={"include_usage": True}, **kw)
        async for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u:
                usage = Usage(
                    int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)
                )
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                text = getattr(delta, "content", None) if delta else None
                if text:
                    parts.append(text)
                    yield text
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001
        raise map_exception(e) from e
    content = "".join(parts)
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        try:
            usage = Usage(
                litellm.token_counter(model=rm.model, messages=messages),
                litellm.token_counter(model=rm.model, text=content),
            )
        except Exception:  # noqa: BLE001
            usage = Usage(len(json.dumps(messages)) // 4, len(content) // 4)
    cost = 0.0
    try:
        cost = float(
            litellm.completion_cost(
                model=rm.model,
                prompt="",
                completion="",
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
            )
            or 0
        )
    except Exception:  # noqa: BLE001
        pass
    yield StreamDone(ChatResult(content=content, usage=usage, model=rm.model, est_cost_usd=cost))


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> str:
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


async def structured(
    rm: ResolvedModel,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    metadata: dict[str, Any] | None = None,
) -> tuple[T, ChatResult]:
    """JSON-mode + Pydantic validation + one repair retry (PRD §12)."""
    json_schema = schema.model_json_schema()
    if rm.is_fake:
        data = fake.fake_structured(messages, schema.__name__, json_schema)
        content = json.dumps(data)
        return schema.model_validate(data), ChatResult(
            content=content, usage=Usage(len(json.dumps(messages)) // 4, len(content) // 4), model=rm.model
        )
    hint = (
        "\n\nRespond with ONLY a single JSON object (no prose, no code fences) that matches this JSON Schema:\n"
        + json.dumps(json_schema)
    )
    msgs = [dict(m) for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user":
            msgs[i]["content"] = str(msgs[i]["content"]) + hint
            break
    else:
        msgs.append({"role": "user", "content": hint.strip()})
    result = await chat(
        rm,
        msgs,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        metadata=metadata,
    )
    try:
        return schema.model_validate_json(extract_json(result.content)), result
    except (ValidationError, ValueError) as first_err:
        repair = msgs + [
            {"role": "assistant", "content": result.content},
            {
                "role": "user",
                "content": f"That was not valid: {str(first_err)[:500]}. "
                "Return the corrected JSON object only.",
            },
        ]
        result2 = await chat(
            rm,
            repair,
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            metadata=metadata,
        )
        result2.usage = Usage(
            result.usage.input_tokens + result2.usage.input_tokens,
            result.usage.output_tokens + result2.usage.output_tokens,
        )
        result2.est_cost_usd += result.est_cost_usd
        try:
            return schema.model_validate_json(extract_json(result2.content)), result2
        except (ValidationError, ValueError) as e:
            raise LLMError(
                "invalid_structured_output", f"Model did not return valid {schema.__name__}"
            ) from e


async def test_model_config(
    session: AsyncSession | None, org_id: uuid.UUID | None, model_cfg: dict[str, Any]
) -> dict[str, Any]:
    """1-token call with clear error mapping (MD-6) and capability info (MD-7)."""
    try:
        rm = await resolve_model(session, org_id, model_cfg)
        if rm.provider == "custom" and not model_cfg.get("model"):
            return {"ok": False, "code": "model_not_found", "message": "Model name is required"}
        result = await chat(
            rm,
            [{"role": "user", "content": "Reply with the single word OK."}],
            max_tokens=5,
            temperature=0,
            timeout=20,
        )
        info = model_info(rm.provider, rm.model.split("/", 1)[-1]) if rm.provider != "custom" else None
        warnings = []
        if info is None and not rm.is_fake:
            warnings.append("Unknown model: tool calling / structured output support could not be verified.")
        return {
            "ok": True,
            "model": rm.display,
            "provider": rm.provider,
            "sample": result.content[:40],
            "supports_tools": rm.supports_tools,
            "supports_json_schema": rm.supports_json,
            "warnings": warnings,
        }
    except LLMError as e:
        return {"ok": False, "code": e.code, "message": e.message}
    except Problem as e:
        return {"ok": False, "code": "invalid_endpoint", "message": e.detail or e.title}


@asynccontextmanager
async def org_llm_slot(org_id: uuid.UUID | str) -> AsyncIterator[None]:
    """Per-org concurrency limiter for platform keys (Redis counter, PRD §12)."""
    r = get_redis()
    key = f"llm:sem:{org_id}"
    acquired = False
    for _ in range(100):  # wait up to ~20 s
        n = await r.incr(key)
        await r.expire(key, 180)
        if n <= settings.llm_concurrency_per_org:
            acquired = True
            break
        await r.decr(key)
        await asyncio.sleep(0.2)
    if not acquired:
        raise Problem(
            429,
            "Too many concurrent AI requests for this organisation; try again shortly",
            headers={"Retry-After": "5"},
        )
    try:
        yield
    finally:
        await r.decr(key)
