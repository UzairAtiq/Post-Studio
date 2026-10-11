"""Post approval decision recording and publication execution service."""

import logging
import time
from typing import Any

from config.constants import PostStatus
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
        3. Map action_id to corresponding PostStatus (approved or discarded).
        4. Update post record status and metadata (response_url, decision_action) in storage.
        5. Return decision confirmation dictionary.

    Returns:
        dict[str, Any]: Dictionary with success status, post_id, decision, and response_url.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    decision_status = PostStatus.APPROVED if action_id == "approve_linkedin_post" else PostStatus.DISCARDED
    update_post_status(
        post_id=post_id,
        status=decision_status,
        extra_data={"response_url": response_url, "decision_action": action_id},
    )

    return {
        "success": True,
        "post_id": post_id,
        "decision": decision_status.value,
        "response_url": response_url,
    }


def execute_post_decision(post_id: str) -> dict[str, Any]:
    """Execute post publishing or disposal based on recorded approval decision.

    Purpose:
        Publishes approved post to LinkedIn or discards it, updating post status
        and replacing the original Slack interactive message card.

    Steps:
        1. Fetch post record from storage by post_id. Return failure if not found.
        2. Inspect current post status.
        3. If approved, resolve access token and person URN, then publish via LinkedIn service.
        4. If LinkedIn publication succeeds, set status to published and update Slack message.
        5. If LinkedIn publication fails, set status to publish_failed and notify Slack.
        6. If discarded or rejected, mark status and update Slack message card.
        7. If already published, return existing result without re-publishing.
        8. Return final execution result dictionary.

    Returns:
        dict[str, Any]: Dictionary containing execution success, post_id, and details.

    Raises:
        None.
    """
    post_record = get_post_by_id(post_id)
    if not post_record:
        return {"success": False, "error": f"Post '{post_id}' not found."}

    current_status = post_record.get("status")
    response_url = post_record.get("response_url")

    # Handle approved status by publishing to LinkedIn
    if current_status == PostStatus.APPROVED:
        content = post_record.get("content", "")
        token = post_record.get("access_token") or get_linkedin_access_token()
        profile_id = post_record.get("profile_id") or get_linkedin_person_urn()

        if not token:
            logger.error(f"Cannot publish post '{post_id}': No LinkedIn access token available.")
            if response_url:
                update_slack_message(
                    response_url=response_url,
                    post_id=post_id,
                    action_type="approved",
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
                    action_type="approved",
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
                action_type="approved",
                detail_message=f"Failed to publish to LinkedIn: {error_message}",
            )
        return {
            "success": False,
            "status": PostStatus.PUBLISH_FAILED.value,
            "post_id": post_id,
            "error": error_message,
        }

    # Handle discarded or rejected status
    if current_status in (PostStatus.DISCARDED, PostStatus.REJECTED):
        update_post_status(post_id=post_id, status=PostStatus.REJECTED)
        if response_url:
            update_slack_message(
                response_url=response_url,
                post_id=post_id,
                action_type="discarded",
                detail_message="Post has been marked as discarded.",
            )
        return {"success": True, "status": PostStatus.REJECTED.value, "post_id": post_id}

    # Handle already published status
    if current_status == PostStatus.PUBLISHED:
        return {
            "success": True,
            "status": PostStatus.PUBLISHED.value,
            "post_id": post_id,
            "publish_result": post_record.get("linkedin_response"),
        }

    return {"success": False, "error": f"Post '{post_id}' is in unhandled state: '{current_status}'"}


def wait_for_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Poll storage until user reaches an approval decision in Slack, then execute action.

    Purpose:
        Periodically checks storage at 2-second intervals for Slack approval or discard
        decisions and triggers execution when detected or times out.

    Steps:
        1. Record start time and initialize 2-second polling interval.
        2. Enter polling loop while elapsed duration is under timeout_seconds.
        3. Query storage for current post record state.
        4. If post is missing, return error immediately.
        5. If status is approved or discarded, call execute_post_decision and return.
        6. If status is already finalized (published, rejected, publish_failed), return current state.
        7. Sleep for 2.0 seconds between poll cycles.
        8. If timeout expires without interaction, return timed_out status dictionary.

    Returns:
        dict[str, Any]: Dictionary containing final execution outcome, status, or timeout report.

    Raises:
        None.
    """
    start_time = time.time()
    poll_interval = 2.0

    while time.time() - start_time < timeout_seconds:
        record = get_post_by_id(post_id)
        if not record:
            return {"success": False, "error": f"Post '{post_id}' not found."}

        current_status = record.get("status")

        # execute decision if approved or discarded in slack
        if current_status in (PostStatus.APPROVED, PostStatus.DISCARDED):
            return execute_post_decision(post_id=post_id)

        # return finalized status if already resolved
        if current_status in (PostStatus.PUBLISHED, PostStatus.REJECTED, PostStatus.PUBLISH_FAILED):
            return {
                "success": current_status == PostStatus.PUBLISHED,
                "post_id": post_id,
                "status": current_status,
                "linkedin_response": record.get("linkedin_response"),
                "message": f"Post finalized with status: '{current_status}'",
            }

        time.sleep(poll_interval)

    return {
        "success": False,
        "post_id": post_id,
        "status": "timed_out",
        "message": f"Timed out after {timeout_seconds} seconds waiting for user response in Slack.",
    }
