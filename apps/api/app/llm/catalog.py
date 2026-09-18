from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


@lru_cache
def load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open() as f:
        return yaml.safe_load(f)


def model_info(provider: str, model_id: str) -> dict[str, Any] | None:
    prov = load_catalog()["providers"].get(provider) or {}
    for m in prov.get("models", []):
        if m["id"] == model_id:
            return m
    return None


def default_model_for(provider: str) -> str | None:
    prov = load_catalog()["providers"].get(provider) or {}
    for m in prov.get("models", []):
        if m.get("default"):
            return m["id"]
    models = prov.get("models", [])
    return models[0]["id"] if models else None


def embedding_dim(model_id: str) -> int | None:
    for m in load_catalog().get("embedding_models", []):
        if m["id"] == model_id:
            return int(m["dim"])
    return None


def public_catalog() -> dict[str, Any]:
    cat = load_catalog()
    return {
        "providers": [
            {
                "key": k,
                "display": v["display"],
                "byo": v.get("key_env") is not None or k == "custom",
                "models": [
                    {
                        "id": m["id"],
                        "display": m["display"],
                        "context": m["context"],
                        "supports_tools": m.get("supports_tools", True),
                        "supports_json_schema": m.get("supports_json_schema", True),
                        "default": m.get("default", False),
                    }
                    for m in v.get("models", [])
                ],
            }
            for k, v in cat["providers"].items()
            if k != "fake"
        ],
        "embedding_models": cat.get("embedding_models", []),
    }
