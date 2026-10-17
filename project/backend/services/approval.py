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

    decision_status = (
        PostStatus.APPROVED
        if action_id in ("approve_linkedin_post", "approve_post")
        else post_record.get("status", PostStatus.PENDING)
    )
    update_post_status(
        post_id=post_id,
        status=decision_status,
        extra_data={"response_url": response_url, "decision_action": action_id},
    )

    decision_name = "approved" if action_id in ("approve_linkedin_post", "approve_post") else "discarded"
    return {
        "success": True,
        "post_id": post_id,
        "decision": decision_name,
        "response_url": response_url,
    }


def _notify_slack_resolution(
    post_record: dict[str, Any],
    post_id: str,
    text: str,
    action_type: str,
    detail_message: str = "",
) -> None:
    """Update Slack DM and/or response_url card with resolution message.

    Purpose:
        Propagates approval or rejection feedback back to Slack via direct message
        update (chat.update) and/or interaction response_url.

    Steps:
        1. If post has slack_channel and slack_ts, call update_message.
        2. If post has response_url, call update_slack_message.
        3. Catch and log any exceptions quietly.

    Returns:
        None.

    Raises:
        None.
    """
    channel = post_record.get("slack_channel")
    ts = post_record.get("slack_ts")
    if channel and ts:
        try:
            from services.slack import update_message

            update_message(channel=channel, ts=ts, text=text)
        except Exception as dm_err:
            logger.warning(f"Failed to update Slack DM for post '{post_id}': {dm_err}")

    response_url = post_record.get("response_url")
    if response_url:
        try:
            update_slack_message(
                response_url=response_url,
                post_id=post_id,
                action_type=action_type,
                detail_message=detail_message,
            )
        except Exception as hook_err:
            logger.warning(f"Failed to update Slack response_url for post '{post_id}': {hook_err}")


def approve_post(post_id: str) -> dict[str, Any]:
    """Approve a pending post, publish it to LinkedIn, and update Slack messages.

    Purpose:
        Executes approval for a pending post by decrypting the author's LinkedIn access token,
        submitting the post to LinkedIn REST API, updating status to published (or publish_failed),
        and replacing the interactive buttons in Slack DM or channel.

    Steps:
        1. Fetch post record from storage. Return failure if not found.
        2. Idempotency check: if status is already published, return current state.
        3. If status is discarded, return failure explanation.
        4. Resolve author's access token and person URN using owner_sub or profile_id.
        5. Submit content to LinkedIn REST API via publish_post_to_linkedin.
        6. On success:
           - Update post status to PostStatus.PUBLISHED in storage.
           - Notify Slack resolution via DM or webhook.
        7. On failure:
           - Update post status to PostStatus.PUBLISH_FAILED in storage.
           - Notify Slack resolution with error notice.
        8. Return execution result dictionary.

    Returns:
        dict[str, Any]: Execution outcome dictionary.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    current_status = post_record.get("status")
    if current_status in (PostStatus.PUBLISHED, "published"):
        return {
            "success": True,
            "status": "published",
            "post_id": post_id,
            "publish_result": post_record.get("linkedin_response"),
            "message": f"Post '{post_id}' is already published.",
        }

    if current_status in (PostStatus.DISCARDED, "discarded"):
        return {
            "success": False,
            "status": "discarded",
            "post_id": post_id,
            "error": f"Post '{post_id}' has already been rejected/discarded.",
        }

    owner_sub = post_record.get("owner_sub") or post_record.get("profile_id")
    token = None
    if owner_sub:
        from services.storage import get_valid_linkedin_access_token

        token = get_valid_linkedin_access_token(token_id=owner_sub)

    if not token and post_record.get("access_token"):
        token = post_record.get("access_token")

    if not token:
        from services.storage import get_valid_linkedin_access_token

        token = get_valid_linkedin_access_token(token_id="default") or get_linkedin_access_token()

    person_urn = post_record.get("profile_id") or owner_sub or get_linkedin_person_urn()
    if person_urn and not person_urn.startswith("urn:li:person:"):
        person_urn = f"urn:li:person:{person_urn}"

    if not token:
        err_msg = "No LinkedIn access token available for post author."
        logger.error(f"Cannot publish post '{post_id}': {err_msg}")
        update_post_status(post_id=post_id, status=PostStatus.PUBLISH_FAILED)
        _notify_slack_resolution(
            post_record=post_record,
            post_id=post_id,
            text=f"⚠️ Post `{post_id}` approval failed: No LinkedIn access token available.",
            action_type=PostStatus.PUBLISH_FAILED.value,
            detail_message="Failed: LinkedIn is not connected.",
        )
        return {"success": False, "status": "publish_failed", "post_id": post_id, "error": err_msg}

    content = post_record.get("content", "")
    publish_result = publish_post_to_linkedin(
        content=content,
        access_token=token,
        person_urn=person_urn,
    )

    if publish_result.get("success"):
        post_urn = publish_result.get("post_urn", "published")
        update_post_status(
            post_id=post_id,
            status=PostStatus.PUBLISHED,
            extra_data={"linkedin_response": publish_result},
        )
        _notify_slack_resolution(
            post_record=post_record,
            post_id=post_id,
            text=f"✅ Post `{post_id}` was approved and published to LinkedIn! ({post_urn})",
            action_type=PostStatus.PUBLISHED.value,
            detail_message=f"Published successfully to LinkedIn ({post_urn})",
        )
        return {
            "success": True,
            "status": "published",
            "post_id": post_id,
            "publish_result": publish_result,
            "linkedin_response": publish_result,
        }

    error_message = publish_result.get("error", "Unknown LinkedIn error")
    logger.error(f"Publish failed for post '{post_id}': {error_message}")
    update_post_status(
        post_id=post_id,
        status=PostStatus.PUBLISH_FAILED,
        extra_data={"linkedin_response": publish_result},
    )
    _notify_slack_resolution(
        post_record=post_record,
        post_id=post_id,
        text=f"⚠️ Post `{post_id}` was approved, but publishing to LinkedIn failed: {error_message}",
        action_type=PostStatus.PUBLISH_FAILED.value,
        detail_message=f"Failed to publish to LinkedIn: {error_message}",
    )
    return {
        "success": False,
        "status": "publish_failed",
        "post_id": post_id,
        "error": error_message,
    }


def reject_post(post_id: str) -> dict[str, Any]:
    """Reject and discard a pending post and update Slack messages.

    Purpose:
        Marks a pending post as discarded in persistent storage and notifies Slack.

    Steps:
        1. Fetch post record from storage. Return failure if not found.
        2. Idempotency check: if status is already discarded, return current state.
        3. Update post status to PostStatus.DISCARDED in storage.
        4. Notify Slack DM and response_url if present.
        5. Return rejection outcome dictionary.

    Returns:
        dict[str, Any]: Outcome dictionary confirming discarded status.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    current_status = post_record.get("status")
    if current_status in (PostStatus.DISCARDED, "discarded"):
        return {"success": True, "status": "discarded", "post_id": post_id}

    update_post_status(post_id=post_id, status=PostStatus.DISCARDED)
    _notify_slack_resolution(
        post_record=post_record,
        post_id=post_id,
        text=f"❌ Post `{post_id}` was rejected.",
        action_type="discarded",
        detail_message="Post has been rejected and discarded.",
    )
    return {"success": True, "status": "discarded", "post_id": post_id}


def execute_post_decision(post_id: str) -> dict[str, Any]:
    """Execute post publishing or disposal based on recorded approval decision.

    Purpose:
        Publishes approved post to LinkedIn or discards it, updating post status
        and replacing the original Slack interactive message card. Idempotent:
        does nothing if post is already finalized.

    Steps:
        1. Fetch post record from storage by post_id. Return failure if not found.
        2. Idempotency check: if status is already published, publish_failed, or discarded,
           return current state dictionary immediately.
        3. If approved or action is approve, delegate to approve_post.
        4. If discard or action is discard/reject, delegate to reject_post.
        5. Return execution outcome dictionary.

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
    if current_status == PostStatus.APPROVED or decision_action in ("approve_linkedin_post", "approve_post"):
        return approve_post(post_id=post_id)

    # Handle discard decision
    if decision_action in ("discard_linkedin_post", "reject_post") or current_status in ("discard", "discarded"):
        return reject_post(post_id=post_id)

    return {"success": False, "error": f"Post '{post_id}' is in unhandled state: '{current_status}'"}


def wait_for_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Poll storage until user reaches an approval decision in Slack (legacy routine)."""
    from legacy.polling import wait_for_decision as legacy_wait

    return legacy_wait(post_id=post_id, timeout_seconds=timeout_seconds)
