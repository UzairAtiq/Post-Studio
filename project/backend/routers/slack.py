"""Slack interactive webhooks and action dispatch router."""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from typing import Any

from config.settings import (
    POST_FLOW_MODE,
    SLACK_SIGNING_SECRET,
    SLACK_WORKSPACE_INVITE_URL,
)
from routers.auth import get_current_user
from services.approval import (
    execute_post_decision,
    record_slack_decision,
)
from services.slack import lookup_slack_user_id
from services.storage import (
    get_linkedin_token_record,
    get_post_by_id,
    get_slack_user_id,
    set_slack_user_id,
)

logger = logging.getLogger("slack_router")

router = APIRouter(tags=["Slack"])


@router.get("/slack/status")
def get_slack_status(
    user_sub: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Check Slack connection status for the authenticated user.

    Purpose:
        Reports whether the user's LinkedIn profile is mapped to a Slack member ID
        in the admin's Slack workspace, and provides the workspace invite link if not.

    Steps:
        1. Extract authenticated user sub from dependency.
        2. Query storage for user's mapped slack_user_id via get_slack_user_id.
        3. Return connection status flag, slack_user_id, and invite URL.

    Returns:
        dict[str, Any]: Dictionary with connected boolean, slack_user_id, and invite_url.

    Raises:
        HTTPException: 401 Unauthorized if user is not authenticated.
    """
    slack_uid = get_slack_user_id(user_sub)
    connected = bool(slack_uid)
    return {
        "connected": connected,
        "slack_user_id": slack_uid,
        "invite_url": SLACK_WORKSPACE_INVITE_URL,
        "message": (
            "Slack DM approval is active."
            if connected
            else "Slack account not connected. Please join the workspace via invite link with your LinkedIn email."
        ),
    }


@router.post("/slack/recheck")
def recheck_slack_status(
    user_sub: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Retry linking user's Slack account by looking up stored profile email.

    Purpose:
        Allows users who joined the Slack workspace after LinkedIn login to re-trigger
        the email lookup without having to re-authenticate with LinkedIn.

    Steps:
        1. Extract authenticated user sub from dependency.
        2. Fetch user's stored token/profile record to retrieve LinkedIn account email.
        3. If email is absent, raise HTTP 400 Bad Request.
        4. Query Slack API via lookup_slack_user_id(email).
        5. If found, persist mapped Slack user ID via set_slack_user_id.
        6. Return updated connection state and invite URL.

    Returns:
        dict[str, Any]: Dictionary with success, connected, and slack_user_id.

    Raises:
        HTTPException: 400 Bad Request if user email cannot be found.
        HTTPException: 401 Unauthorized if user is unauthenticated.
    """
    record = get_linkedin_token_record(token_id=user_sub)
    profile_id_str = record.get("profile_id") if record else None
    email = None
    if profile_id_str:
        try:
            profile_data = json.loads(profile_id_str)
            if isinstance(profile_data, dict):
                email = profile_data.get("email")
        except Exception:
            pass

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email found in LinkedIn profile. Please re-login via /linkedin/login.",
        )

    slack_uid = lookup_slack_user_id(email)
    if slack_uid:
        set_slack_user_id(sub=user_sub, slack_user_id=slack_uid)
        return {
            "success": True,
            "connected": True,
            "slack_user_id": slack_uid,
            "message": "Slack account linked successfully!",
        }

    return {
        "success": False,
        "connected": False,
        "slack_user_id": None,
        "invite_url": SLACK_WORKSPACE_INVITE_URL,
        "message": f"Email '{email}' not found in Slack workspace. Please join via invite link first.",
    }


def verify_slack_signature(request: Request, raw_body: bytes) -> None:
    """Validate Slack request signature and timestamp headers.

    Purpose:
        Protects Slack webhook endpoint against replay attacks and forged requests
        using the HMAC SHA-256 signature and request timestamp headers.

    Steps:
        1. Extract X-Slack-Request-Timestamp and X-Slack-Signature headers.
        2. Resolve signing secret from environment or application settings.
        3. If signing secret is configured, require both headers.
        4. If timestamp is provided, verify it is within 5 minutes of current time.
        5. If timestamp is older than 5 minutes, raise HTTP 401 Unauthorized.
        6. If signing secret is configured, compute expected HMAC SHA-256 signature.
        7. Compare expected signature against X-Slack-Signature using compare_digest.
        8. If signature mismatch, raise HTTP 401 Unauthorized.

    Returns:
        None.

    Raises:
        HTTPException: 401 Unauthorized if headers are missing, expired, or invalid.
    """
    timestamp_header = request.headers.get("x-slack-request-timestamp", "")
    slack_signature = request.headers.get("x-slack-signature", "")
    signing_secret = os.getenv("SLACK_SIGNING_SECRET") or SLACK_SIGNING_SECRET

    if signing_secret:
        if not timestamp_header or not slack_signature:
            logger.warning("Slack interaction request missing verification headers.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Slack verification signature or timestamp headers.",
            )

        try:
            req_timestamp = int(timestamp_header)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Slack request timestamp header.",
            )

        current_timestamp = int(time.time())
        if abs(current_timestamp - req_timestamp) > 300:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Slack request timestamp is older than 5 minutes.",
            )

        sig_basestring = f"v0:{timestamp_header}:{raw_body.decode('utf-8')}".encode("utf-8")
        expected_sig = "v0=" + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, slack_signature):
            logger.warning("Invalid Slack signature verification failed.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Slack request signature.",
            )
    elif timestamp_header:
        # If timestamp provided even when secret is unset, enforce freshness check
        try:
            req_timestamp = int(timestamp_header)
            if abs(int(time.time()) - req_timestamp) > 300:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Slack request timestamp is older than 5 minutes.",
                )
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Slack request timestamp header.",
            )


# slack interactive webhook endpoint
@router.post("/slack/interactions")
async def handle_slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle interactive button clicks from Slack approval messages.

    Purpose:
        Verifies Slack signature, validates clicking user ownership of the post,
        records decision, and defers execution to BackgroundTasks to immediately
        satisfy Slack's 3-second acknowledgment deadline.

    Steps:
        1. Read raw request body bytes and verify Slack signature and timestamp headers.
        2. Parse the URL-encoded JSON payload received from Slack.
        3. Validate the action list and extract action ID, post ID, and response URL.
        4. If post ID is present, verify clicking user ID equals owner's Slack user ID.
        5. If unauthorized, reject action and return HTTP 200 acknowledgment.
        6. Record the Slack decision in database storage synchronously.
        7. Enqueue post decision execution in BackgroundTasks.
        8. Return an HTTP 200 acknowledgment response to Slack immediately.

    Returns:
        Response: HTTP 200 OK acknowledgment for Slack.

    Raises:
        HTTPException: 400 Bad Request if the payload cannot be parsed as JSON.
        HTTPException: 401 Unauthorized if Slack signature or timestamp is invalid.
    """
    raw_body = await request.body()
    verify_slack_signature(request=request, raw_body=raw_body)

    body_text = raw_body.decode("utf-8")
    parsed_form = urllib.parse.parse_qs(body_text)
    payload_str = parsed_form.get("payload", [""])[0]

    # Fallback if raw JSON body sent
    if not payload_str and body_text.strip().startswith("{"):
        payload_str = body_text

    try:
        interaction_data = json.loads(payload_str)
    except (json.JSONDecodeError, TypeError) as parse_err:
        logger.error(f"failed to parse Slack interaction payload: {parse_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload format.",
        )

    # Ownership check: verify clicking user matches post author's Slack ID
    actions = interaction_data.get("actions", [])
    if actions:
        primary_action = actions[0]
        post_id = primary_action.get("value", "")
        if post_id:
            post = get_post_by_id(post_id)
            if post:
                owner_sub = post.get("owner_sub") or post.get("profile_id")
                if owner_sub:
                    expected_slack_id = get_slack_user_id(owner_sub)
                    clicking_user_id = interaction_data.get("user", {}).get("id")
                    if expected_slack_id and clicking_user_id and clicking_user_id != expected_slack_id:
                        logger.warning(
                            f"Slack interaction ownership mismatch for post '{post_id}': "
                            f"clicking user '{clicking_user_id}' != expected owner Slack ID '{expected_slack_id}'."
                        )
                        return Response(status_code=status.HTTP_200_OK)

    if POST_FLOW_MODE == "graph":
        from services.post_graph import process_graph_slack_interaction

        return process_graph_slack_interaction(
            interaction_data=interaction_data,
            background_tasks=background_tasks,
        )

    from legacy.slack_flow import process_legacy_slack_interaction

    return process_legacy_slack_interaction(
        interaction_data=interaction_data,
        background_tasks=background_tasks,
    )
