"""LinkedIn REST API service for author profile resolution and UGC post publication."""

import logging
import requests
from config.settings import LINKEDIN_PERSON_URN, get_linkedin_access_token

logger = logging.getLogger("linkedin_service")

# linkedin api endpoint constants
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_ME_URL = "https://api.linkedin.com/v2/me"
LINKEDIN_UGC_POST_URL = "https://api.linkedin.com/v2/ugcPosts"


def fetch_linkedin_person_urn(access_token: str | None = None) -> str:
    """Fetch LinkedIn person URN using active OAuth access token.

    Error Paradigm:
        RAISES EXCEPTIONS. Raises ValueError if token is absent or RuntimeError
        if both userinfo and /v2/me endpoints fail to resolve member identity.

    Purpose:
        Resolves author person URN (e.g., 'urn:li:person:abcdef') required for authoring posts.

    Steps:
        1. Retrieve access token from parameter or token storage fallback.
        2. If access token is missing, raise ValueError.
        3. Check LINKEDIN_PERSON_URN config override; return if present.
        4. Query OpenID /v2/userinfo endpoint for 'sub' field.
        5. If userinfo endpoint fails or lacks sub, query legacy /v2/me endpoint for 'id'.
        6. Return formatted author URN string or raise RuntimeError if resolution fails.

    Returns:
        str: LinkedIn person URN formatted as 'urn:li:person:<id>'.

    Raises:
        ValueError: If LinkedIn access token is missing.
        RuntimeError: If all LinkedIn profile endpoints fail.
    """
    token = access_token or get_linkedin_access_token()
    if not token:
        raise ValueError("LinkedIn access token is missing. Please authenticate first.")

    if LINKEDIN_PERSON_URN:
        return LINKEDIN_PERSON_URN.strip()

    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(LINKEDIN_USERINFO_URL, headers=headers, timeout=10)
        if response.status_code == 200:
            payload = response.json()
            sub_id = payload.get("sub")
            if sub_id:
                return f"urn:li:person:{sub_id}"
    except requests.RequestException as err:
        logger.warning(f"failed to fetch person urn from userinfo endpoint: {err}")

    try:
        response = requests.get(LINKEDIN_ME_URL, headers=headers, timeout=10)
        if response.status_code == 200:
            payload = response.json()
            member_id = payload.get("id")
            if member_id:
                return f"urn:li:person:{member_id}"
    except requests.RequestException as err:
        logger.warning(f"failed to fetch member id from /v2/me endpoint: {err}")

    raise RuntimeError("unable to resolve LinkedIn Person URN using provided access token")


def publish_post_to_linkedin(
    content: str,
    access_token: str | None = None,
    person_urn: str | None = None,
) -> dict:
    """Publish text commentary to LinkedIn member profile feed.

    Error Paradigm:
        RETURNS RESULT DICT. This function adheres strictly to returning result
        dictionaries (`{'success': False, 'status': 'failed', 'error': ...}`)
        for all HTTP status errors, API response failures, and network timeouts.
        It does not raise exceptions for API or communication failures.

    Purpose:
        Submits formatted UGC post payload to LinkedIn ugcPosts REST endpoint.

    Steps:
        1. Resolve access token; raise ValueError if no token exists.
        2. Resolve author person URN via parameter or fetch_linkedin_person_urn.
        3. Build UGC post JSON payload with PUBLIC visibility and NONE media category.
        4. Send HTTP POST request to LINKEDIN_UGC_POST_URL with 15-second timeout.
        5. If HTTP 200 or 201, parse post URN from response and return success dict.
        6. If non-200 HTTP status, log error and return structured failure dict.
        7. If requests.RequestException occurs, catch and return network failure dict.

    Returns:
        dict: Status dictionary containing success flag, post_urn, author, or error details.

    Raises:
        ValueError: If access token is completely missing prior to submission.
    """
    # retrieve access token from argument or configuration
    token = access_token or get_linkedin_access_token()
    if not token:
        raise ValueError("LinkedIn access token is missing. Cannot publish post.")

    # determine author urn using explicit parameter or dynamic lookup
    author_urn = person_urn or fetch_linkedin_person_urn(token)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    payload = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": content},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    try:
        # submit post payload to linkedin ugc posts endpoint
        response = requests.post(
            LINKEDIN_UGC_POST_URL,
            headers=headers,
            json=payload,
            timeout=15,
        )

        # handle successful response
        if response.status_code in (200, 201):
            response_json = response.json() if response.text else {}
            post_urn = response_json.get("id") or response.headers.get("x-restli-id", "")
            return {
                "success": True,
                "status": "published",
                "post_urn": post_urn,
                "author": author_urn,
                "response": response_json,
            }

        # handle api error response
        logger.error(f"LinkedIn publishing failed ({response.status_code}): {response.text}")
        return {
            "success": False,
            "status": "failed",
            "status_code": response.status_code,
            "error": response.text,
        }

    except requests.RequestException as err:
        # handle network and connection errors
        logger.error(f"network error while publishing post to LinkedIn: {err}")
        return {
            "success": False,
            "status": "failed",
            "error": str(err),
        }
