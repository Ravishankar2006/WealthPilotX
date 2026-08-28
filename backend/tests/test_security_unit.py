"""Unit tests for the security and crypto primitives."""

import uuid

import jwt
import pytest

from app.core.crypto import decrypt_value, encrypt_value
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        password = "correct-horse-battery-staple"
        assert hash_password(password) != password

    def test_hashes_are_salted(self) -> None:
        password = "correct-horse-battery-staple"
        assert hash_password(password) != hash_password(password)

    def test_verify_accepts_the_right_password(self) -> None:
        password = "correct-horse-battery-staple"
        assert verify_password(password, hash_password(password))

    def test_verify_rejects_the_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("right-password-here"))

    def test_verify_rejects_a_malformed_hash_without_raising(self) -> None:
        assert not verify_password("anything", "not-a-hash")


class TestAccessTokens:
    def test_round_trips(self) -> None:
        user_id = uuid.uuid4()
        token, expires = create_access_token(user_id)

        payload = decode_token(token, expected_type="access")
        assert payload["sub"] == str(user_id)
        assert payload["type"] == "access"
        assert expires is not None

    def test_rejects_a_token_of_the_wrong_type(self) -> None:
        token, _ = create_access_token(uuid.uuid4())
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token, expected_type="refresh")

    def test_rejects_a_tampered_signature(self) -> None:
        token, _ = create_access_token(uuid.uuid4())
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token + "x", expected_type="access")


class TestFieldEncryption:
    def test_round_trips(self) -> None:
        assert decrypt_value(encrypt_value("82000.00")) == "82000.00"

    def test_ciphertext_does_not_contain_the_plaintext(self) -> None:
        assert "82000" not in encrypt_value("82000.00")

    def test_encryption_is_non_deterministic(self) -> None:
        """Fernet includes a random IV, so equal values do not produce equal
        ciphertext — which is also why these columns cannot be indexed."""
        assert encrypt_value("82000.00") != encrypt_value("82000.00")
