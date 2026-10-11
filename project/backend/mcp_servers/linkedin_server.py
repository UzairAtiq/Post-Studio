"""Model Context Protocol (MCP) server providing tools for LinkedIn post generation and approval."""

import logging
from typing import Any

from legacy.polling import wait_for_decision as poll_for_decision
from mcp.server.fastmcp import FastMCP
from services.post_service import (
    discard_post as discard_post_service,
    generate_post as generate_post_service,
    get_post as get_post_service,
    publish_post as publish_post_service,
    request_approval as request_approval_service,
    save_post as save_post_service,
)
from services.storage import list_posts

logger = logging.getLogger("linkedin_mcp_server")

# initialize fastmcp server
mcp = FastMCP("linkedin-post-generator")


@mcp.tool()
def generate_post(
    topic: str = "AI advancements and modern software engineering",
) -> dict[str, Any]:
    """Generate technical LinkedIn post content via AI completion service.

    Purpose:
        Thin MCP wrapper around post generation service.

    Steps:
        1. Delegate directly to post_service.generate_post.
        2. Return resulting status and generated content.

    Returns:
        dict[str, Any]: Content dictionary or error explanation.

    Raises:
        None.
    """
    return generate_post_service(topic=topic)


@mcp.tool()
def save_post(
    content: str,
    post_id: str = "",
) -> dict[str, Any]:
    """Persist generated post content to storage with pending approval status.

    Purpose:
        Thin MCP wrapper around post persistence service.

    Steps:
        1. Delegate directly to post_service.save_post.
        2. Return persisted post identifier and metadata.

    Returns:
        dict[str, Any]: Saved post dictionary or error explanation.

    Raises:
        None.
    """
    return save_post_service(content=content, post_id=post_id or None)


@mcp.tool()
def request_approval(
    post_id: str,
    content: str,
) -> dict[str, Any]:
    """Dispatch Slack Block Kit interactive card requesting human post approval.

    Purpose:
        Thin MCP wrapper around Slack approval delivery service.

    Steps:
        1. Delegate directly to post_service.request_approval.
        2. Return Slack dispatch outcome.

    Returns:
        dict[str, Any]: Delivery status dictionary.

    Raises:
        None.
    """
    return request_approval_service(post_id=post_id, content=content)


@mcp.tool()
def publish_post(
    post_id: str,
) -> dict[str, Any]:
    """Publish an approved post to LinkedIn REST API.

    Purpose:
        Thin MCP wrapper around LinkedIn publication service.

    Steps:
        1. Delegate directly to post_service.publish_post.
        2. Return publication outcome.

    Returns:
        dict[str, Any]: Publication result dictionary.

    Raises:
        None.
    """
    return publish_post_service(post_id=post_id)


@mcp.tool()
def discard_post(
    post_id: str,
) -> dict[str, Any]:
    """Discard a pending post and mark status as discarded.

    Purpose:
        Thin MCP wrapper around post disposal service.

    Steps:
        1. Delegate directly to post_service.discard_post.
        2. Return disposal outcome.

    Returns:
        dict[str, Any]: Disposal status dictionary.

    Raises:
        None.
    """
    return discard_post_service(post_id=post_id)


@mcp.tool()
def get_post(
    post_id: str,
) -> dict[str, Any]:
    """Retrieve details and status for a single post by ID with sanitized credentials.

    Purpose:
        Thin MCP wrapper around post retrieval service.

    Steps:
        1. Delegate directly to post_service.get_post.
        2. Return sanitized post dictionary.

    Returns:
        dict[str, Any]: Post details dictionary.

    Raises:
        None.
    """
    return get_post_service(post_id=post_id)


# Backwards compatibility wrappers for existing callers and test suites
@mcp.tool()
def get_post_details(post_id: str) -> dict[str, Any]:
    """Legacy alias for get_post retrieving post details by ID."""
    return get_post_service(post_id=post_id)


@mcp.tool()
def list_all_generated_posts(status_filter: str = "") -> dict[str, Any]:
    """List all saved LinkedIn posts, optionally filtered by status."""
    all_records = list_posts()
    if status_filter:
        filter_val = status_filter.strip().lower()
        filtered = [
            post for post in all_records
            if (post.get("status") or "").lower() == filter_val
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


@mcp.tool()
def generate_and_request_approval(
    topic: str = "AI advancements and modern software engineering",
    wait_for_decision: bool = False,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Composite tool generating post, persisting, and dispatching approval card."""
    gen = generate_post_service(topic=topic)
    if not gen.get("success"):
        return gen

    saved = save_post_service(content=gen["content"])
    if not saved.get("success"):
        return saved

    internal_post_id = saved["post_id"]
    slack_result = request_approval_service(post_id=internal_post_id, content=gen["content"])

    if not wait_for_decision:
        return {
            "success": True,
            "post_id": internal_post_id,
            "profile_id": saved.get("profile_id"),
            "status": "pending_approval",
            "generated_content": gen["content"],
            "slack_delivery": slack_result.get("slack_delivery"),
            "message": "Post generated and sent to Slack for user approval.",
        }

    return poll_for_decision(post_id=internal_post_id, timeout_seconds=timeout_seconds)


@mcp.tool()
def wait_for_approval_decision(post_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Poll storage until human reviewer reaches an approval decision."""
    return poll_for_decision(post_id=post_id.strip(), timeout_seconds=timeout_seconds)


@mcp.tool()
def approve_or_discard_post_directly(post_id: str, action: str = "approve") -> dict[str, Any]:
    """Directly execute approval or discard decision without Slack interaction."""
    clean_id = post_id.strip()
    clean_action = action.strip().lower()
    if clean_action in ("approve", "yes", "approved", "approve_linkedin_post"):
        return publish_post_service(post_id=clean_id)
    return discard_post_service(post_id=clean_id)


if __name__ == "__main__":
    mcp.run()
