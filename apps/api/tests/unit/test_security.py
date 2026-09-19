import base64
import hashlib
import hmac
import os

import pytest
from app.core.crypto import decrypt_secret, encrypt_secret, last4
from app.core.ids import uuid7
from app.core.logging import _redact
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)


def test_argon2id_password_hashing() -> None:
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)


def test_access_token_roundtrip_and_type() -> None:
    tok = create_access_token("user-1")
    payload = decode_access_token(tok)
    assert payload and payload["sub"] == "user-1" and payload["typ"] == "access"
    assert decode_access_token(tok + "x") is None


def test_opaque_tokens_are_hashed() -> None:
    raw, h = new_opaque_token()
    assert h == hash_token(raw) and len(h) == 64 and raw not in h


def test_envelope_encryption_roundtrip_and_rotation() -> None:
    k1, k2 = os.urandom(32), os.urandom(32)
    blob = encrypt_secret("postgres-password", keys=[k1])
    assert blob.startswith("v1:0:") and "postgres-password" not in blob
    assert decrypt_secret(blob, keys=[k1]) == "postgres-password"
    assert decrypt_secret(blob, keys=[k2, k1]) == "postgres-password"  # rotated: old key still listed
    with pytest.raises(ValueError):
        decrypt_secret(blob, keys=[k2])
    assert last4("sk-abcdef") == "cdef"


def test_uuid7_is_time_ordered() -> None:
    a, b = uuid7(), uuid7()
    assert a.version == 7 and b.int > a.int


def test_log_redaction() -> None:
    out = _redact(
        {
            "api_key": "sk-123",
            "nested": {"password": "x", "ok": "fine"},
            "msg": "token sk-abcdefghijklmnopqrstuvwxyz",
        }
    )
    assert (
        out["api_key"] == "[REDACTED]"
        and out["nested"]["password"] == "[REDACTED]"
        and out["nested"]["ok"] == "fine"
    )
    assert "sk-abcdef" not in out["msg"]


def test_webhook_signature_scheme() -> None:
    secret, body = "app-secret", b'{"entry":[]}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
    assert not hmac.compare_digest(sig, "sha256=" + hmac.new(b"other", body, hashlib.sha256).hexdigest())


def test_master_key_validation() -> None:
    from app.core.config import Settings

    good = base64.b64encode(os.urandom(32)).decode()
    s = Settings(encryption_master_keys=good, app_env="local")
    assert len(s.master_keys) == 1
    with pytest.raises(ValueError):
        _ = Settings(
            app_env="production",
            jwt_secret="x" * 40,
            csrf_secret="y",
            encryption_master_keys=good,
            s3_secret_key="minioadmin",
        ).master_keys


def test_quote_ident_escapes_embedded_quotes() -> None:
    from app.sql.introspect import quote_ident

    assert quote_ident("orders") == '"orders"'
    assert quote_ident('x"; DROP TABLE t; --') == '"x""; DROP TABLE t; --"'
