"""Unit and integration tests for Slack DM approval workflow and ownership verification."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.main import app
from config.constants import PostStatus
from services.approval import approve_post, reject_post
from services.slack import (
    lookup_slack_user_id,
    send_approval_dm,
    update_message,
)
from services.storage import (
    create_session,
    generate_next_post_id,
    get_post_by_id,
    get_slack_user_id,
    save_linkedin_token,
    save_post_record,
    set_slack_user_id,
    update_post_slack_ref,
)


def test_slack_lookup_user_id():
    """Verify lookup_slack_user_id queries Slack users.lookupByEmail API."""
    with patch("services.slack.requests.get") as mock_get:
        # Success response
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "user": {"id": "U012345678"}}
        mock_get.return_value = mock_resp

        user_id = lookup_slack_user_id("user@example.com")
        assert user_id == "U012345678"
        assert "users.lookupByEmail" in mock_get.call_args[0][0]

        # Not found response
        mock_resp_fail = MagicMock()
        mock_resp_fail.json.return_value = {"ok": False, "error": "users_not_found"}
        mock_get.return_value = mock_resp_fail

        assert lookup_slack_user_id("nonexistent@example.com") is None


def test_send_approval_dm_and_update_ref():
    """Verify send_approval_dm constructs Block Kit with Approve/Reject buttons and persists channel/ts."""
    post_id = f"test_post_dm_{int(time.time())}"
    save_post_record(
        post_id=post_id,
        content="Testing Slack DM Block Kit delivery.",
        profile_id="urn:li:person:dm_user",
        access_token="fake_token",
        status=PostStatus.PENDING,
        owner_sub="urn:li:person:dm_user",
    )

    with patch("services.slack.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "channel": "D0987654", "ts": "1710000000.123456"}
        mock_post.return_value = mock_resp

        result = send_approval_dm(
            slack_user_id="U012345678",
            post={"post_id": post_id, "content": "Testing Slack DM Block Kit delivery."},
        )

        assert result is not None
        assert result.get("ok") is True
        assert result.get("channel") == "D0987654"
        assert result.get("ts") == "1710000000.123456"

        # Verify Block Kit payload structure
        payload = mock_post.call_args[1]["json"]
        assert payload["channel"] == "U012345678"
        action_elements = payload["blocks"][-1]["elements"]
        action_ids = [btn["action_id"] for btn in action_elements]
        assert "approve_post" in action_ids
        assert "reject_post" in action_ids
        assert all(btn["value"] == post_id for btn in action_elements)

        # Verify channel and ts persisted on post record
        post_record = get_post_by_id(post_id)
        assert post_record is not None
        assert post_record.get("slack_channel") == "D0987654"
        assert post_record.get("slack_ts") == "1710000000.123456"


def test_update_message_api():
    """Verify update_message calls chat.update with updated text."""
    with patch("services.slack.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        success = update_message(channel="D0987654", ts="1710000000.123456", text="✅ Approved")
        assert success is True
        payload = mock_post.call_args[1]["json"]
        assert payload["channel"] == "D0987654"
        assert payload["ts"] == "1710000000.123456"
        assert "✅ Approved" in payload["text"]


def test_storage_slack_user_mapping():
    """Verify set_slack_user_id and get_slack_user_id maintain user mapping across bare and URN IDs."""
    unique_sub = f"user_sub_test_{int(time.time())}"
    person_urn = f"urn:li:person:{unique_sub}"

    save_linkedin_token(
        access_token="fake_test_token",
        person_urn=person_urn,
        token_id=person_urn,
        profile_id=json.dumps({"sub": unique_sub, "email": "test@domain.com"}),
    )

    # Map Slack user ID
    updated = set_slack_user_id(sub=unique_sub, slack_user_id="U99887766")
    assert updated is True

    # Retrieve by bare sub and by URN
    assert get_slack_user_id(unique_sub) == "U99887766"
    assert get_slack_user_id(person_urn) == "U99887766"


def test_slack_status_and_recheck_endpoints():
    """Verify GET /slack/status and POST /slack/recheck endpoints require auth and reflect linkage."""
    client = TestClient(app)

    # Invalid session cookie -> 401
    client.cookies.set("session_id", "invalid_expired_session_token_xyz")
    resp = client.get("/slack/status")
    assert resp.status_code == 401

    unique_sub = f"status_user_{int(time.time())}"
    person_urn = f"urn:li:person:{unique_sub}"
    session_id = create_session(sub=person_urn)

    save_linkedin_token(
        access_token="fake_token_status",
        person_urn=person_urn,
        token_id=person_urn,
        profile_id=json.dumps({"sub": unique_sub, "email": "status_user@example.com"}),
    )

    # Initial check: unlinked
    client.cookies.set("session_id", session_id)
    resp = client.get("/slack/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert "invite_url" in data

    # Recheck with mock Slack lookup finding the user
    with patch("routers.slack.lookup_slack_user_id", return_value="U_NEW_LINKED_ID"):
        recheck_resp = client.post("/slack/recheck")
        assert recheck_resp.status_code == 200
        recheck_data = recheck_resp.json()
        assert recheck_data["connected"] is True
        assert recheck_data["slack_user_id"] == "U_NEW_LINKED_ID"

    # Status now reflects linked state
    status_resp = client.get("/slack/status")
    assert status_resp.json()["connected"] is True
    assert status_resp.json()["slack_user_id"] == "U_NEW_LINKED_ID"


def test_post_generation_dm_behavior():
    """Verify POST /posts/generate sends DM when user is linked to Slack, and skips DM when unlinked."""
    client = TestClient(app)

    # User 1: Has Slack user ID
    user1_sub = f"linked_user_{int(time.time())}"
    user1_session = create_session(sub=user1_sub)
    save_linkedin_token(
        access_token="fake_token_1",
        person_urn=f"urn:li:person:{user1_sub}",
        token_id=user1_sub,
    )
    set_slack_user_id(sub=user1_sub, slack_user_id="U_LINKED_USER_1")

    client.cookies.set("session_id", user1_session)
    with (
        patch("routers.posts.POST_FLOW_MODE", "legacy"),
        patch("services.groq_service.generate_linkedin_post_content", return_value="Great post content!"),
        patch("services.slack.send_approval_dm", return_value={"ok": True, "channel": "D1", "ts": "1.1"}) as mock_dm,
    ):
        resp = client.post("/posts/generate?topic=AI")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dm_sent"] is True
        assert mock_dm.called

    # User 2: Has NO Slack user ID
    user2_sub = f"unlinked_user_{int(time.time())}"
    user2_session = create_session(sub=user2_sub)
    save_linkedin_token(
        access_token="fake_token_2",
        person_urn=f"urn:li:person:{user2_sub}",
        token_id=user2_sub,
    )

    client.cookies.set("session_id", user2_session)
    with (
        patch("routers.posts.POST_FLOW_MODE", "legacy"),
        patch("services.groq_service.generate_linkedin_post_content", return_value="Another post"),
        patch("services.slack.send_approval_dm") as mock_dm_unlinked,
        patch("config.settings.SLACK_WEBHOOK_URL", None),
    ):
        resp2 = client.post("/posts/generate?topic=Cloud")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["dm_sent"] is False
        assert not mock_dm_unlinked.called


def test_slack_interaction_ownership_verification():
    """Verify POST /slack/interactions rejects button clicks from users other than the post owner."""
    client = TestClient(app)
    post_id = f"test_verify_owner_{int(time.time())}"
    author_sub = f"author_sub_{int(time.time())}"

    save_post_record(
        post_id=post_id,
        content="Post for verification test.",
        profile_id=author_sub,
        access_token="fake_token",
        status=PostStatus.PENDING,
        owner_sub=author_sub,
    )
    set_slack_user_id(sub=author_sub, slack_user_id="U_EXPECTED_OWNER")

    # Unauthorized click from a different Slack user
    unauthorized_payload = {
        "user": {"id": "U_MALICIOUS_USER"},
        "actions": [{"action_id": "approve_post", "value": post_id}],
        "response_url": "https://hooks.slack.com/actions/test",
    }

    with (
        patch("routers.slack.verify_slack_signature"),
        patch("services.approval.approve_post") as mock_approve,
    ):
        resp = client.post(
            "/slack/interactions",
            data={"payload": json.dumps(unauthorized_payload)},
        )
        assert resp.status_code == 200
        # approve_post should NOT have been called due to user mismatch
        assert not mock_approve.called

    # Authorized click from the expected Slack user
    authorized_payload = {
        "user": {"id": "U_EXPECTED_OWNER"},
        "actions": [{"action_id": "approve_post", "value": post_id}],
        "response_url": "https://hooks.slack.com/actions/test",
    }

    with (
        patch("routers.slack.verify_slack_signature"),
        patch("services.approval.publish_post_to_linkedin", return_value={"success": True, "post_urn": "urn:li:share:123"}),
    ):
        resp = client.post(
            "/slack/interactions",
            data={"payload": json.dumps(authorized_payload)},
        )
        assert resp.status_code == 200


def test_dashboard_approve_and_reject_endpoints():
    """Verify /posts/{id}/approve and /posts/{id}/reject enforce post ownership and update status."""
    client = TestClient(app)

    owner_sub = f"owner_dash_{int(time.time())}"
    owner_session = create_session(sub=owner_sub)
    other_sub = f"other_dash_{int(time.time())}"
    other_session = create_session(sub=other_sub)

    post_id = f"post_dash_{int(time.time())}"
    save_post_record(
        post_id=post_id,
        content="Dashboard approval test content.",
        profile_id=owner_sub,
        access_token="valid_token",
        status=PostStatus.PENDING,
        owner_sub=owner_sub,
    )

    # Other user trying to approve -> 403 Forbidden
    client.cookies.set("session_id", other_session)
    resp_forbidden = client.post(f"/posts/{post_id}/approve")
    assert resp_forbidden.status_code == 403

    # Owner approving post -> 200 OK and published
    client.cookies.set("session_id", owner_session)
    with patch("services.approval.publish_post_to_linkedin", return_value={"success": True, "post_urn": "urn:li:share:dash"}):
        resp_approved = client.post(f"/posts/{post_id}/approve")
        assert resp_approved.status_code == 200
        assert resp_approved.json()["status"] == "published"

    # Test reject endpoint on another post
    post_id_reject = f"post_dash_reject_{int(time.time())}"
    save_post_record(
        post_id=post_id_reject,
        content="Dashboard reject test content.",
        profile_id=owner_sub,
        access_token="valid_token",
        status=PostStatus.PENDING,
        owner_sub=owner_sub,
    )

    client.cookies.set("session_id", owner_session)
    resp_reject = client.post(f"/posts/{post_id_reject}/reject")
    assert resp_reject.status_code == 200
    assert resp_reject.json()["status"] == "discarded"


def test_custom_email_slack_connect():
    """Verify connecting Slack with custom email and channel invite dispatch."""
    client = TestClient(app)

    unique_sub = f"custom_slack_user_{int(time.time())}"
    person_urn = f"urn:li:person:{unique_sub}"
    session_id = create_session(sub=person_urn)
    save_linkedin_token(
        access_token="fake_token_custom_slack",
        person_urn=person_urn,
        token_id=person_urn,
        profile_id=json.dumps({"sub": unique_sub, "email": "linkedin_profile@example.com"}),
    )

    client.cookies.set("session_id", session_id)

    # 1. Custom email not found in Slack
    with patch("routers.slack.lookup_slack_user_id", return_value=None):
        resp = client.post("/slack/connect", json={"email": "custom_slack@company.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["email"] == "custom_slack@company.com"
        assert "invite_url" in data

    # 2. Custom email found in Slack, with channel invite and welcome DM
    with (
        patch("routers.slack.lookup_slack_user_id", return_value="U_CUSTOM_SLACK_123"),
        patch("routers.slack.SLACK_CHANNEL_ID", "C_ALERTS_1"),
        patch("routers.slack.invite_user_to_channel", return_value=True) as mock_invite,
        patch("routers.slack.send_welcome_dm", return_value=True) as mock_welcome,
    ):
        resp = client.post("/slack/connect", json={"email": "custom_slack@company.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["slack_user_id"] == "U_CUSTOM_SLACK_123"
        assert data["email"] == "custom_slack@company.com"
        assert data["channel_invited"] is True
        mock_invite.assert_called_once_with(channel="C_ALERTS_1", slack_user_id="U_CUSTOM_SLACK_123")
        mock_welcome.assert_called_once_with(slack_user_id="U_CUSTOM_SLACK_123", email="custom_slack@company.com")

    # 3. Status endpoint now returns linked Slack state and email
    status_resp = client.get("/slack/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["connected"] is True
    assert status_data["slack_user_id"] == "U_CUSTOM_SLACK_123"

