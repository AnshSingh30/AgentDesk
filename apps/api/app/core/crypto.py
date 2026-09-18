"""Envelope encryption for customer secrets (PRD §18.2).

Format: v1:<key_index>:<b64 wrapped_dk>:<b64 nonce>:<b64 ciphertext>
Per-record AES-256-GCM data key wrapped by a master key from ENCRYPTION_MASTER_KEYS
(first key encrypts; any listed key decrypts, which allows rotation).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_PREFIX = "v1"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def encrypt_secret(plaintext: str, keys: list[bytes] | None = None) -> str:
    keys = keys or settings.master_keys
    master = keys[0]
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode(), None)
    wrap_nonce = os.urandom(12)
    wrapped = AESGCM(master).encrypt(wrap_nonce, data_key, None)
    return ":".join([_PREFIX, "0", _b64(wrap_nonce + wrapped), _b64(nonce), _b64(ciphertext)])


def decrypt_secret(blob: str, keys: list[bytes] | None = None) -> str:
    keys = keys or settings.master_keys
    try:
        prefix, key_index, wrapped_b64, nonce_b64, ct_b64 = blob.split(":")
    except ValueError as e:
        raise ValueError("malformed secret blob") from e
    if prefix != _PREFIX:
        raise ValueError("unsupported secret version")
    wrapped_all = _unb64(wrapped_b64)
    wrap_nonce, wrapped = wrapped_all[:12], wrapped_all[12:]
    candidates = (
        [keys[int(key_index)]] + [k for i, k in enumerate(keys) if i != int(key_index)]
        if int(key_index) < len(keys)
        else list(keys)
    )
    last_err: Exception | None = None
    for master in candidates:
        try:
            data_key = AESGCM(master).decrypt(wrap_nonce, wrapped, None)
            return AESGCM(data_key).decrypt(_unb64(nonce_b64), _unb64(ct_b64), None).decode()
        except Exception as e:  # noqa: BLE001 - try the next key
            last_err = e
    raise ValueError("unable to decrypt secret with configured master keys") from last_err


def last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else "****"
