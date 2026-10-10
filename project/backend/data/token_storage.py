import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import requests
from config.settings import (
    BASE_DIR,
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
)
from data.db import (
    get_postgres_connection,
    get_sqlite_connection,
    init_database_tables,
)

# configure token storage logger
logger = logging.getLogger("token_storage")

# token endpoint constants
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# initialize token storage database tables
def init_token_db() -> None:
    init_database_tables()

# save or update linkedin tokens in persistent storage
def save_linkedin_token(
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int | None = None,
    refresh_expires_in: int | None = None,
    profile_id: str | None = None,
    person_urn: str | None = None,
    scope: str | None = None,
    token_id: str = "default",
) -> dict[str, Any]:
    init_token_db()
    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + timedelta(seconds=expires_in)) if expires_in else None
    refresh_expires_at = (now_utc + timedelta(seconds=refresh_expires_in)) if refresh_expires_in else None

    # update postgres if available
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO linkedin_tokens (
                            id, access_token, refresh_token, expires_at,
                            refresh_expires_at, profile_id, person_urn, scope, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            access_token = EXCLUDED.access_token,
                            refresh_token = COALESCE(EXCLUDED.refresh_token, linkedin_tokens.refresh_token),
                            expires_at = COALESCE(EXCLUDED.expires_at, linkedin_tokens.expires_at),
                            refresh_expires_at = COALESCE(EXCLUDED.refresh_expires_at, linkedin_tokens.refresh_expires_at),
                            profile_id = COALESCE(EXCLUDED.profile_id, linkedin_tokens.profile_id),
                            person_urn = COALESCE(EXCLUDED.person_urn, linkedin_tokens.person_urn),
                            scope = COALESCE(EXCLUDED.scope, linkedin_tokens.scope),
                            updated_at = EXCLUDED.updated_at;
                        """,
                        (
                            token_id,
                            access_token,
                            refresh_token,
                            expires_at,
                            refresh_expires_at,
                            profile_id,
                            person_urn,
                            scope,
                            now_utc,
                        ),
                    )
            return {
                "success": True,
                "token_id": token_id,
                "person_urn": person_urn,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        except Exception as err:
            logger.warning(f"failed to save token to postgres, saving to sqlite fallback: {err}")

    # fallback to sqlite storage
    with get_sqlite_connection() as sqlite_conn:
        sqlite_conn.execute(
            """
            INSERT INTO linkedin_tokens (
                id, access_token, refresh_token, expires_at,
                refresh_expires_at, profile_id, person_urn, scope, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = COALESCE(excluded.refresh_token, linkedin_tokens.refresh_token),
                expires_at = COALESCE(excluded.expires_at, linkedin_tokens.expires_at),
                refresh_expires_at = COALESCE(excluded.refresh_expires_at, linkedin_tokens.refresh_expires_at),
                profile_id = COALESCE(excluded.profile_id, linkedin_tokens.profile_id),
                person_urn = COALESCE(excluded.person_urn, linkedin_tokens.person_urn),
                scope = COALESCE(excluded.scope, linkedin_tokens.scope),
                updated_at = excluded.updated_at;
            """,
            (
                token_id,
                access_token,
                refresh_token,
                expires_at.isoformat() if expires_at else None,
                refresh_expires_at.isoformat() if refresh_expires_at else None,
                profile_id,
                person_urn,
                scope,
                now_utc.isoformat(),
            ),
        )
        sqlite_conn.commit()

    # save local backup file
    token_backup_file = BASE_DIR / ".linkedin_token"
    try:
        token_backup_file.write_text(access_token, encoding="utf-8")
    except OSError:
        pass

    return {
        "success": True,
        "token_id": token_id,
        "person_urn": person_urn,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }

# retrieve stored token record from persistent storage
def get_linkedin_token_record(token_id: str = "default") -> dict[str, Any] | None:
    init_token_db()
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, access_token, refresh_token, expires_at,
                               refresh_expires_at, profile_id, person_urn, scope, updated_at
                        FROM linkedin_tokens
                        WHERE id = %s;
                        """,
                        (token_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return {
                            "id": row[0],
                            "access_token": row[1],
                            "refresh_token": row[2],
                            "expires_at": row[3],
                            "refresh_expires_at": row[4],
                            "profile_id": row[5],
                            "person_urn": row[6],
                            "scope": row[7],
                            "updated_at": row[8],
                        }
        except Exception as err:
            logger.warning(f"failed to load token from postgres: {err}")

    # check sqlite
    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.cursor()
            cur.execute(
                """
                SELECT id, access_token, refresh_token, expires_at,
                       refresh_expires_at, profile_id, person_urn, scope, updated_at
                FROM linkedin_tokens
                WHERE id = ?;
                """,
                (token_id,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "access_token": row[1],
                    "refresh_token": row[2],
                    "expires_at": row[3],
                    "refresh_expires_at": row[4],
                    "profile_id": row[5],
                    "person_urn": row[6],
                    "scope": row[7],
                    "updated_at": row[8],
                }
    except Exception as err:
        logger.warning(f"failed to load token from sqlite: {err}")

    # check environment variable fallback
    env_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if env_token:
        return {
            "id": token_id,
            "access_token": env_token.strip(),
            "refresh_token": None,
            "expires_at": None,
            "profile_id": os.getenv("LINKEDIN_PROFILE_ID"),
            "person_urn": os.getenv("LINKEDIN_PERSON_URN"),
            "scope": None,
            "updated_at": None,
        }

    # check local file fallback
    token_file = BASE_DIR / ".linkedin_token"
    if not token_file.exists() and (BASE_DIR.parent.parent / ".linkedin_token").exists():
        token_file = BASE_DIR.parent.parent / ".linkedin_token"
    if token_file.exists():
        file_token = token_file.read_text(encoding="utf-8").strip()
        if file_token:
            return {
                "id": token_id,
                "access_token": file_token,
                "refresh_token": None,
                "expires_at": None,
                "profile_id": os.getenv("LINKEDIN_PROFILE_ID"),
                "person_urn": os.getenv("LINKEDIN_PERSON_URN"),
                "scope": None,
                "updated_at": None,
            }

    return None

# exchange refresh token for a new access token
def refresh_linkedin_access_token(token_id: str = "default") -> dict[str, Any]:
    record = get_linkedin_token_record(token_id=token_id)
    if not record or not record.get("refresh_token"):
        return {
            "success": False,
            "error": "No refresh token available in storage. Please re-authenticate via /linkedin/login.",
        }

    client_id = LINKEDIN_CLIENT_ID
    client_secret = LINKEDIN_CLIENT_SECRET
    if not client_id or not client_secret:
        return {
            "success": False,
            "error": "LINKEDIN_CLIENT_ID or LINKEDIN_CLIENT_SECRET is missing.",
        }

    refresh_payload = {
        "grant_type": "refresh_token",
        "refresh_token": record["refresh_token"],
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        response = requests.post(
            LINKEDIN_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=refresh_payload,
            timeout=15,
        )
        data = response.json()
    except Exception as err:
        logger.error(f"failed to refresh token: {err}")
        return {"success": False, "error": f"Network error: {err}"}

    if "access_token" not in data:
        error_detail = data.get("error_description") or data.get("error") or response.text
        logger.error(f"linkedin token refresh rejected: {error_detail}")
        return {"success": False, "error": f"Token refresh failed: {error_detail}"}

    new_access_token = data["access_token"]
    expires_in = data.get("expires_in")
    new_refresh_token = data.get("refresh_token") or record["refresh_token"]
    refresh_expires_in = data.get("refresh_token_expires_in")

    save_result = save_linkedin_token(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        profile_id=record.get("profile_id"),
        person_urn=record.get("person_urn"),
        scope=data.get("scope") or record.get("scope"),
        token_id=token_id,
    )
    logger.info("successfully refreshed LinkedIn access token")
    return {"success": True, "token": new_access_token, "details": save_result}

# get valid access token, auto-refreshing if expired or nearing expiration
def get_valid_linkedin_access_token(token_id: str = "default") -> str | None:
    record = get_linkedin_token_record(token_id=token_id)
    if not record:
        return None

    access_token = record.get("access_token")
    expires_at = record.get("expires_at")
    refresh_token = record.get("refresh_token")

    # verify expiration window
    if expires_at and refresh_token:
        try:
            if isinstance(expires_at, str):
                exp_dt = datetime.fromisoformat(expires_at)
            else:
                exp_dt = expires_at

            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            # auto-refresh if token expires in less than 24 hours
            if datetime.now(timezone.utc) + timedelta(hours=24) >= exp_dt:
                logger.info("LinkedIn token nearing expiration or expired. Triggering auto-refresh...")
                refresh_res = refresh_linkedin_access_token(token_id=token_id)
                if refresh_res.get("success"):
                    return refresh_res.get("token")
        except Exception as err:
            logger.warning(f"error checking token expiration date: {err}")

    return access_token

# retrieve stored person urn
def get_stored_person_urn(token_id: str = "default") -> str | None:
    record = get_linkedin_token_record(token_id=token_id)
    if record and record.get("person_urn"):
        return record["person_urn"]
    return None
