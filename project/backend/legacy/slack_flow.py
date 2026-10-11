"""Legacy Slack interaction handling and background decision execution."""

import logging
from typing import Any
from fastapi import BackgroundTasks, Response, status

from services.approval import (
    execute_post_decision,
    record_slack_decision,
)

logger = logging.getLogger("legacy_slack_flow")


def process_legacy_slack_interaction(
    interaction_data: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> Response:
    """Process Slack Block Kit interactive action payloads for legacy post flow.

    Purpose:
        Extracts action metadata from interactive payload, records approval/discard
        decision in storage, and schedules asynchronous execution via BackgroundTasks.

    Steps:
        1. Extract action list from interaction payload.
        2. If empty, return HTTP 200 acknowledgment immediately.
        3. Extract action_id, post_id, and response_url from primary action.
        4. If post_id is missing, acknowledge with HTTP 200 immediately.
        5. Record decision action and response_url in persistent storage.
        6. Enqueue execute_post_decision in background tasks.
        7. Return HTTP 200 OK response to satisfy Slack response deadline.

    Returns:
        Response: HTTP 200 OK response.

    Raises:
        None.
    """
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
