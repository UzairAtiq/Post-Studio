"""Legacy polling routines for post approval detection."""

import time
from typing import Any

from config.constants import MAX_APPROVAL_TIMEOUT_SECONDS, PostStatus
from services.approval import execute_post_decision
from services.storage import get_post_by_id


def wait_for_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Poll storage until user reaches an approval decision in Slack, then execute action.

    Purpose:
        Periodically checks storage at 2-second intervals for Slack approval or discard
        decisions and triggers execution when detected or times out.

    Steps:
        1. Cap timeout_seconds between 1 and MAX_APPROVAL_TIMEOUT_SECONDS constant.
        2. Record start time and initialize 2-second polling interval.
        3. Enter polling loop while elapsed duration is under effective timeout.
        4. Query storage for current post record state.
        5. If post is missing, return error immediately.
        6. If status is approved or discarded, call execute_post_decision and return.
        7. If status is already finalized (published, discarded, publish_failed), return current state.
        8. Sleep for 2.0 seconds between poll cycles.
        9. If timeout expires without interaction, return timed_out status dictionary.

    Returns:
        dict[str, Any]: Dictionary containing final execution outcome, status, or timeout report.

    Raises:
        None.
    """
    effective_timeout = min(max(1, timeout_seconds), MAX_APPROVAL_TIMEOUT_SECONDS)
    start_time = time.time()
    poll_interval = 2.0

    while time.time() - start_time < effective_timeout:
        record = get_post_by_id(post_id)
        if not record:
            return {"success": False, "error": f"Post '{post_id}' not found."}

        current_status = record.get("status")

        # execute decision if approved or discarded in slack
        if current_status in (PostStatus.APPROVED, PostStatus.DISCARDED):
            return execute_post_decision(post_id=post_id)

        # return finalized status if already resolved
        if current_status in (PostStatus.PUBLISHED, PostStatus.DISCARDED, PostStatus.PUBLISH_FAILED):
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
