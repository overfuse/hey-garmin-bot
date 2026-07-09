"""Encryption at rest for stored Garmin tokens.

The stored garth blob is a standing bearer credential: the OAuth1 half alone can
mint fresh OAuth2 tokens forever, with no SSO and no password. Encrypting it
buys defense against DB compromise, backup leak, and snapshot exfiltration — it
buys nothing against application compromise (this process holds the plaintext
DEK by design), and must not be sold internally as more than that.

Scheme
------
AES-256-GCM with a single data-encryption key held in the TOKEN_ENC_KEY env var
(base64, 32 bytes). The AAD is bound to the owning telegram_id, so a ciphertext
swapped between two user documents fails to decrypt rather than silently
authenticating user A as user B. Stored shape: {"v": 1, "nonce": b64, "ct": b64}.

The key lives in the deploy's env, not in Mongo, which is the whole point. If a
real KMS enters the picture later, only init() changes: unwrap the DEK there and
cache it, exactly as now. A KMS Decrypt per upload would add a network hop to
the hot path for no gain — the process already holds plaintext tokens in memory.

Generate a key:  python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"

Failure policy mirrors rate_limiter: a missing TOKEN_ENC_KEY crashes startup
rather than silently degrading to plaintext writes. Local dev without a key
requires an explicit TOKEN_ENC_DISABLED=1.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DISABLED = os.getenv("TOKEN_ENC_DISABLED", "") == "1"

_dek: AESGCM | None = None

_NONCE_LEN = 12  # GCM standard


def init() -> None:
    """Load and cache the DEK. Call once at startup; raises rather than degrades."""
    global _dek

    if DISABLED:
        print("⚠️  TOKEN_ENC_DISABLED=1 — Garmin tokens will be stored in PLAINTEXT", flush=True)
        return

    raw = os.getenv("TOKEN_ENC_KEY", "")
    if not raw:
        raise RuntimeError(
            "TOKEN_ENC_KEY is not set and TOKEN_ENC_DISABLED is not 1. Refusing to "
            "start without token encryption — set one or the other explicitly."
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"TOKEN_ENC_KEY must decode to 32 bytes (got {len(key)}). Generate one with: "
            "python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
        )
    _dek = AESGCM(key)
    print("✓ Token encryption active (AES-256-GCM)", flush=True)


def enabled() -> bool:
    return _dek is not None


def _aad(telegram_id: int) -> bytes:
    return str(telegram_id).encode()


def encrypt_token(telegram_id: int, token: str) -> dict:
    """Encrypt a garth token blob for storage, bound to its owner."""
    if _dek is None:
        raise RuntimeError("token_crypto not initialised; call init()")
    nonce = os.urandom(_NONCE_LEN)
    ct = _dek.encrypt(nonce, token.encode(), _aad(telegram_id))
    return {
        "v": 1,
        "nonce": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ct).decode(),
    }


def decrypt_token(telegram_id: int, blob: dict) -> str:
    """Decrypt a stored token blob.

    Raises cryptography.exceptions.InvalidTag if the ciphertext was tampered
    with or belongs to a different telegram_id (AAD mismatch).
    """
    if _dek is None:
        raise RuntimeError("token_crypto not initialised; call init()")
    if blob.get("v") != 1:
        raise ValueError(f"unknown token blob version: {blob.get('v')!r}")
    nonce = base64.b64decode(blob["nonce"])
    ct = base64.b64decode(blob["ct"])
    return _dek.decrypt(nonce, ct, _aad(telegram_id)).decode()
