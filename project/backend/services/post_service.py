"""Post business logic service providing single source of truth for post lifecycle operations."""

import logging
from typing import Any

from config.constants import PostStatus
from config.settings import get_linkedin_access_token, get_linkedin_person_urn
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn, publish_post_to_linkedin
from services.slack_service import (
    send_approval_request as send_slack_approval_request,
    send_slack_message,
    update_slack_message,
)
from services.storage import (
    generate_next_post_id,
    get_post_by_id,
    get_valid_linkedin_access_token,
    save_post_record,
    update_post_status,
)

logger = logging.getLogger("post_service")


def notify_failure(post_id: str, node: str, reason: str) -> None:
    """Send short failure alert notification to Slack approval channel.

    Purpose:
        Alerts channel reviewers when a graph or workflow node encounters an unrecoverable failure.
        Catches errors silently to avoid cascading failures.

    Steps:
        1. Format short failure alert message.
        2. Attempt sending notification via Slack webhook / service.
        3. Catch and log any delivery failure without retrying.

    Returns:
        None.

    Raises:
        None.
    """
    message_text = f"⚠️ Workflow node '{node}' failed for post_id={post_id}: {reason}"
    try:
        send_slack_message(message_text)
    except Exception as notify_err:
        logger.error(f"Failed to deliver Slack failure alert for post_id={post_id}: {notify_err}")


def generate_post(topic: str) -> dict[str, Any]:
    """Generate technical LinkedIn post content via Groq LLM service.

    Purpose:
        Validates topic input and generates targeted post content using Groq LLM.

    Steps:
        1. Validate topic is non-empty.
        2. Call Groq service to generate post text.
        3. Return success payload containing generated content.

    Returns:
        dict[str, Any]: Dictionary with success status, content, or error explanation.

    Raises:
        None.
    """
    if not topic or not topic.strip():
        return {"success": False, "error": "Topic cannot be empty."}

    clean_topic = topic.strip()
    try:
        content = generate_linkedin_post_content(topic=clean_topic)
        return {
            "success": True,
            "topic": clean_topic,
            "content": content,
        }
    except Exception as err:
        logger.error(f"generate failed for topic={clean_topic}: {err}")
        return {
            "success": False,
            "error": f"generate failed for topic={clean_topic}: {err}",
        }


def save_post(
    content: str,
    post_id: str | None = None,
    person_urn: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Persist drafted post into storage with pending approval status.

    Purpose:
        Resolves LinkedIn credentials, assigns post ID if not provided, and persists
        the pending post record to active storage.

    Steps:
        1. Validate content is non-empty.
        2. Resolve valid LinkedIn access token and author person URN.
        3. Generate next sequential post ID if omitted.
        4. Persist post record with pending status.
        5. Return saved post record metadata.

    Returns:
        dict[str, Any]: Dictionary with success status, post_id, profile_id, and status.

    Raises:
        None.
    """
    if not content or not content.strip():
        return {"success": False, "error": "Content cannot be empty."}

    token = access_token or get_valid_linkedin_access_token() or get_linkedin_access_token()
    if not token:
        logger.error("save_post failed: No active LinkedIn access token available.")
        return {"success": False, "error": "LinkedIn access token not found."}

    resolved_urn = person_urn or get_linkedin_person_urn()
    if not resolved_urn:
        try:
            resolved_urn = fetch_linkedin_person_urn(access_token=token)
        except Exception as urn_err:
            logger.error(f"save_post failed resolving person URN: {urn_err}")
            return {"success": False, "error": f"Failed resolving person URN: {urn_err}"}

    final_post_id = post_id or generate_next_post_id()

    try:
        record = save_post_record(
            post_id=final_post_id,
            content=content.strip(),
            profile_id=resolved_urn,
            access_token=token,
            status=PostStatus.PENDING,
        )
        return {
            "success": True,
            "post_id": final_post_id,
            "profile_id": resolved_urn,
            "status": record.get("status", PostStatus.PENDING.value),
            "content": content.strip(),
        }
    except Exception as save_err:
        logger.error(f"save_post failed for post_id={final_post_id}: {save_err}")
        return {"success": False, "error": f"save_post failed for post_id={final_post_id}: {save_err}"}


def request_approval(post_id: str, content: str) -> dict[str, Any]:
    """Dispatch Slack Block Kit interactive approval card for a pending post.

    Purpose:
        Sends an interactive message with Approve and Discard buttons to configured Slack channel.

    Steps:
        1. Validate post_id and content arguments.
        2. Call Slack service to dispatch interactive Block Kit message.
        3. Return delivery confirmation outcome.

    Returns:
        dict[str, Any]: Delivery outcome dictionary with success flag and delivery details.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"success": False, "error": "post_id cannot be empty."}
    if not content or not content.strip():
        return {"success": False, "error": "content cannot be empty."}

    clean_post_id = post_id.strip()
    try:
        slack_result = send_slack_approval_request(
            post_id=clean_post_id,
            content=content.strip(),
        )
        if not slack_result.get("success"):
            err_msg = slack_result.get("error", "Unknown Slack error")
            logger.error(f"request_approval failed for post_id={clean_post_id}: {err_msg}")
            return {
                "success": False,
                "post_id": clean_post_id,
                "error": f"request_approval failed for post_id={clean_post_id}: {err_msg}",
            }
        return {
            "success": True,
            "post_id": clean_post_id,
            "slack_delivery": slack_result,
        }
    except Exception as dispatch_err:
        logger.error(f"request_approval failed for post_id={clean_post_id}: {dispatch_err}")
        return {
            "success": False,
            "post_id": clean_post_id,
            "error": f"request_approval failed for post_id={clean_post_id}: {dispatch_err}",
        }


def publish_post(post_id: str) -> dict[str, Any]:
    """Publish an approved post to LinkedIn REST API and update record status.

    Purpose:
        Validates post existence, checks idempotency to prevent double posting, publishes
        to LinkedIn, updates status to published, and refreshes the Slack message card.

    Steps:
        1. Validate post_id is provided.
        2. Query storage for existing post record.
        3. If already published, return idempotent success immediately without re-posting.
        4. Resolve active access token and author URN.
        5. Submit UGC post payload to LinkedIn REST API.
        6. On success: update status to published and update Slack card.
        7. On failure: update status to publish_failed, log error, and update Slack card.

    Returns:
        dict[str, Any]: Outcome dictionary containing success flag, status, and publish details.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"success": False, "error": "post_id cannot be empty."}

    clean_post_id = post_id.strip()
    record = get_post_by_id(clean_post_id)
    if not record:
        return {"success": False, "error": f"Post '{clean_post_id}' not found."}

    current_status = record.get("status")
    response_url = record.get("response_url")

    # Idempotency check: never retry publish blindly if already published
    if current_status in (PostStatus.PUBLISHED, "published"):
        logger.info(f"publish_post skipped for post_id={clean_post_id}: already published")
        return {
            "success": True,
            "status": "published",
            "post_id": clean_post_id,
            "message": f"Post '{clean_post_id}' is already published.",
            "linkedin_response": record.get("linkedin_response"),
        }

    token = record.get("access_token") or get_valid_linkedin_access_token() or get_linkedin_access_token()
    profile_id = record.get("profile_id") or get_linkedin_person_urn()

    if not token:
        err_msg = "No LinkedIn access token available."
        logger.error(f"publish failed for post_id={clean_post_id}: {err_msg}")
        update_post_status(clean_post_id, PostStatus.PUBLISH_FAILED)
        if response_url:
            update_slack_message(
                response_url=response_url,
                post_id=clean_post_id,
                action_type=PostStatus.PUBLISH_FAILED.value,
                detail_message="Failed: LinkedIn is not connected.",
            )
        return {"success": False, "status": "publish_failed", "post_id": clean_post_id, "error": err_msg}

    content = record.get("content", "")
    publish_result = publish_post_to_linkedin(
        content=content,
        access_token=token,
        person_urn=profile_id,
    )

    if publish_result.get("success"):
        post_urn = publish_result.get("post_urn", "published")
        update_post_status(
            post_id=clean_post_id,
            status=PostStatus.PUBLISHED,
            extra_data={"linkedin_response": publish_result},
        )
        if response_url:
            update_slack_message(
                response_url=response_url,
                post_id=clean_post_id,
                action_type=PostStatus.PUBLISHED.value,
                detail_message=f"Published successfully to LinkedIn ({post_urn})",
            )
        return {
            "success": True,
            "status": "published",
            "post_id": clean_post_id,
            "publish_result": publish_result,
            "linkedin_response": publish_result,
        }

    error_message = publish_result.get("error", "Unknown LinkedIn publish error")
    logger.error(f"publish failed for post_id={clean_post_id}: {error_message}")
    update_post_status(
        post_id=clean_post_id,
        status=PostStatus.PUBLISH_FAILED,
        extra_data={"linkedin_response": publish_result},
    )
    if response_url:
        update_slack_message(
            response_url=response_url,
            post_id=clean_post_id,
            action_type=PostStatus.PUBLISH_FAILED.value,
            detail_message=f"Failed to publish to LinkedIn: {error_message}",
        )
    return {
        "success": False,
        "status": "publish_failed",
        "post_id": clean_post_id,
        "error": f"publish failed for post_id={clean_post_id}: {error_message}",
    }


def discard_post(post_id: str) -> dict[str, Any]:
    """Mark a pending post record as discarded and refresh Slack message card.

    Purpose:
        Updates post record status to discarded in storage and modifies the Slack card.

    Steps:
        1. Validate post_id is provided.
        2. Query storage to verify post existence.
        3. Update post status to discarded.
        4. If response_url is present, update Slack card with discarded notification.
        5. Return discarded confirmation.

    Returns:
        dict[str, Any]: Confirmation dictionary with success flag, status, and post_id.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"success": False, "error": "post_id cannot be empty."}

    clean_post_id = post_id.strip()
    record = get_post_by_id(clean_post_id)
    if not record:
        return {"success": False, "error": f"Post '{clean_post_id}' not found."}

    response_url = record.get("response_url")
    update_post_status(post_id=clean_post_id, status=PostStatus.DISCARDED)

    if response_url:
        update_slack_message(
            response_url=response_url,
            post_id=clean_post_id,
            action_type="discarded",
            detail_message="Post has been marked as discarded.",
        )

    return {
        "success": True,
        "status": "discarded",
        "post_id": clean_post_id,
    }


def get_post(post_id: str) -> dict[str, Any]:
    """Retrieve post details by ID with sensitive credentials masked.

    Purpose:
        Fetches an individual post record from persistent storage and sanitizes
        its access token before returning.

    Steps:
        1. Validate post_id is provided.
        2. Query storage for matching post record.
        3. If not found, return found=False dictionary.
        4. Mask access token before returning.
        5. Return found=True with sanitized post record.

    Returns:
        dict[str, Any]: Dictionary with found boolean and post details or error.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"found": False, "error": "post_id cannot be empty."}

    clean_post_id = post_id.strip()
    record = get_post_by_id(clean_post_id)
    if not record:
        return {"found": False, "error": f"Post with ID '{clean_post_id}' not found."}

    safe_record = dict(record)
    if safe_record.get("access_token"):
        safe_record["access_token"] = safe_record["access_token"][:10] + "..."

    return {
        "found": True,
        "post": safe_record,
    }
