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
    SLACK_IGNORE_LINKEDIN_EMAIL,
)
from services.storage import (
    create_session,
    delete_session,
    get_linkedin_token_record,
    get_session_sub,
    get_valid_linkedin_access_token,
    refresh_linkedin_access_token,
    save_linkedin_token,
    set_slack_user_id,
)
from services.linkedin_service import fetch_linkedin_person_urn
from utils import FRONTEND_DIR, get_effective_redirect_uri

logger = logging.getLogger("auth_router")

router = APIRouter(tags=["Authentication"])


def get_current_user(request: Request) -> str:
    """FastAPI dependency to extract and validate the authenticated user sub.

    Purpose:
        Enforces session authentication via server-side session_id cookie and returns
        the user's sub or person URN.

    Steps:
        1. Extract session_id cookie from incoming request.
        2. Look up active user sub in database via get_session_sub.
        3. If session_id is missing, expired, or invalid, raise HTTP 401 Unauthorized.
        4. Return authenticated user sub.

    Returns:
        str: Authenticated user sub or person URN.

    Raises:
        HTTPException: 401 Unauthorized if session is missing, invalid, or expired.
    """
    session_id = request.cookies.get("session_id")
    if session_id:
        sub = get_session_sub(session_id)
        if sub:
            return sub
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
        )

    # Fallback to default user if default credentials are valid (single-user / tests)
    token = get_valid_linkedin_access_token(token_id="default")
    if token:
        record = get_linkedin_token_record(token_id="default")
        return (record.get("person_urn") if record else None) or "default"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Please log in via /linkedin/login.",
    )


def is_secure_connection(request: Request) -> bool:
    """Check if the incoming request uses HTTPS directly or via reverse proxy.

    Purpose:
        Determines if secure cookie transport flags should be enabled based on
        the direct scheme or X-Forwarded-Proto header.

    Steps:
        1. Inspect URL scheme on request object.
        2. Check X-Forwarded-Proto header for 'https' from reverse proxies.
        3. Return True if either is 'https', False otherwise.

    Returns:
        bool: True if connection is secure, False otherwise.

    Raises:
        None.
    """
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# linkedin oauth authorization redirect
@router.get("/linkedin/login")
def linkedin_login(request: Request) -> RedirectResponse:
    """Initiate the LinkedIn OAuth 2.0 authorization code flow.

    Purpose:
        Builds the LinkedIn OAuth authorization URL with state validation, sets
        a temporary secure state cookie, and redirects user to LinkedIn.

    Steps:
        1. Validate presence of LINKEDIN_CLIENT_ID in settings.
        2. Resolve effective callback URL using dynamic request headers.
        3. Generate 32-byte cryptographically secure random OAuth state string.
        4. Construct LinkedIn OAuth authorization URL with required scopes.
        5. Set short-lived HttpOnly linkedin_oauth_state cookie on response.
        6. Return 302 redirect response pointing to LinkedIn.

    Returns:
        RedirectResponse: 302 redirect to LinkedIn consent dialog.

    Raises:
        HTTPException: 500 Internal Server Error if client ID is unconfigured.
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

    Steps:
        1. Validate CSRF state query parameter matches linkedin_oauth_state cookie.
        2. Handle errors returned by LinkedIn consent screen.
        3. Exchange authorization code for access and refresh tokens.
        4. Query /v2/userinfo to resolve user name, profile data, and person URN.
        5. Persist credentials in storage under person URN and default token ID.
        6. Create server-side session and set secure session_id cookie.
        7. Clear temporary state cookie and return HTML response.

    Returns:
        Response: HTML response rendering success template or error page.

    Raises:
        None.
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
    user_email = None
    sub = None

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
            user_email = user_info.get("email")
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

    # save tokens and profile into persistent database under user's person_urn
    save_linkedin_token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        profile_id=profile_id_str,
        person_urn=person_urn,
        scope=scope,
        token_id=person_urn,
    )
    # Also save under default for backward compatibility with single-user / CLI mode
    save_linkedin_token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        profile_id=profile_id_str,
        person_urn=person_urn,
        scope=scope,
        token_id="default",
    )

    # Attempt to link user's Slack account by email in the shared Slack workspace (if not disabled by flag)
    if user_email and not SLACK_IGNORE_LINKEDIN_EMAIL:
        try:
            from services.slack import lookup_slack_user_id

            slack_uid = lookup_slack_user_id(user_email)
            if slack_uid:
                set_slack_user_id(sub=person_urn, slack_user_id=slack_uid)
                if sub:
                    set_slack_user_id(sub=sub, slack_user_id=slack_uid)
                set_slack_user_id(sub="default", slack_user_id=slack_uid)
                logger.info(f"Linked Slack user ID '{slack_uid}' for LinkedIn user '{person_urn}'.")
            else:
                logger.info(f"User email '{user_email}' not found in Slack workspace; Slack unlinked.")
        except Exception as slack_err:
            logger.warning(f"Failed to lookup or link Slack user during callback: {slack_err}")
    elif SLACK_IGNORE_LINKEDIN_EMAIL:
        logger.info("SLACK_IGNORE_LINKEDIN_EMAIL flag is active: skipped auto-linking Slack with LinkedIn email.")

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
    """Clear session and log out user.

    Purpose:
        Revokes the active user session from server-side storage and deletes
        the session cookie from the client browser.

    Steps:
        1. Extract session_id from request cookies.
        2. Delete session record from storage if present.
        3. Construct 302 redirect response pointing to root dashboard.
        4. Clear session_id cookie on response and return.

    Returns:
        Response: 302 redirect to root dashboard with deleted session cookie.

    Raises:
        None.
    """
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
        1. Extract session_id cookie and resolve active user person URN.
        2. Query stored token record for user sub, falling back to default record.
        3. If no record exists, return unauthenticated status dictionary.
        4. Check token validity and expiration via get_valid_linkedin_access_token.
        5. If token is expired and cannot refresh, return expired status dictionary.
        6. Mask access token (showing first 8 and last 4 characters).
        7. Return dictionary of token properties and profile identifiers.

    Returns:
        dict[str, Any]: Authentication status and masked token metadata.

    Raises:
        None.
    """
    effective_uri = get_effective_redirect_uri(request)
    session_id = request.cookies.get("session_id")
    user_sub = get_session_sub(session_id) if session_id else None

    # If the user has no valid session, return unauthenticated without exposing credentials or person URN
    if not user_sub:
        return {
            "authenticated": False,
            "expired": False,
            "redirect_uri": effective_uri,
            "message": "Not authenticated. Please visit /linkedin/login to authenticate.",
        }

    record = get_linkedin_token_record(token_id=user_sub)

    if not record or not record.get("access_token"):
        return {
            "authenticated": False,
            "expired": False,
            "redirect_uri": effective_uri,
            "message": "No LinkedIn token found. Please visit /linkedin/login to authenticate.",
        }

    token_id = record.get("id", user_sub)
    valid_token = get_valid_linkedin_access_token(token_id=token_id)
    if not valid_token:
        return {
            "authenticated": False,
            "expired": True,
            "redirect_uri": effective_uri,
            "person_urn": record.get("person_urn"),
            "expires_at": str(record.get("expires_at")),
            "message": "LinkedIn access token has expired. Please visit /linkedin/login to re-authenticate.",
        }

    access_token = valid_token
    masked_token = f"{access_token[:8]}...{access_token[-4:]}" if len(access_token) > 12 else "present"

    return {
        "authenticated": True,
        "expired": False,
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
def refresh_token_endpoint(request: Request) -> dict[str, Any]:
    """Trigger programmatic or manual renewal of LinkedIn access token.

    Purpose:
        Uses the stored OAuth refresh token to request a new access token from
        LinkedIn without requiring interactive user login.

    Steps:
        1. Extract session_id cookie and resolve active user person URN.
        2. Invoke refresh_linkedin_access_token() against user credentials.
        3. Check result for success.
        4. If failed, raise HTTP 400 with failure explanation.
        5. Return success status dictionary.

    Returns:
        dict[str, Any]: Dictionary confirming successful token refresh.

    Raises:
        HTTPException: 401 Unauthorized if user is not authenticated.
        HTTPException: 400 Bad Request if token refresh fails.
    """
    session_id = request.cookies.get("session_id")
    user_sub = get_session_sub(session_id) if session_id else None
    if not user_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to refresh token.",
        )

    result = refresh_linkedin_access_token(token_id=user_sub)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Token refresh failed."),
        )
    return {"status": "refreshed", "message": "LinkedIn access token successfully refreshed."}
