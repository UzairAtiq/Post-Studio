"""Legacy post generation flow without graph orchestration."""

import logging
from typing import Any
from fastapi import HTTPException, status

from config.constants import PostStatus
from config.settings import SLACK_WEBHOOK_URL, get_linkedin_person_urn
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
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute legacy procedural post generation and Slack approval dispatch.

    Purpose:
        Automates creation of a LinkedIn post on a given topic using Groq or custom LLM,
        stores it with pending status, and dispatches an interactive approval request to Slack.

    Steps:
        1. Validate that a valid, active LinkedIn access token exists in storage for user or default.
        2. If token is missing or expired, raise HTTP 401 Unauthorized.
        3. Resolve the user's LinkedIn person URN.
        4. Generate post content using Groq or custom LLM based on the topic parameter.
        5. Generate a unique post identifier.
        6. Persist pending post record into storage with profile ID and token.
        7. Send interactive Block Kit approval message to configured Slack channel.
        8. Return confirmation dictionary with post ID, content, and delivery result.

    Returns:
        dict[str, Any]: Summary dictionary with post_id, status, generated content, and Slack delivery info.

    Raises:
        HTTPException: 401 Unauthorized if LinkedIn is not authenticated.
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
    post_content = generate_linkedin_post_content(
        topic=topic,
        api_key=(llm_config or {}).get("api_key"),
        base_url=(llm_config or {}).get("base_url"),
        model=(llm_config or {}).get("model"),
    )
    post_id = generate_next_post_id()

    resolved_owner = user_sub or person_urn
    save_post_record(
        post_id=post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=token,
        status=PostStatus.PENDING,
        owner_sub=resolved_owner,
    )

    from services.slack import send_approval_dm
    from services.storage import get_slack_user_id

    slack_uid = get_slack_user_id(resolved_owner)
    if not slack_uid and user_sub:
        slack_uid = get_slack_user_id(user_sub)
    if not slack_uid:
        slack_uid = get_slack_user_id("default")

    dm_sent = False
    slack_res = None
    if slack_uid:
        post_record = {"post_id": post_id, "content": post_content}
        slack_res = send_approval_dm(slack_user_id=slack_uid, post=post_record)
        dm_sent = bool(slack_res and slack_res.get("ok"))
    elif SLACK_WEBHOOK_URL:
        slack_res = send_approval_request(
            post_id=post_id,
            content=post_content,
        )
    else:
        logger.info(f"User '{resolved_owner}' has no Slack user ID; skipped DM for post '{post_id}'.")

    return {
        "success": True,
        "post_id": post_id,
        "person_urn": person_urn,
        "owner_sub": resolved_owner,
        "status": "pending_approval",
        "generated_content": post_content,
        "dm_sent": dm_sent,
        "slack_delivery": slack_res,
        "message": (
            "Post generated and sent to Slack for user approval."
            if dm_sent
            else "Post generated. Slack DM skipped (account not linked in workspace); approve via web dashboard."
        ),
    }
