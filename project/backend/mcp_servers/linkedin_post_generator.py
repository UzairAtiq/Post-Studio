import logging
import time
from config.constants import PostStatus
from config.settings import get_linkedin_access_token
from data.post_storage import (
    generate_next_post_id,
    get_post_by_id,
    list_posts,
    save_post_record,
)
from mcp.server.fastmcp import FastMCP
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.approval import (
    execute_post_decision,
    record_slack_decision,
)
from services.slack_service import (
    send_approval_request,
)

# configure logger for mcp server
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("linkedin_post_mcp_server")

# initialize the fastmcp server
mcp = FastMCP("linkedin-post-generator")

# mcp tool: generate post via groq, store it, send to slack, and optionally wait for human decision
@mcp.tool()
def generate_and_request_approval(
    topic: str = "AI advancements and modern software engineering",
    wait_for_decision: bool = False,
    timeout_seconds: int = 60,
) -> dict:
    """Generate a LinkedIn post using Groq LLM, save it locally, and send to Slack with Approve/Discard buttons.
    If wait_for_decision is True, it will wait for the user to click Yes or No in Slack before returning."""
    # ensure linkedin access token is present
    token = get_linkedin_access_token()
    if not token:
        return {
            "success": False,
            "error": "LinkedIn access token not found. Please authenticate or add .linkedin_token.",
        }

    # resolve linkedin person urn
    try:
        person_urn = fetch_linkedin_person_urn(access_token=token)
    except Exception as err:
        return {
            "success": False,
            "error": f"Failed to fetch LinkedIn profile URN: {err}",
        }

    # generate post content using groq llm
    try:
        post_content = generate_linkedin_post_content(topic=topic)
    except Exception as err:
        return {
            "success": False,
            "error": f"Failed to generate post with Groq LLM: {err}",
        }

    # generate a unique internal post id
    internal_post_id = generate_next_post_id()

    # save post record to persistent storage with pending status
    save_post_record(
        post_id=internal_post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=token,
        status=PostStatus.PENDING,
    )

    # send block kit approval request to slack
    slack_result = send_approval_request(
        post_id=internal_post_id,
        content=post_content,
    )

    # return immediately if asynchronous flow requested
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

    # wait and poll for human decision, then execute linkedin publishing or discard
    return wait_for_approval_decision(post_id=internal_post_id, timeout_seconds=timeout_seconds)

# mcp tool: wait for human approval or discard decision from slack and execute action
@mcp.tool()
def wait_for_approval_decision(post_id: str, timeout_seconds: int = 60) -> dict:
    """Wait and poll until the user clicks Approve or Discard in Slack for a given post ID, then execute action."""
    start_time = time.time()
    poll_interval = 2.0

    while time.time() - start_time < timeout_seconds:
        # retrieve post state from storage
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

    # return timeout error if no user interaction detected within window
    return {
        "success": False,
        "post_id": post_id,
        "status": "timed_out",
        "message": f"Timed out after {timeout_seconds} seconds waiting for user response in Slack.",
    }

# mcp tool: directly approve or discard a post
@mcp.tool()
def approve_or_discard_post_directly(post_id: str, action: str = "approve") -> dict:
    """Directly approve or discard a pending post without using Slack UI."""
    # map action string to internal action identifier
    action_id = "approve_linkedin_post" if action.lower() in ("approve", "yes") else "discard_linkedin_post"
    record_result = record_slack_decision(action_id=action_id, post_id=post_id)
    if not record_result.get("success"):
        return record_result
    return execute_post_decision(post_id=post_id)

# mcp tool: check the status of a specific post by id
@mcp.tool()
def get_post_details(post_id: str) -> dict:
    """Retrieve full details and status of a post by its internal post ID."""
    record = get_post_by_id(post_id)
    if not record:
        return {"found": False, "error": f"Post with ID '{post_id}' not found."}

    # mask sensitive access token in output
    safe_record = dict(record)
    if safe_record.get("access_token"):
        safe_record["access_token"] = safe_record["access_token"][:10] + "..."
    return {"found": True, "post": safe_record}

# mcp tool: list all generated posts
@mcp.tool()
def list_all_generated_posts(status_filter: str = "") -> dict:
    """List all saved LinkedIn posts, optionally filtered by status (pending, published, rejected)."""
    all_records = list_posts()
    if status_filter:
        filtered = [
            post for post in all_records
            if post.get("status", "").lower() == status_filter.lower()
        ]
    else:
        filtered = all_records

    # mask access tokens in list output
    sanitized = []
    for post in filtered:
        item = dict(post)
        if item.get("access_token"):
            item["access_token"] = item["access_token"][:10] + "..."
        sanitized.append(item)

    return {"total_count": len(sanitized), "posts": sanitized}

# run server directly if executed
if __name__ == "__main__":
    mcp.run()
