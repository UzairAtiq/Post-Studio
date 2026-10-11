"""Slack interactive webhooks and action dispatch router."""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, status

from services.approval import (
    execute_post_decision,
    record_slack_decision,
)

logger = logging.getLogger("slack_router")

router = APIRouter(tags=["Slack"])


# slack interactive webhook endpoint
@router.post("/slack/interactions")
async def handle_slack_interactions(
    payload: Annotated[str, Form()],
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle interactive button clicks from Slack approval messages.

    Purpose:
        Processes Slack Block Kit interactive actions (e.g., approving or
        discarding a pending LinkedIn post) and defers execution to BackgroundTasks
        to immediately satisfy Slack's 3-second acknowledgment deadline.

    Steps:
        1. Parse the URL-encoded JSON payload received from Slack.
        2. Validate the action list and extract action ID, post ID, and response URL.
        3. If post ID is absent, acknowledge with HTTP 200 immediately.
        4. Record the Slack decision in database storage synchronously.
        5. Enqueue post decision execution (LinkedIn publish/discard and Slack update) in BackgroundTasks.
        6. Return an HTTP 200 acknowledgment response to Slack immediately.

    Returns:
        Response: HTTP 200 OK acknowledgment for Slack.

    Raises:
        HTTPException: 400 Bad Request if the payload cannot be parsed as JSON.
    """
    try:
        interaction_data = json.loads(payload)
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
