import json
import logging
import urllib.parse
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
from config.constants import PostStatus
from config.settings import (
    BASE_DIR,
    DATABASE_URL,
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
    LINKEDIN_REDIRECT_URI,
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
    get_linkedin_token_record,
    get_valid_linkedin_access_token,
    refresh_linkedin_access_token,
    save_linkedin_token,
)
from routers.slack import router as slack_router
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import (
    send_approval_request,
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
FRONTEND_DIR = (BASE_DIR.parent / "frontend").resolve() if (BASE_DIR.parent / "frontend").exists() else (BASE_DIR / "frontend").resolve()
DIST_DIR = FRONTEND_DIR / "dist"
ASSETS_DIR = DIST_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# register api routers
app.include_router(slack_router)

def get_index_html_response() -> HTMLResponse:
    index_file = DIST_DIR / "index.html" if DIST_DIR.exists() else FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(
            content=index_file.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse(content="<h2>LinkedIn Agent API Running</h2><a href='/docs'>Swagger Docs</a>")

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

# resolve effective redirect uri from explicit config or incoming request headers
def get_effective_redirect_uri(request: Request) -> str:
    # check explicit non-localhost redirect uri in environment
    if LINKEDIN_REDIRECT_URI and not LINKEDIN_REDIRECT_URI.startswith("http://localhost") and not LINKEDIN_REDIRECT_URI.startswith("http://127.0.0.1"):
        return LINKEDIN_REDIRECT_URI.strip()

    # detect protocol and host from cloud reverse proxy headers (e.g. Render, Railway)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    if host and "localhost" not in host and "127.0.0.1" not in host:
        return f"{proto}://{host}/linkedin/callback"

    return LINKEDIN_REDIRECT_URI or f"{proto}://{host}/linkedin/callback"

# linkedin oauth authorization redirect
@app.get("/linkedin/login")
def linkedin_login(request: Request) -> RedirectResponse:
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LINKEDIN_CLIENT_ID is not configured in environment.",
        )

    redirect_uri = get_effective_redirect_uri(request)
    oauth_state = uuid.uuid4().hex
    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": oauth_state,
        "scope": "openid profile w_member_social email",
    }
    auth_url = f"https://www.linkedin.com/oauth/v2/authorization?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=auth_url)

# linkedin oauth callback endpoint (supports both /linkedin/callback and legacy /callback)
@app.get("/linkedin/callback", response_class=HTMLResponse)
@app.get("/callback", response_class=HTMLResponse)
async def linkedin_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> Response:
    error_template = FRONTEND_DIR / "error.html"

    if error:
        logger.error(f"LinkedIn OAuth error: {error} - {error_description}")
        err_msg = error_description or error
        if error_template.exists():
            html_content = error_template.read_text(encoding="utf-8").replace("{{ERROR_MESSAGE}}", err_msg)
            return HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=html_content)
        return HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=f"<h2>Error: {err_msg}</h2>")

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization code missing from LinkedIn callback.",
        )

    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LinkedIn client credentials not configured.",
        )

    # exchange authorization code for access and refresh tokens
    redirect_uri = get_effective_redirect_uri(request)
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    payload = {
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": redirect_uri,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
    }

    try:
        response = requests.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
            timeout=15,
        )
        token_data = response.json()
    except Exception as err:
        logger.error(f"failed during token exchange: {err}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with LinkedIn OAuth server: {err}",
        )

    if "access_token" not in token_data:
        err_msg = token_data.get("error_description") or token_data.get("error") or response.text
        logger.warning(f"token exchange issue: {err_msg}")

        # if token already exists in database from a concurrent or previous request, redirect to dashboard
        existing_record = get_linkedin_token_record()
        if existing_record and existing_record.get("access_token"):
            return RedirectResponse(url="/")

        if error_template.exists():
            html_content = error_template.read_text(encoding="utf-8").replace("{{ERROR_MESSAGE}}", err_msg)
            return HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=html_content)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"LinkedIn token exchange failed: {err_msg}",
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    refresh_expires_in = token_data.get("refresh_token_expires_in")
    scope = token_data.get("scope")

    # fetch user profile information
    user_name = "LinkedIn User"
    person_urn = ""
    profile_id_str = None

    try:
        userinfo_resp = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code == 200:
            user_info = userinfo_resp.json()
            profile_id_str = json.dumps(user_info)
            user_name = user_info.get("name", user_name)
            sub = user_info.get("sub")
            if sub:
                person_urn = f"urn:li:person:{sub}"
    except Exception as err:
        logger.warning(f"failed to fetch userinfo in callback: {err}")

    # fallback to person urn resolver if openid sub is empty
    if not person_urn:
        try:
            person_urn = fetch_linkedin_person_urn(access_token=access_token)
        except Exception:
            person_urn = "urn:li:person:unknown"

    # save tokens and profile into persistent database
    save_linkedin_token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        profile_id=profile_id_str,
        person_urn=person_urn,
        scope=scope,
    )

    logger.info(f"successfully authenticated LinkedIn user: {user_name} ({person_urn})")

    callback_template = FRONTEND_DIR / "callback.html"
    if callback_template.exists():
        html = callback_template.read_text(encoding="utf-8")
        html = html.replace("{{USER_NAME}}", user_name)
        html = html.replace("{{PERSON_URN}}", person_urn)
        html = html.replace("{{EXPIRES_IN}}", str(expires_in or "N/A"))
        html = html.replace("{{REFRESH_STATUS}}", "Available (Auto-refresh on)" if refresh_token else "Standard Token")
        return HTMLResponse(content=html)

    return HTMLResponse(content=f"<h2>LinkedIn Connected! User: {user_name}</h2><a href='/'>Dashboard</a>")

# linkedin token metadata endpoint
@app.get("/linkedin/status")
def linkedin_status(request: Request) -> dict[str, Any]:
    record = get_linkedin_token_record()
    effective_uri = get_effective_redirect_uri(request)
    if not record:
        return {
            "authenticated": False,
            "redirect_uri": effective_uri,
            "message": "No LinkedIn token found. Please visit /linkedin/login to authenticate.",
        }

    access_token = record.get("access_token", "")
    masked_token = f"{access_token[:8]}...{access_token[-4:]}" if len(access_token) > 12 else "present"

    return {
        "authenticated": True,
        "token_preview": masked_token,
        "redirect_uri": effective_uri,
        "person_urn": record.get("person_urn"),
        "expires_at": str(record.get("expires_at")),
        "refresh_token_available": bool(record.get("refresh_token")),
        "scope": record.get("scope"),
        "updated_at": str(record.get("updated_at")),
    }

# manual or programmatic token refresh endpoint
@app.post("/linkedin/refresh")
def refresh_token_endpoint() -> dict[str, Any]:
    result = refresh_linkedin_access_token()
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Token refresh failed."),
        )
    return {"status": "refreshed", "message": "LinkedIn access token successfully refreshed."}

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
