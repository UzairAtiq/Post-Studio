import json
import logging
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests
from config.constants import PostStatus
from config.settings import (
    DATABASE_URL,
    PORT,
    get_linkedin_person_urn,
)
from data.post_storage import (
    delete_post_record,
    generate_next_post_id,
    get_post_by_id,
    list_posts,
    save_post_record,
)
from data.token_storage import (
    get_valid_linkedin_access_token,
)
from routers.auth import router as auth_router
from routers.slack import router as slack_router
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import (
    send_approval_request,
)
from utils import (
    ASSETS_DIR,
    DIST_DIR,
    FRONTEND_DIR,
    get_index_html_response,
)

# configure application logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main_api")

# initialize fastapi app
app = FastAPI(
    title="LinkedIn Post Automation & Slack Approval API",
    description="Unified API service managing LinkedIn OAuth, automated token refreshes, Slack interactive approvals, and post publishing.",
    version="2.0.0",
)

# mount frontend static and built react assets
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# register api routers
app.include_router(auth_router)
app.include_router(slack_router)

# root dashboard endpoint
@app.get("/", response_class=HTMLResponse)
async def root_dashboard() -> HTMLResponse:
    return get_index_html_response()

# health check endpoint for cloud hosts
@app.get("/health")
def health_check() -> dict[str, Any]:
    token_valid = bool(get_valid_linkedin_access_token())
    return {
        "status": "healthy",
        "service": "linkedin-slack-agent",
        "database": "postgresql" if DATABASE_URL else "sqlite",
        "linkedin_authenticated": token_valid,
    }

# list all saved posts (or return React app if visited directly by browser)
@app.get("/posts")
def get_all_posts(request: Request, status_filter: str | None = None) -> Any:
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

# client-side routing fallback for React SPA
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str, request: Request) -> Any:
    if full_path.startswith(("health", "linkedin", "slack", "posts", "docs", "redoc", "openapi.json", "assets", "static")):
        raise HTTPException(status_code=404, detail="Not found")
    return get_index_html_response()

# retrieve single post by id
@app.get("/posts/{post_id}")
def get_single_post(post_id: str) -> dict[str, Any]:
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
@app.delete("/posts/{post_id}")
def delete_post_endpoint(post_id: str) -> dict[str, Any]:
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

# trigger post generation and slack approval request for testing
@app.post("/posts/generate")
def generate_post_endpoint(
    topic: str = Query(default="AI advancements and modern software engineering"),
) -> dict[str, Any]:
    token = get_valid_linkedin_access_token()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LinkedIn is not authenticated. Please visit /linkedin/login first.",
        )

    person_urn = get_linkedin_person_urn() or fetch_linkedin_person_urn(token)
    post_content = generate_linkedin_post_content(topic=topic)
    post_id = generate_next_post_id()

    save_post_record(
        post_id=post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=token,
        status=PostStatus.PENDING,
    )

    slack_res = send_approval_request(
        post_id=post_id,
        content=post_content,
    )

    return {
        "success": True,
        "post_id": post_id,
        "person_urn": person_urn,
        "status": "pending_approval",
        "generated_content": post_content,
        "slack_delivery": slack_res,
        "message": "Post generated and sent to Slack for user approval.",
    }

# run server directly when executed
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
