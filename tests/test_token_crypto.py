"""Tests for token encryption at rest.

The AAD test is the load-bearing one: a ciphertext moved between two user
documents must fail to decrypt, not silently authenticate user A as user B.
"""

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

import token_crypto


@pytest.fixture
def crypto(monkeypatch):
    monkeypatch.setattr(token_crypto, "DISABLED", False)
    monkeypatch.setattr(token_crypto, "_dek", None)
    monkeypatch.setenv("TOKEN_ENC_KEY", base64.b64encode(os.urandom(32)).decode())
    token_crypto.init()
    return token_crypto


def test_roundtrip(crypto):
    blob = crypto.encrypt_token(42, "sekret-garth-token")
    assert blob["v"] == 1
    assert "sekret" not in str(blob)  # actually encrypted, not encoded
    assert crypto.decrypt_token(42, blob) == "sekret-garth-token"


def test_ciphertext_swapped_between_users_fails(crypto):
    blob = crypto.encrypt_token(42, "user-42-token")
    with pytest.raises(InvalidTag):
        crypto.decrypt_token(43, blob)


def test_unknown_blob_version_rejected(crypto):
    blob = crypto.encrypt_token(42, "tok")
    blob["v"] = 2
    with pytest.raises(ValueError):
        crypto.decrypt_token(42, blob)


def test_missing_key_crashes_startup(monkeypatch):
    monkeypatch.setattr(token_crypto, "DISABLED", False)
    monkeypatch.setattr(token_crypto, "_dek", None)
    monkeypatch.delenv("TOKEN_ENC_KEY", raising=False)
    with pytest.raises(RuntimeError):
        token_crypto.init()


def test_wrong_key_length_crashes_startup(monkeypatch):
    monkeypatch.setattr(token_crypto, "DISABLED", False)
    monkeypatch.setattr(token_crypto, "_dek", None)
    monkeypatch.setenv("TOKEN_ENC_KEY", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(RuntimeError):
        token_crypto.init()


def test_explicit_disable_leaves_encryption_off(monkeypatch):
    monkeypatch.setattr(token_crypto, "DISABLED", True)
    monkeypatch.setattr(token_crypto, "_dek", None)
    token_crypto.init()
    assert not token_crypto.enabled()
