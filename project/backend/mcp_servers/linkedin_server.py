"""Model Context Protocol (MCP) server providing tools for LinkedIn post generation and approval."""

import logging
from typing import Any

from config.constants import PostStatus
from config.settings import get_linkedin_access_token
from mcp.server.fastmcp import FastMCP
from services.approval import (
    execute_post_decision,
    record_slack_decision,
    wait_for_decision,
)
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import send_approval_request
from services.storage import (
    generate_next_post_id,
    get_post_by_id,
    list_posts,
    save_post_record,
)

logger = logging.getLogger("linkedin_mcp_server")

# initialize fastmcp server
mcp = FastMCP("linkedin-post-generator")


@mcp.tool()
def generate_and_request_approval(
    topic: str = "AI advancements and modern software engineering",
    wait_for_decision: bool = False,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Generate a LinkedIn post using AI, store it, send to Slack, and optionally await human decision.

    Purpose:
        Thin MCP tool orchestrating AI post drafting, storage persistence, and Slack notification.

    Steps:
        1. Validate topic argument is non-empty.
        2. Verify LinkedIn access token availability.
        3. Resolve LinkedIn author profile URN.
        4. Generate technical post content using Groq LLM service.
        5. Generate unique post identifier and persist to storage with pending status.
        6. Dispatch interactive Block Kit approval card to Slack.
        7. If wait_for_decision is False, return pending approval response immediately.
        8. If wait_for_decision is True, delegate to approval service wait_for_decision and return result.

    Returns:
        dict[str, Any]: Post generation summary or final decision execution result.

    Raises:
        None.
    """
    if not topic or not topic.strip():
        return {"success": False, "error": "Topic cannot be empty."}

    token = get_linkedin_access_token()
    if not token:
        return {
            "success": False,
            "error": "LinkedIn access token not found. Please authenticate or add .linkedin_token.",
        }

    try:
        person_urn = fetch_linkedin_person_urn(access_token=token)
    except Exception as err:
        return {
            "success": False,
            "error": f"Failed to fetch LinkedIn profile URN: {err}",
        }

    try:
        post_content = generate_linkedin_post_content(topic=topic)
    except Exception as err:
        return {
            "success": False,
            "error": f"Failed to generate post with Groq LLM: {err}",
        }

    internal_post_id = generate_next_post_id()

    save_post_record(
        post_id=internal_post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=token,
        status=PostStatus.PENDING,
    )

    slack_result = send_approval_request(
        post_id=internal_post_id,
        content=post_content,
    )

    if not wait_for_decision:
        return {
            "success": True,
            "post_id": internal_post_id,
            "profile_id": person_urn,
            "status": "pending_approval",
            "generated_content": post_content,
            "slack_delivery": slack_result,
            "message": "Post generated and sent to Slack for user approval.",
        }

    return wait_for_decision(post_id=internal_post_id, timeout_seconds=timeout_seconds)


@mcp.tool()
def wait_for_approval_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Wait and poll until human reviewer clicks Approve or Discard in Slack, then execute action.

    Purpose:
        Thin MCP tool delegating polling and decision execution to approval service.

    Steps:
        1. Validate post_id is provided and non-empty.
        2. Validate timeout_seconds is a positive integer.
        3. Delegate to approval.wait_for_decision and return outcome.

    Returns:
        dict[str, Any]: Execution outcome dictionary once decision is reached or timeout occurs.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"success": False, "error": "post_id must be provided."}

    return wait_for_decision(post_id=post_id.strip(), timeout_seconds=max(1, timeout_seconds))


@mcp.tool()
def approve_or_discard_post_directly(post_id: str, action: str = "approve") -> dict[str, Any]:
    """Directly approve or discard a pending post without using Slack UI.

    Purpose:
        Thin MCP tool to programmatically record and execute an approval or discard decision.

    Steps:
        1. Validate post_id is non-empty.
        2. Map action string to internal action identifier (approve_linkedin_post or discard_linkedin_post).
        3. Record decision in storage via approval.record_slack_decision.
        4. If recording fails, return error response.
        5. Execute decision via approval.execute_post_decision and return outcome.

    Returns:
        dict[str, Any]: Outcome dictionary indicating publication or disposal result.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"success": False, "error": "post_id must be provided."}

    clean_post_id = post_id.strip()
    action_id = "approve_linkedin_post" if action.lower() in ("approve", "yes") else "discard_linkedin_post"

    record_result = record_slack_decision(action_id=action_id, post_id=clean_post_id)
    if not record_result.get("success"):
        return record_result

    return execute_post_decision(post_id=clean_post_id)


@mcp.tool()
def get_post_details(post_id: str) -> dict[str, Any]:
    """Retrieve full details and status of a post by its internal post ID.

    Purpose:
        Thin MCP tool fetching a single post record with masked credentials.

    Steps:
        1. Validate post_id is non-empty.
        2. Query storage service for post record matching post_id.
        3. If record does not exist, return found=False dictionary.
        4. Mask access token before returning.
        5. Return found=True and safe post details.

    Returns:
        dict[str, Any]: Dictionary containing found boolean and sanitized post details.

    Raises:
        None.
    """
    if not post_id or not post_id.strip():
        return {"found": False, "error": "post_id must be provided."}

    record = get_post_by_id(post_id.strip())
    if not record:
        return {"found": False, "error": f"Post with ID '{post_id}' not found."}

    safe_record = dict(record)
    if safe_record.get("access_token"):
        safe_record["access_token"] = safe_record["access_token"][:10] + "..."
    return {"found": True, "post": safe_record}


@mcp.tool()
def list_all_generated_posts(status_filter: str = "") -> dict[str, Any]:
    """List all saved LinkedIn posts, optionally filtered by status.

    Purpose:
        Thin MCP tool listing stored posts with token sanitization and optional status filtering.

    Steps:
        1. Query storage service for all post records.
        2. If status_filter is provided, filter records case-insensitively.
        3. Mask access tokens in output list.
        4. Return total count and sanitized records list.

    Returns:
        dict[str, Any]: Dictionary containing total_count and list of sanitized post records.

    Raises:
        None.
    """
    all_records = list_posts()
    if status_filter:
        filtered = [
            post for post in all_records
            if post.get("status", "").lower() == status_filter.strip().lower()
        ]
    else:
        filtered = all_records

    sanitized = []
    for post in filtered:
        item = dict(post)
        if item.get("access_token"):
            item["access_token"] = item["access_token"][:10] + "..."
        sanitized.append(item)

    return {"total_count": len(sanitized), "posts": sanitized}


if __name__ == "__main__":
    mcp.run()
