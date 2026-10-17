"""Token encryption and decryption service using Fernet symmetric encryption."""

import base64
import hashlib
import logging
from cryptography.fernet import Fernet, InvalidToken
from config.settings import LINKEDIN_CLIENT_SECRET, TOKEN_ENCRYPTION_KEY

logger = logging.getLogger("crypto_service")

_fernet_instance: Fernet | None = None


def get_fernet() -> Fernet:
    """Initialize or retrieve the Fernet cipher suite instance."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    key = TOKEN_ENCRYPTION_KEY
    if key and key.strip():
        try:
            _fernet_instance = Fernet(key.strip().encode("utf-8"))
            return _fernet_instance
        except Exception as err:
            logger.error(f"Invalid TOKEN_ENCRYPTION_KEY format: {err}. Falling back to derived key.")

    # Fallback deterministic key derived from client secret or fallback seed
    seed = (LINKEDIN_CLIENT_SECRET or "mcp-langraph-agent-local-secret-seed").encode("utf-8")
    derived = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    _fernet_instance = Fernet(derived)
    return _fernet_instance


def encrypt_token(plain_token: str | None) -> str | None:
    """Encrypt a plaintext OAuth token for storage at rest.

    Returns:
        str | None: Fernet-encrypted ciphertext string, or None if token is empty.
    """
    if not plain_token or not str(plain_token).strip():
        return None
    fernet = get_fernet()
    return fernet.encrypt(plain_token.strip().encode("utf-8")).decode("utf-8")


def decrypt_token(cipher_or_plain: str | None) -> str | None:
    """Decrypt a stored OAuth token, with fallback for unencrypted legacy rows.

    Returns:
        str | None: Decrypted plaintext token string, or None if input is empty.
    """
    if not cipher_or_plain or not str(cipher_or_plain).strip():
        return None
    val = cipher_or_plain.strip()
    fernet = get_fernet()
    try:
        return fernet.decrypt(val.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        # Plaintext fallback for migration of legacy rows; should be re-saved as encrypted
        logger.warning("Decryption failed; token appears to be unencrypted plaintext (migration fallback)")
        return val
