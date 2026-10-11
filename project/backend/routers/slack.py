"""Slack interactive webhooks and action dispatch router."""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status

from config.settings import SLACK_SIGNING_SECRET
from services.approval import (
    execute_post_decision,
    record_slack_decision,
)

logger = logging.getLogger("slack_router")

router = APIRouter(tags=["Slack"])


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
        Verifies Slack signature, processes Slack Block Kit interactive actions,
        records decision, and defers execution to BackgroundTasks to immediately
        satisfy Slack's 3-second acknowledgment deadline.

    Steps:
        1. Read raw request body bytes and verify Slack signature and timestamp headers.
        2. Parse the URL-encoded JSON payload received from Slack.
        3. Validate the action list and extract action ID, post ID, and response URL.
        4. If post ID is absent, acknowledge with HTTP 200 immediately.
        5. Record the Slack decision in database storage synchronously.
        6. Enqueue post decision execution in BackgroundTasks.
        7. Return an HTTP 200 acknowledgment response to Slack immediately.

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

    actions = interaction_data.get("actions", [])
    if not actions:
        logger.warning("received Slack interaction without actions")
        return Response(status_code=status.HTTP_200_OK)

    primary_action = actions[0]
    action_id = primary_action.get("action_id", "")
    post_id = primary_action.get("value", "")
    response_url = interaction_data.get("response_url")

    logger.info(f"processing Slack action: '{action_id}' for post_id: '{post_id}'")

    if not post_id:
        return Response(status_code=status.HTTP_200_OK)

    # record decision in storage
    record_slack_decision(
        action_id=action_id,
        post_id=post_id,
        response_url=response_url,
    )

    # run publish and message update in background to satisfy Slack 3-second limit
    background_tasks.add_task(execute_post_decision, post_id=post_id)

    return Response(status_code=status.HTTP_200_OK)
