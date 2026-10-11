"""Guard conditions and terminal validation checks for post flow completion."""

import logging
from typing import Any, Mapping

from config.constants import PostStatus
from graphs.post_flow.state import PostFlowState
from services.storage import get_post_by_id

logger = logging.getLogger("post_flow_guards")


def is_flow_complete(state: PostFlowState | Mapping[str, Any]) -> bool:
    """Validate strict completion criteria for post generation and review workflow.

    Purpose:
        Acts as the final authority on whether a post workflow has successfully concluded.
        Completion requires final status (published or discarded), stored human decision,
        and confirmed LinkedIn publication response when published.

    Steps:
        1. Extract status, decision, post_id, and linkedin_response from state or storage.
        2. If post_id exists, merge current storage record to verify persistent state.
        3. Verify status is either 'published' or 'discarded'.
        4. If status is 'published', verify decision is 'approved' and linkedin_response is recorded.
        5. If status is 'discarded', verify decision is recorded as discarded/rejected.
        6. Return True only if all criteria are satisfied; otherwise False.

    Returns:
        bool: True if flow satisfies completion rules, False otherwise.

    Raises:
        None.
    """
    post_id = state.get("post_id", "")
    current_status = state.get("status", "")
    decision = state.get("decision", "")
    linkedin_response = state.get("linkedin_response")

    # If post_id is known, check storage for ground truth verification
    if post_id:
        record = get_post_by_id(post_id)
        if record:
            current_status = record.get("status", current_status)
            decision = decision or record.get("decision_action", "")
            linkedin_response = linkedin_response or record.get("linkedin_response")

    # Normalize status and decision strings
    norm_status = str(current_status).lower().strip()
    norm_decision = str(decision).lower().strip()

    # Rule 1: status must be published or discarded
    if norm_status not in (PostStatus.PUBLISHED.value, PostStatus.DISCARDED.value, "published", "discarded"):
        logger.info(f"Guard check failed for post_id={post_id}: status '{norm_status}' is not terminal.")
        return False

    # Rule 2: If status is published, decision must be approved and LinkedIn response stored
    if norm_status in (PostStatus.PUBLISHED.value, "published"):
        decision_approved = norm_decision in ("approved", "approve_linkedin_post")
        if not decision_approved:
            logger.warning(f"Guard check failed for post_id={post_id}: published without approved decision.")
            return False

        if not linkedin_response or not isinstance(linkedin_response, dict):
            logger.warning(f"Guard check failed for post_id={post_id}: published without stored LinkedIn response.")
            return False

        has_urn_or_success = bool(
            linkedin_response.get("post_urn")
            or linkedin_response.get("urn")
            or linkedin_response.get("success")
        )
        if not has_urn_or_success:
            logger.warning(f"Guard check failed for post_id={post_id}: missing post URN in LinkedIn response.")
            return False

        return True

    # Rule 3: If status is discarded, decision must be stored as discarded or rejected
    if norm_status in (PostStatus.DISCARDED.value, "discarded"):
        decision_discarded = norm_decision in ("discarded", "rejected", "discard_linkedin_post")
        if not decision_discarded:
            logger.warning(f"Guard check failed for post_id={post_id}: discarded without stored discard decision.")
            return False
        return True

    return False


def can_publish_post(state: PostFlowState | Mapping[str, Any]) -> bool:
    """Check whether a post satisfies pre-conditions for LinkedIn publication.

    Purpose:
        Guards publish node against unauthorized or duplicate publication attempts.

    Steps:
        1. Verify stored decision is 'approved'.
        2. Verify post is not already published (prevent double posting).

    Returns:
        bool: True if post can be published, False otherwise.

    Raises:
        None.
    """
    post_id = state.get("post_id", "")
    decision = str(state.get("decision", "")).lower().strip()
    status_val = str(state.get("status", "")).lower().strip()

    if post_id:
        record = get_post_by_id(post_id)
        if record:
            status_val = str(record.get("status", status_val)).lower().strip()
            decision = decision or str(record.get("decision_action", "")).lower().strip()

    if status_val in ("published", PostStatus.PUBLISHED.value):
        logger.warning(f"can_publish_post blocked for post_id={post_id}: already published.")
        return False

    return decision in ("approved", "approve_linkedin_post")
