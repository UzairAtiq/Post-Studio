"""Unit tests for multi-user OAuth security, session management, and encryption."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient
from api.main import app
from services.crypto import decrypt_token, encrypt_token
from services.storage import (
    create_session,
    delete_session,
    get_linkedin_token_record,
    get_session_sub,
    get_valid_linkedin_access_token,
    save_linkedin_token,
)


def test_token_encryption_and_decryption():
    """Verify tokens are encrypted at rest and cleanly decrypted in memory."""
    raw_secret = "AQV_super_secret_oauth_access_token_12345"
    encrypted = encrypt_token(raw_secret)

    assert encrypted is not None
    assert encrypted != raw_secret
    assert encrypted.startswith("gAAAAA")

    decrypted = decrypt_token(encrypted)
    assert decrypted == raw_secret

    # Migration fallback for legacy unencrypted database rows
    legacy_plaintext = "AQV_legacy_unencrypted_token"
    assert decrypt_token(legacy_plaintext) == legacy_plaintext


def test_session_lifecycle():
    """Verify session creation, lookup, and deletion."""
    test_sub = "urn:li:person:secure_test_user_789"
    session_id = create_session(sub=test_sub, lifetime_days=7)

    assert session_id is not None
    assert len(session_id) >= 32

    resolved_sub = get_session_sub(session_id)
    assert resolved_sub == test_sub

    deleted = delete_session(session_id)
    assert deleted is True

    # After deletion, lookup must return None
    assert get_session_sub(session_id) is None


def test_expired_session_handling():
    """Verify expired sessions return None."""
    test_sub = "urn:li:person:expired_session_user"
    session_id = create_session(sub=test_sub, lifetime_days=-1)

    assert get_session_sub(session_id) is None


def test_multi_user_token_isolation():
    """Verify User A and User B tokens do not overwrite each other."""
    user_a_urn = "urn:li:person:alice_111"
    user_b_urn = "urn:li:person:bob_222"

    token_a = "token_alice_secret"
    token_b = "token_bob_secret"

    save_linkedin_token(
        access_token=token_a,
        person_urn=user_a_urn,
        token_id=user_a_urn,
        expires_in=3600,
    )
    save_linkedin_token(
        access_token=token_b,
        person_urn=user_b_urn,
        token_id=user_b_urn,
        expires_in=3600,
    )

    record_a = get_linkedin_token_record(token_id=user_a_urn)
    record_b = get_linkedin_token_record(token_id=user_b_urn)

    assert record_a is not None
    assert record_b is not None
    assert record_a["access_token"] == token_a
    assert record_b["access_token"] == token_b
    assert record_a["person_urn"] == user_a_urn
    assert record_b["person_urn"] == user_b_urn


def test_expired_token_without_refresh_returns_none():
    """Verify an expired token without refresh returns None prompting re-login."""
    expired_user_urn = "urn:li:person:expired_token_user"

    save_linkedin_token(
        access_token="expired_access_token",
        refresh_token=None,
        expires_in=-3600,  # expired 1 hour ago
        person_urn=expired_user_urn,
        token_id=expired_user_urn,
    )

    valid_token = get_valid_linkedin_access_token(token_id=expired_user_urn)
    assert valid_token is None


def test_oauth_state_generation_and_validation():
    """Verify CSRF state cookie is set on login and rejected on callback if mismatched."""
    client = TestClient(app)

    with patch("routers.auth.LINKEDIN_CLIENT_ID", "test_client_id"):
        # 1. Login sets cookie
        login_res = client.get("/linkedin/login", follow_redirects=False)
        assert login_res.status_code == 302
        assert "linkedin_oauth_state" in login_res.cookies
        state_cookie = login_res.cookies["linkedin_oauth_state"]

        # 2. Callback with wrong state is rejected with 403 Forbidden
        bad_cb = client.get("/linkedin/callback?code=test_code&state=forged_state")
        assert bad_cb.status_code == 403

        # 3. Callback with matching state proceeds past state validation check
        with (
            patch("routers.auth.requests.post") as mock_post,
            patch("routers.auth.get_linkedin_token_record", return_value=None),
        ):
            mock_post.return_value.json.return_value = {
                "error": "invalid_grant",
                "error_description": "mocked code error",
            }
            client.cookies.set("linkedin_oauth_state", state_cookie)
            valid_state_cb = client.get(
                f"/linkedin/callback?code=mock_code&state={state_cookie}",
                follow_redirects=False,
            )
            # State matched, progressed into code exchange (which returned 400 bad request)
            assert valid_state_cb.status_code == 400


def test_linkedin_status_endpoint_with_session():
    """Verify /linkedin/status reads user session cookie."""
    client = TestClient(app)
    user_urn = "urn:li:person:status_test_user"

    save_linkedin_token(
        access_token="status_test_token_abc123",
        person_urn=user_urn,
        token_id=user_urn,
        expires_in=7200,
    )
    session_id = create_session(sub=user_urn)

    client.cookies.set("session_id", session_id)
    res = client.get("/linkedin/status")
    assert res.status_code == 200
    data = res.json()
    assert data["authenticated"] is True
    assert data["person_urn"] == user_urn
    assert "token_preview" in data


def test_linkedin_logout():
    """Verify /linkedin/logout revokes session and clears cookie."""
    client = TestClient(app)
    user_urn = "urn:li:person:logout_user"
    session_id = create_session(sub=user_urn)

    client.cookies.set("session_id", session_id)
    logout_res = client.get("/linkedin/logout", follow_redirects=False)
    assert logout_res.status_code == 302

    # Session record must be deleted
    assert get_session_sub(session_id) is None
