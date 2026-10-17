"""Legacy post generation flow without graph orchestration."""

import logging
from typing import Any
from fastapi import HTTPException, status

from config.constants import PostStatus
from config.settings import get_linkedin_person_urn
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import send_approval_request
from services.storage import (
    generate_next_post_id,
    get_valid_linkedin_access_token,
    save_post_record,
)

logger = logging.getLogger("legacy_post_flow")


def run_legacy_post_flow(
    topic: str = "AI advancements and modern software engineering",
    user_sub: str | None = None,
) -> dict[str, Any]:
    """Execute legacy procedural post generation and Slack approval dispatch.

    Purpose:
        Automates creation of a LinkedIn post on a given topic using Groq LLM,
        stores it with pending status, and dispatches an interactive approval request to Slack.
    """
    token_id = user_sub or "default"
    token = get_valid_linkedin_access_token(token_id=token_id)
    if not token and token_id != "default":
        token = get_valid_linkedin_access_token(token_id="default")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LinkedIn is not authenticated. Please visit /linkedin/login first.",
        )

    person_urn = user_sub or get_linkedin_person_urn() or fetch_linkedin_person_urn(token)
    post_content = generate_linkedin_post_content(topic=topic)
    post_id = generate_next_post_id()

    save_post_record(
        post_id=post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=token,
        status=PostStatus.PENDING,
    )

    slack_res = send_approval_request(
        post_id=post_id,
        content=post_content,
    )

    return {
        "success": True,
        "post_id": post_id,
        "person_urn": person_urn,
        "status": "pending_approval",
        "generated_content": post_content,
        "slack_delivery": slack_res,
        "message": "Post generated and sent to Slack for user approval.",
    }
