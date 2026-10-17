"""LinkedIn post generation, management, and CRUD API router."""

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from config.constants import PostStatus
from config.settings import (
    POST_FLOW_MODE,
    get_linkedin_person_urn,
)
from routers.auth import get_current_user
from services.storage import (
    delete_post_record,
    generate_next_post_id,
    get_post_by_id,
    get_valid_linkedin_access_token,
    list_posts,
    save_post_record,
)
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import send_approval_request
from utils import get_index_html_response

router = APIRouter(tags=["Posts"])


# list all saved posts (or return React app if visited directly by browser)
@router.get("/posts")
def get_all_posts(request: Request, status_filter: str | None = None) -> Any:
    """List stored posts with optional filtering or serve frontend for direct browser navigation.

    Purpose:
        Returns all stored LinkedIn posts as JSON (with token masking and optional
        status filtering), or serves the frontend SPA if accessed directly in a browser.

    Steps:
        1. Check the request Accept header for text/html without X-Requested-With.
        2. If accessed by a browser, return index.html for client-side routing.
        3. Query storage for all saved post records.
        4. If status_filter is supplied, filter records by status case-insensitively.
        5. Mask sensitive access tokens in post records.
        6. Return JSON payload containing total count and the posts list.

    Returns:
        Any: HTMLResponse if visited by a web browser, or dict containing total_count and posts.

    Raises:
        None.
    """
    # If a browser requests the page directly (e.g. reload on /posts), return the React app
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header and not request.headers.get("x-requested-with"):
        return get_index_html_response()

    records = list_posts()
    if status_filter:
        records = [
            post for post in records
            if post.get("status", "").lower() == status_filter.lower()
        ]

    # mask sensitive access tokens before returning
    sanitized_records = []
    for record in records:
        safe_copy = dict(record)
        if safe_copy.get("access_token"):
            safe_copy["access_token"] = safe_copy["access_token"][:10] + "..."
        sanitized_records.append(safe_copy)

    return {"total_count": len(sanitized_records), "posts": sanitized_records}


# retrieve single post by id
@router.get("/posts/{post_id}")
def get_single_post(post_id: str) -> dict[str, Any]:
    """Retrieve details of a single post by its unique ID.

    Purpose:
        Fetches an individual post record from persistent storage and sanitizes
        its access token before returning.

    Steps:
        1. Query storage for a post record matching post_id.
        2. If no record is found, raise HTTP 404 Not Found.
        3. Mask access token characters if present in record.
        4. Return the sanitized post record.

    Returns:
        dict[str, Any]: Post data dictionary including content, status, and metadata.

    Raises:
        HTTPException: 404 Not Found if post_id does not exist.
    """
    record = get_post_by_id(post_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post '{post_id}' not found.",
        )

    safe_copy = dict(record)
    if safe_copy.get("access_token"):
        safe_copy["access_token"] = safe_copy["access_token"][:10] + "..."
    return safe_copy


# delete single post by id
@router.delete("/posts/{post_id}")
def delete_post_endpoint(post_id: str) -> dict[str, Any]:
    """Delete a stored post record by its unique ID.

    Purpose:
        Removes a post record from persistent storage after verifying its existence.

    Steps:
        1. Look up the post in storage to confirm it exists.
        2. If not found, raise HTTP 404 Not Found.
        3. Delete the post record from storage.
        4. Return confirmation payload indicating success.

    Returns:
        dict[str, Any]: Dictionary with success boolean, post_id, and confirmation message.

    Raises:
        HTTPException: 404 Not Found if post_id does not exist.
    """
    record = get_post_by_id(post_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post '{post_id}' not found.",
        )

    deleted = delete_post_record(post_id)
    return {
        "success": deleted,
        "post_id": post_id,
        "message": f"Post '{post_id}' deleted successfully.",
    }


def check_post_ownership(post: dict[str, Any], user_sub: str) -> bool:
    """Verify whether the authenticated user owns the given post record.

    Purpose:
        Compares post owner_sub or profile_id with current user sub, stripping
        urn:li:person: prefixes to prevent unauthorized access.

    Steps:
        1. Extract owner_sub or profile_id from post.
        2. Allow if unowned or default token mode.
        3. Match exact string or stripped bare IDs.
        4. Return True if match, False otherwise.

    Returns:
        bool: True if user owns the post, False otherwise.

    Raises:
        None.
    """
    owner = post.get("owner_sub") or post.get("profile_id")
    if not owner or owner == "default" or user_sub == "default":
        return True
    if owner == user_sub:
        return True
    bare_owner = owner.replace("urn:li:person:", "").strip()
    bare_user = user_sub.replace("urn:li:person:", "").strip()
    return bare_owner == bare_user


@router.post("/posts/{post_id}/approve")
def approve_post_dashboard_endpoint(
    post_id: str,
    user_sub: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Approve and publish a pending post from the web dashboard.

    Purpose:
        Validates post ownership for the authenticated user and executes
        the shared LinkedIn publication and Slack card update logic.

    Steps:
        1. Query post by post_id from storage. If not found, raise 404.
        2. Validate that user_sub owns this post. If not, raise 403 Forbidden.
        3. Call shared approve_post(post_id).
        4. Return execution result dictionary.

    Returns:
        dict[str, Any]: Publication outcome dictionary.

    Raises:
        HTTPException: 404 if post not found, 403 if user does not own post, 401 if unauthenticated.
    """
    post = get_post_by_id(post_id)
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post '{post_id}' not found.",
        )

    if not check_post_ownership(post=post, user_sub=user_sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to approve this post.",
        )

    from services.approval import approve_post

    result = approve_post(post_id=post_id)
    if not result.get("success") and result.get("status") != "published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to approve post."),
        )
    return result


@router.post("/posts/{post_id}/reject")
def reject_post_dashboard_endpoint(
    post_id: str,
    user_sub: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Reject and discard a pending post from the web dashboard.

    Purpose:
        Validates post ownership for the authenticated user and executes
        the shared rejection and Slack card update logic.

    Steps:
        1. Query post by post_id from storage. If not found, raise 404.
        2. Validate that user_sub owns this post. If not, raise 403 Forbidden.
        3. Call shared reject_post(post_id).
        4. Return rejection outcome dictionary.

    Returns:
        dict[str, Any]: Rejection outcome dictionary.

    Raises:
        HTTPException: 404 if post not found, 403 if user does not own post, 401 if unauthenticated.
    """
    post = get_post_by_id(post_id)
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post '{post_id}' not found.",
        )

    if not check_post_ownership(post=post, user_sub=user_sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to reject this post.",
        )

    from services.approval import reject_post

    return reject_post(post_id=post_id)


# trigger post generation and slack approval request for testing
@router.post("/posts/generate")
async def generate_post_endpoint(
    request: Request,
    topic: str = Query(default="AI advancements and modern software engineering"),
    user_sub: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate a LinkedIn post using AI and send an interactive Slack approval DM if linked.

    Purpose:
        Automates the creation of a LinkedIn post on a given topic using Groq LLM,
        stores it with pending status and owner_sub, and sends a DM to the author's Slack account
        if mapped, or leaves it for web dashboard approval if unlinked.

    Steps:
        1. Extract authenticated user sub from dependency.
        2. If POST_FLOW_MODE is 'graph', invoke services.post_graph.start_post_flow.
        3. Otherwise, invoke legacy.post_flow.run_legacy_post_flow.
        4. Return summary dictionary with post ID, status, content, and dm_sent status.

    Returns:
        dict[str, Any]: Summary dictionary with post_id, status, generated content, and DM delivery info.

    Raises:
        HTTPException: 401 Unauthorized if LinkedIn is not authenticated.
    """
    session_id = request.cookies.get("session_id")

    if POST_FLOW_MODE == "graph":
        from services.post_graph import start_post_flow

        if session_id:
            return await start_post_flow(topic=topic, user_sub=user_sub)
        return await start_post_flow(topic=topic)

    from legacy.post_flow import run_legacy_post_flow

    return run_legacy_post_flow(topic=topic, user_sub=user_sub)

