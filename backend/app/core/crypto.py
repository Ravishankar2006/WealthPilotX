"""Application-layer encryption for financial profile fields (PRD §11.2).

Decision 01 of the Phase 1 plan: `income` and `savings` are encrypted here rather
than relying on the host's disk encryption, so the protection travels with the data
across any deployment target and cannot be silently lost by a hosting change.

The trade-off is deliberate and documented: these columns cannot be aggregated in
SQL, so the fairness dashboard (FR-14) must aggregate in application code.
"""

from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


def _derive_key(secret: str) -> bytes:
    """HKDF the configured secret into a Fernet key.

    Lets the operator supply any sufficiently long secret instead of requiring a
    correctly-formatted 32-byte urlsafe-base64 value in the environment.
    """
    import base64

    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"wealthpilotx.profile.v1",
        info=b"financial-profile-field-encryption",
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key(get_settings().profile_encryption_key))
    return _fernet


def encrypt_value(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_value(ciphertext: str) -> str:
    return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")


class EncryptedNumeric(TypeDecorator[Decimal]):
    """A Decimal in Python, opaque ciphertext in Postgres.

    Because the stored form is text, this column supports equality on nothing and
    ordering on nothing. That is the intended cost — see the module docstring.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return encrypt_value(str(Decimal(value)))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(decrypt_value(value))
        except InvalidToken as exc:  # wrong key, or ciphertext written by another key
            raise ValueError(
                "Could not decrypt a financial profile field. "
                "PROFILE_ENCRYPTION_KEY may have changed since this row was written."
            ) from exc
