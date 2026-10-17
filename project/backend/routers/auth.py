"""LinkedIn OAuth 2.0 authentication and token management router."""

import json
import logging
import secrets
import urllib.parse
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
import requests
from config.settings import (
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
)
from services.storage import (
    create_session,
    delete_session,
    get_linkedin_token_record,
    get_session_sub,
    refresh_linkedin_access_token,
    save_linkedin_token,
)
from services.linkedin_service import fetch_linkedin_person_urn
from utils import FRONTEND_DIR, get_effective_redirect_uri

logger = logging.getLogger("auth_router")

router = APIRouter(tags=["Authentication"])


def is_secure_connection(request: Request) -> bool:
    """Check if the incoming request uses HTTPS directly or via reverse proxy."""
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# linkedin oauth authorization redirect
@router.get("/linkedin/login")
def linkedin_login(request: Request) -> RedirectResponse:
    """Initiate the LinkedIn OAuth 2.0 authorization code flow.

    Purpose:
        Builds the LinkedIn OAuth authorization URL with state validation, sets
        a temporary secure state cookie, and redirects user to LinkedIn.
    """
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LINKEDIN_CLIENT_ID is not configured in environment.",
        )

    redirect_uri = get_effective_redirect_uri(request)
    oauth_state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": oauth_state,
        "scope": "openid profile w_member_social email",
    }
    auth_url = f"https://www.linkedin.com/oauth/v2/authorization?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="linkedin_oauth_state",
        value=oauth_state,
        httponly=True,
        secure=is_secure_connection(request),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


# linkedin oauth callback endpoint (supports both /linkedin/callback and legacy /callback)
@router.get("/linkedin/callback", response_class=HTMLResponse)
@router.get("/callback", response_class=HTMLResponse)
async def linkedin_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> Response:
    """Handle OAuth redirect callback from LinkedIn after user consent.

    Purpose:
        Validates the OAuth state cookie, exchanges the authorization code for
        access and refresh tokens, persists credentials, and renders the result.
    """
    error_template = FRONTEND_DIR / "error.html"

    # Validate CSRF state parameter against short-lived cookie
    cookie_state = request.cookies.get("linkedin_oauth_state")
    if not state or not cookie_state or state != cookie_state:
        logger.warning(f"OAuth state mismatch or missing cookie: state={state}, cookie={cookie_state}")
        err_msg = "Invalid or expired OAuth state. Please initiate login again."
        if error_template.exists():
            html_content = error_template.read_text(encoding="utf-8").replace("{{ERROR_MESSAGE}}", err_msg)
            resp = HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content=html_content)
        else:
            resp = HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content=f"<h2>Error: {err_msg}</h2>")
        resp.delete_cookie("linkedin_oauth_state", path="/")
        return resp

    if error:
        logger.error(f"LinkedIn OAuth error: {error} - {error_description}")
        err_msg = error_description or error
        if error_template.exists():
            html_content = error_template.read_text(encoding="utf-8").replace("{{ERROR_MESSAGE}}", err_msg)
            resp = HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=html_content)
        else:
            resp = HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=f"<h2>Error: {err_msg}</h2>")
        resp.delete_cookie("linkedin_oauth_state", path="/")
        return resp

    if not code:
        resp = HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content="<h2>Error: Authorization code missing</h2>")
        resp.delete_cookie("linkedin_oauth_state", path="/")
        return resp

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
            resp = RedirectResponse(url="/")
            resp.delete_cookie("linkedin_oauth_state", path="/")
            return resp

        if error_template.exists():
            html_content = error_template.read_text(encoding="utf-8").replace("{{ERROR_MESSAGE}}", err_msg)
            resp = HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=html_content)
        else:
            resp = HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content=f"<h2>Error: {err_msg}</h2>")
        resp.delete_cookie("linkedin_oauth_state", path="/")
        return resp

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

    # Create server-side session mapping session_id to user sub (person_urn)
    session_id = create_session(sub=person_urn)

    callback_template = FRONTEND_DIR / "callback.html"
    if callback_template.exists():
        html = callback_template.read_text(encoding="utf-8")
        html = html.replace("{{USER_NAME}}", user_name)
        html = html.replace("{{PERSON_URN}}", person_urn)
        html = html.replace("{{EXPIRES_IN}}", str(expires_in or "N/A"))
        html = html.replace("{{REFRESH_STATUS}}", "Available (Auto-refresh on)" if refresh_token else "Standard Token")
        resp = HTMLResponse(content=html)
    else:
        resp = HTMLResponse(content=f"<h2>LinkedIn Connected! User: {user_name}</h2><a href='/'>Dashboard</a>")

    resp.delete_cookie("linkedin_oauth_state", path="/")
    resp.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=is_secure_connection(request),
        samesite="lax",
        max_age=14 * 86400,
        path="/",
    )
    return resp


# logout endpoint to revoke session
@router.get("/linkedin/logout")
@router.post("/linkedin/logout")
def linkedin_logout(request: Request) -> Response:
    """Clear session and log out user."""
    session_id = request.cookies.get("session_id")
    if session_id:
        delete_session(session_id)
    resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("session_id", path="/")
    return resp


# linkedin token metadata endpoint
@router.get("/linkedin/status")
def linkedin_status(request: Request) -> dict[str, Any]:
    """Retrieve current LinkedIn authentication state and token metadata.

    Purpose:
        Provides the client with authentication status, masked token preview,
        person URN, expiration timestamp, and refresh token availability.

    Steps:
        1. Fetch stored token record from persistent database.
        2. Resolve effective callback URI for reference.
        3. If no record exists, return an unauthenticated status dictionary.
        4. Mask access token (showing first 8 and last 4 characters).
        5. Return dictionary of token properties and profile identifiers.

    Returns:
        dict[str, Any]: Authentication status and masked token metadata.

    Raises:
        None.
    """
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
@router.post("/linkedin/refresh")
def refresh_token_endpoint() -> dict[str, Any]:
    """Trigger programmatic or manual renewal of LinkedIn access token.

    Purpose:
        Uses the stored OAuth refresh token to request a new access token from
        LinkedIn without requiring interactive user login.

    Steps:
        1. Invoke refresh_linkedin_access_token() against stored credentials.
        2. Check result for success.
        3. If failed, raise HTTP 400 with failure explanation.
        4. Return success status dictionary.

    Returns:
        dict[str, Any]: Dictionary confirming successful token refresh.

    Raises:
        HTTPException: 400 Bad Request if token refresh fails.
    """
    result = refresh_linkedin_access_token()
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Token refresh failed."),
        )
    return {"status": "refreshed", "message": "LinkedIn access token successfully refreshed."}
