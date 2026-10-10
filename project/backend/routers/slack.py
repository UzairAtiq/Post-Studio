import json
import logging
from typing import Annotated
from fastapi import APIRouter, Form, HTTPException, Response, status
from services.slack_service import (
    execute_post_decision,
    record_slack_decision,
)

logger = logging.getLogger("slack_router")

router = APIRouter(tags=["Slack"])

# slack interactive webhook endpoint
@router.post("/slack/interactions")
async def handle_slack_interactions(payload: Annotated[str, Form()]) -> Response:
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

    # execute post decision immediately
    execute_result = execute_post_decision(post_id=post_id)
    logger.info(f"executed post decision for '{post_id}': {execute_result}")

    return Response(status_code=status.HTTP_200_OK)
