"""S3/MinIO object storage: presigned uploads/downloads and byte access (PRD §6.5, §18.3)."""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ \-()]+")


def sanitize_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = SAFE_FILENAME.sub("_", name)[:180]
    return name or "file"


def _client(endpoint: str | None = None) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def internal_client() -> Any:
    return _client()


@lru_cache
def public_client() -> Any:
    return _client(settings.s3_public_endpoint or settings.s3_endpoint)


async def ensure_bucket() -> None:
    def _do() -> None:
        c = internal_client()
        try:
            c.head_bucket(Bucket=settings.s3_bucket)
        except ClientError:
            c.create_bucket(Bucket=settings.s3_bucket)

    await asyncio.to_thread(_do)


async def presign_put(key: str, content_type: str, content_length: int, expires: int = 900) -> str:
    def _do() -> str:
        return public_client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )

    return await asyncio.to_thread(_do)


async def presign_get(key: str, filename: str, expires: int = 300) -> str:
    def _do() -> str:
        return public_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{sanitize_filename(filename)}"',
            },
            ExpiresIn=expires,
        )

    return await asyncio.to_thread(_do)


async def head_object(key: str) -> dict[str, Any] | None:
    def _do() -> dict[str, Any] | None:
        try:
            return internal_client().head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError:
            return None

    return await asyncio.to_thread(_do)


async def get_bytes(key: str, byte_range: str | None = None) -> bytes:
    def _do() -> bytes:
        kwargs: dict[str, Any] = {"Bucket": settings.s3_bucket, "Key": key}
        if byte_range:
            kwargs["Range"] = byte_range
        return internal_client().get_object(**kwargs)["Body"].read()

    return await asyncio.to_thread(_do)


async def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    await asyncio.to_thread(
        lambda: internal_client().put_object(
            Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
        )
    )


async def delete_object(key: str) -> None:
    await asyncio.to_thread(lambda: internal_client().delete_object(Bucket=settings.s3_bucket, Key=key))


async def download_to_path(key: str, path: str) -> None:
    await asyncio.to_thread(lambda: internal_client().download_file(settings.s3_bucket, key, path))


async def upload_from_path(path: str, key: str) -> None:
    await asyncio.to_thread(lambda: internal_client().upload_file(path, settings.s3_bucket, key))


async def org_storage_bytes(org_id: str) -> int:
    def _do() -> int:
        total = 0
        paginator = internal_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=f"orgs/{org_id}/"):
            for obj in page.get("Contents", []):
                total += obj.get("Size", 0)
        return total

    return await asyncio.to_thread(_do)


MAGIC = {
    "application/pdf": [b"%PDF"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [b"PK\x03\x04"],
}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv"}


def sniff_matches(declared_mime: str, head: bytes) -> bool:
    """Validate MIME by magic bytes, not extension (KB-3)."""
    if declared_mime in MAGIC:
        return any(head.startswith(sig) for sig in MAGIC[declared_mime])
    if declared_mime in TEXT_TYPES:
        if b"\x00" in head[:512]:
            return False
        try:
            head[:512].decode("utf-8")
            return True
        except UnicodeDecodeError:
            try:
                head[:512].decode("latin-1")
                return True
            except UnicodeDecodeError:
                return False
    return False
