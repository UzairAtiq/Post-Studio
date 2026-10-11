"""Post approval decision recording and publication execution service."""

import logging
import time
from typing import Any

from config.constants import MAX_APPROVAL_TIMEOUT_SECONDS, PostStatus
from config.settings import get_linkedin_access_token, get_linkedin_person_urn
from services.linkedin_service import publish_post_to_linkedin
from services.slack_service import update_slack_message
from services.storage import get_post_by_id, update_post_status

logger = logging.getLogger("approval_service")


def record_slack_decision(
    action_id: str,
    post_id: str,
    response_url: str | None = None,
) -> dict[str, Any]:
    """Record human button decision received from Slack interaction into storage.

    Purpose:
        Validates post existence and records approval or rejection state in persistent storage.

    Steps:
        1. Look up existing post record from storage by post_id.
        2. If post does not exist, return failure dictionary.
        3. If post is already finalized, return current status immediately.
        4. Record decision action and response URL in storage.
        5. Return decision confirmation dictionary.

    Returns:
        dict[str, Any]: Dictionary with success status, post_id, decision, and response_url.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    current_status = post_record.get("status")
    if current_status in (PostStatus.PUBLISHED, PostStatus.PUBLISH_FAILED, PostStatus.DISCARDED):
        return {
            "success": True,
            "post_id": post_id,
            "decision": current_status,
            "response_url": response_url,
        }

    decision_status = PostStatus.APPROVED if action_id == "approve_linkedin_post" else post_record.get("status", PostStatus.PENDING)
    update_post_status(
        post_id=post_id,
        status=decision_status,
        extra_data={"response_url": response_url, "decision_action": action_id},
    )

    decision_name = "approved" if action_id == "approve_linkedin_post" else "discarded"
    return {
        "success": True,
        "post_id": post_id,
        "decision": decision_name,
        "response_url": response_url,
    }


def execute_post_decision(post_id: str) -> dict[str, Any]:
    """Execute post publishing or disposal based on recorded approval decision.

    Purpose:
        Publishes approved post to LinkedIn or discards it, updating post status
        and replacing the original Slack interactive message card. Idempotent:
        does nothing if post is already finalized.

    Steps:
        1. Fetch post record from storage by post_id. Return failure if not found.
        2. Idempotency check: if status is already published, publish_failed, or discarded,
           do nothing and return current state dictionary immediately.
        3. If approved, resolve credentials and publish to LinkedIn. Update Slack card.
        4. If publication succeeds, update status to published. If fails, update to publish_failed.
        5. If discard decision recorded, update status to discarded and update Slack card.
        6. Return execution outcome dictionary.

    Returns:
        dict[str, Any]: Dictionary containing execution success, post_id, and details.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    current_status = post_record.get("status")
    decision_action = post_record.get("decision_action")
    response_url = post_record.get("response_url")

    # Idempotency check: do nothing if status is already published, publish_failed, or discarded
    if current_status in (
        PostStatus.PUBLISHED,
        PostStatus.PUBLISH_FAILED,
        PostStatus.DISCARDED,
        "published",
        "publish_failed",
        "discarded",
    ):
        return {
            "success": current_status in (PostStatus.PUBLISHED, PostStatus.DISCARDED, "published", "discarded"),
            "status": current_status if isinstance(current_status, str) else current_status.value,
            "post_id": post_id,
            "publish_result": post_record.get("linkedin_response"),
            "message": f"Post '{post_id}' is already finalized with status: '{current_status}'.",
        }

    # Handle approved status or approve action
    if current_status == PostStatus.APPROVED or decision_action == "approve_linkedin_post":
        content = post_record.get("content", "")
        token = post_record.get("access_token") or get_linkedin_access_token()
        profile_id = post_record.get("profile_id") or get_linkedin_person_urn()

        if not token:
            logger.error(f"Cannot publish post '{post_id}': No LinkedIn access token available.")
            if response_url:
                update_slack_message(
                    response_url=response_url,
                    post_id=post_id,
                    action_type=PostStatus.PUBLISH_FAILED.value,
                    detail_message="Failed: LinkedIn is not connected. Please connect LinkedIn via the web dashboard.",
                )
            return {"success": False, "error": "No LinkedIn access token available."}

        publish_result = publish_post_to_linkedin(
            content=content,
            access_token=token,
            person_urn=profile_id,
        )

        if publish_result.get("success"):
            post_urn = publish_result.get("post_urn", "published")
            update_post_status(
                post_id=post_id,
                status=PostStatus.PUBLISHED,
                extra_data={"linkedin_response": publish_result},
            )
            if response_url:
                update_slack_message(
                    response_url=response_url,
                    post_id=post_id,
                    action_type=PostStatus.PUBLISHED.value,
                    detail_message=f"Published successfully to LinkedIn ({post_urn})",
                )
            return {
                "success": True,
                "status": PostStatus.PUBLISHED.value,
                "post_id": post_id,
                "publish_result": publish_result,
            }

        error_message = publish_result.get("error", "Unknown error")
        update_post_status(
            post_id=post_id,
            status=PostStatus.PUBLISH_FAILED,
            extra_data={"linkedin_response": publish_result},
        )
        if response_url:
            update_slack_message(
                response_url=response_url,
                post_id=post_id,
                action_type=PostStatus.PUBLISH_FAILED.value,
                detail_message=f"Failed to publish to LinkedIn: {error_message}",
            )
        return {
            "success": False,
            "status": PostStatus.PUBLISH_FAILED.value,
            "post_id": post_id,
            "error": error_message,
        }

    # Handle discard decision
    if decision_action == "discard_linkedin_post" or current_status == "discard":
        update_post_status(post_id=post_id, status=PostStatus.DISCARDED)
        if response_url:
            update_slack_message(
                response_url=response_url,
                post_id=post_id,
                action_type="discarded",
                detail_message="Post has been marked as discarded.",
            )
        return {"success": True, "status": PostStatus.DISCARDED.value, "post_id": post_id}

    return {"success": False, "error": f"Post '{post_id}' is in unhandled state: '{current_status}'"}

def wait_for_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Poll storage until user reaches an approval decision in Slack (legacy routine)."""
    from legacy.polling import wait_for_decision as legacy_wait

    return legacy_wait(post_id=post_id, timeout_seconds=timeout_seconds)
