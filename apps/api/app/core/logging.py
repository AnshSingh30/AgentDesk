"""structlog JSON logging with secret redaction and request-id binding (PRD §18.2, §19.1)."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

SENSITIVE_KEY = re.compile(r"(token|secret|password|api_key|apikey|authorization|credential)", re.I)
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|postgres(?:ql)?://[^\s]+:[^\s]+@|EAA[A-Za-z0-9]{20,})"
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SENSITIVE_KEY.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[REDACTED]", value)
    return value


def redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _redact(event_dict)


def configure_logging(level: str = "INFO", service: str = "api", env: str = "local") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
        redact_processor,
    ]
    renderer: Any = structlog.processors.JSONRenderer()
    if env == "local" and sys.stderr.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service, env=env)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), stream=sys.stdout, format="%(message)s"
    )
    for noisy in ("uvicorn.access", "httpx", "httpcore", "LiteLLM", "litellm", "botocore", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
