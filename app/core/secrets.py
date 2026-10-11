"""
Connector credential encryption (SECURITY.md item 4).

Connector.config is a JSON column. For file/webhook connectors, nothing
in it needs to be secret-at-rest (webhook tokens are hashed, not
encrypted — we only ever verify them, never need the original back). A
database connector is different: we need the actual password repeatedly,
to keep reconnecting. That has to be reversible, so it has to be
encrypted, not hashed.

Fernet (symmetric, authenticated encryption) — not a bespoke scheme.
The key lives in CONNECTOR_ENCRYPTION_KEY, outside the database entirely.
Losing that key means every stored credential becomes unrecoverable —
that's the correct failure mode for a secret, not a bug.
"""
import os
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    key = os.environ.get("CONNECTOR_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CONNECTOR_ENCRYPTION_KEY is not set — cannot store or read connector "
            "credentials without it. Generate one with: "
            "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Never surface the raw crypto error to a caller — could leak key
        # material details. This should only happen if the encryption key
        # was rotated without re-encrypting existing secrets.
        raise RuntimeError("Could not decrypt connector credential — check CONNECTOR_ENCRYPTION_KEY")


# Config keys that must always be encrypted before storage, never stored
# plain. Any connector type's config passes through this list.
SECRET_CONFIG_KEYS = {"password", "db_password", "api_secret", "private_key"}


def encrypt_config_secrets(config: dict) -> dict:
    """Returns a copy of config with every key in SECRET_CONFIG_KEYS encrypted."""
    result = dict(config)
    for key in SECRET_CONFIG_KEYS:
        if key in result and result[key]:
            result[key] = encrypt_secret(str(result[key]))
    return result


def decrypt_config_secrets(config: dict) -> dict:
    """Inverse of encrypt_config_secrets — used only at the moment of
    actually connecting, never to render a config back to an API response."""
    result = dict(config)
    for key in SECRET_CONFIG_KEYS:
        if key in result and result[key]:
            result[key] = decrypt_secret(result[key])
    return result
