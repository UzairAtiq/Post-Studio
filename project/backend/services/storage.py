"""Centralized storage service managing PostgreSQL, SQLite, and JSON fallback backends."""

import json
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
import requests

from config.constants import PostStatus
from config.settings import (
    BASE_DIR,
    DATABASE_URL,
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
    POSTS_STORAGE_PATH,
)

logger = logging.getLogger("storage_service")

# Database paths and endpoints
SQLITE_DB_PATH = BASE_DIR / "data" / "app.db"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

# Runtime tracker for PostgreSQL availability
_postgres_available = True if DATABASE_URL else False


def ensure_sqlite_directory() -> Path:
    """Ensure directory for SQLite database file exists.

    Purpose:
        Creates parent directories for SQLite database storage if absent.

    Steps:
        1. Resolve parent directory of SQLite database path.
        2. Create directories with mkdir(parents=True, exist_ok=True).
        3. Return path to SQLite database.

    Returns:
        Path: Path to SQLite database file.

    Raises:
        OSError: If directory creation fails.
    """
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return SQLITE_DB_PATH


def _ensure_json_file() -> Path:
    """Ensure parent directory and initial file for JSON storage fallback exist.

    Purpose:
        Creates storage directory and empty JSON object file if not present.

    Steps:
        1. Resolve configured posts JSON storage path.
        2. Create parent directory if absent.
        3. Initialize file with empty JSON object '{}' if file does not exist.
        4. Return resolved Path.

    Returns:
        Path: Path to posts JSON storage file.

    Raises:
        OSError: If filesystem operations fail.
    """
    storage_path = Path(POSTS_STORAGE_PATH)
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    if not storage_path.exists():
        storage_path.write_text("{}", encoding="utf-8")
    return storage_path


def _load_posts_from_json() -> dict[str, dict[str, Any]]:
    """Load all saved post records from local JSON file fallback.

    Purpose:
        Reads and parses post records from filesystem when relational databases fail.

    Steps:
        1. Resolve JSON storage file path.
        2. Read UTF-8 encoded text content.
        3. Parse JSON into Python dictionary or return empty dict on error.

    Returns:
        dict[str, dict[str, Any]]: Dictionary mapping post_id to post records.

    Raises:
        None: Suppresses and logs decode/IO errors, returning empty dict.
    """
    json_path = _ensure_json_file()
    try:
        content = json_path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_posts_to_json(posts: dict[str, dict[str, Any]]) -> None:
    """Persist posts dictionary to local JSON file fallback.

    Purpose:
        Writes updated post records to filesystem JSON file.

    Steps:
        1. Resolve JSON storage file path.
        2. Serialize dictionary to formatted JSON string.
        3. Write text to file using UTF-8 encoding.

    Returns:
        None.

    Raises:
        OSError: If file writing fails.
    """
    json_path = _ensure_json_file()
    json_path.write_text(json.dumps(posts, indent=2), encoding="utf-8")


def get_postgres_connection() -> psycopg.Connection | None:
    """Attempt connecting to PostgreSQL database with graceful fallback.

    Purpose:
        Provides an active PostgreSQL connection if reachable, otherwise marks
        PostgreSQL unavailable and returns None.

    Steps:
        1. Check if PostgreSQL was previously marked unavailable or DATABASE_URL is empty.
        2. Attempt psycopg connection with a 3-second timeout.
        3. If connection succeeds, return connection object.
        4. If connection fails, log warning, set _postgres_available to False, and return None.

    Returns:
        psycopg.Connection | None: Active PostgreSQL connection, or None if unavailable.

    Raises:
        None.
    """
    global _postgres_available
    if not _postgres_available or not DATABASE_URL:
        return None
    try:
        return psycopg.connect(DATABASE_URL, connect_timeout=3)
    except Exception as err:
        logger.warning(f"PostgreSQL connection unavailable ({err}). Using SQLite fallback.")
        _postgres_available = False
        return None


def get_sqlite_connection() -> sqlite3.Connection:
    """Create and return a connection to local SQLite database.

    Purpose:
        Provides connection to local SQLite database as fallback or primary store.

    Steps:
        1. Ensure directory exists for SQLite database file.
        2. Open connection using sqlite3.connect.
        3. Return connection object.

    Returns:
        sqlite3.Connection: Active SQLite connection.

    Raises:
        sqlite3.Error: If database connection fails.
    """
    ensure_sqlite_directory()
    return sqlite3.connect(SQLITE_DB_PATH)


def is_postgres_active() -> bool:
    """Check whether PostgreSQL backend is active and configured.

    Purpose:
        Informs callers whether PostgreSQL is current primary backend.

    Steps:
        1. Return boolean evaluation of availability flag and DATABASE_URL presence.

    Returns:
        bool: True if PostgreSQL is reachable and configured, False otherwise.

    Raises:
        None.
    """
    return _postgres_available and bool(DATABASE_URL)


def init_database_tables() -> None:
    """Initialize relational database tables across available backends.

    Purpose:
        Ensures linkedin_tokens and posts tables exist in PostgreSQL or SQLite.

    Steps:
        1. Attempt connection to PostgreSQL.
        2. If PostgreSQL is available, execute CREATE TABLE IF NOT EXISTS for tokens and posts.
        3. Fall back to SQLite and execute CREATE TABLE IF NOT EXISTS for tokens and posts.

    Returns:
        None.

    Raises:
        None.
    """
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS linkedin_tokens (
                            id VARCHAR(50) PRIMARY KEY,
                            access_token TEXT NOT NULL,
                            refresh_token TEXT,
                            expires_at TIMESTAMP WITH TIME ZONE,
                            refresh_expires_at TIMESTAMP WITH TIME ZONE,
                            profile_id TEXT,
                            person_urn TEXT,
                            scope TEXT,
                            updated_at TIMESTAMP WITH TIME ZONE
                        );
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS posts (
                            post_id VARCHAR(100) PRIMARY KEY,
                            content TEXT NOT NULL,
                            profile_id TEXT,
                            access_token TEXT,
                            status VARCHAR(50) NOT NULL,
                            created_at TIMESTAMP WITH TIME ZONE,
                            updated_at TIMESTAMP WITH TIME ZONE,
                            linkedin_response TEXT,
                            response_url TEXT,
                            decision_action VARCHAR(50)
                        );
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS sessions (
                            session_id VARCHAR(100) PRIMARY KEY,
                            sub VARCHAR(100) NOT NULL,
                            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                            expires_at TIMESTAMP WITH TIME ZONE NOT NULL
                        );
                        """
                    )
            return
        except Exception as err:
            logger.warning(f"Failed to initialize PostgreSQL tables: {err}. Falling back to SQLite.")

    # Fallback to SQLite
    with get_sqlite_connection() as sqlite_conn:
        sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS linkedin_tokens (
                id TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at TEXT,
                refresh_expires_at TEXT,
                profile_id TEXT,
                person_urn TEXT,
                scope TEXT,
                updated_at TEXT
            );
            """
        )
        sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                profile_id TEXT,
                access_token TEXT,
                status TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                linkedin_response TEXT,
                response_url TEXT,
                decision_action TEXT
            );
            """
        )
        sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                sub TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        sqlite_conn.commit()


def migrate_legacy_rejected_status() -> None:
    """Migrate legacy 'rejected' status values to canonical 'discarded' across storage tiers.

    Purpose:
        Ensures existing database rows and JSON records with status 'rejected' are
        updated to the unified status 'discarded'.

    Steps:
        1. Attempt updating PostgreSQL posts table rows where status = 'rejected'.
        2. Attempt updating SQLite posts table rows where status = 'rejected'.
        3. Scan JSON fallback file and convert any 'rejected' statuses to 'discarded'.

    Returns:
        None.

    Raises:
        None.
    """
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE posts SET status = 'discarded' WHERE status = 'rejected';")
        except Exception as err:
            logger.warning(f"PostgreSQL status migration notice: {err}")

    try:
        with get_sqlite_connection() as sqlite_conn:
            sqlite_conn.execute("UPDATE posts SET status = 'discarded' WHERE status = 'rejected';")
            sqlite_conn.commit()
    except Exception as err:
        logger.warning(f"SQLite status migration notice: {err}")

    json_posts = _load_posts_from_json()
    changed = False
    for p in json_posts.values():
        if p.get("status") == "rejected":
            p["status"] = "discarded"
            changed = True
    if changed:
        _save_posts_to_json(json_posts)


def init_storage() -> None:
    """Initialize storage tables, directories, and data migrations across all tiers.

    Purpose:
        Initializes schema tables, ensures fallback storage files exist, and applies
        status normalization migrations.

    Steps:
        1. Run init_database_tables to prepare relational tables.
        2. Ensure JSON storage directory and file exist.
        3. Run migrate_legacy_rejected_status to unify rejection statuses to 'discarded'.

    Returns:
        None.

    Raises:
        None.
    """
    init_database_tables()
    _ensure_json_file()
    migrate_legacy_rejected_status()


# Compatibility aliases
init_posts_db = init_storage
init_token_db = init_storage


# ---------------------------------------------------------------------------
# Post Operations
# ---------------------------------------------------------------------------

def generate_next_post_id() -> str:
    """Generate a unique sequential post identifier.

    Purpose:
        Produces a unique post ID prefixed by count and random hex suffix.

    Steps:
        1. Retrieve current post records count.
        2. Generate 6-character random hex string from UUID4.
        3. Format string as 'post_{count}_{short_uuid}'.

    Returns:
        str: Formatted unique post identifier.

    Raises:
        None.
    """
    posts = list_posts()
    count = len(posts) + 1
    short_uuid = uuid.uuid4().hex[:6]
    return f"post_{count}_{short_uuid}"


def save_post_record(
    post_id: str,
    content: str,
    profile_id: str,
    access_token: str,
    status: str | PostStatus = PostStatus.PENDING,
) -> dict[str, Any]:
    """Persist a new post record across available storage backends with fallback.

    Purpose:
        Saves post record into PostgreSQL, falling back to SQLite, then JSON file.

    Steps:
        1. Initialize storage tables if needed.
        2. Construct post record dictionary with ISO timestamps.
        3. Attempt upsert into PostgreSQL posts table.
        4. On failure, attempt upsert into SQLite posts table.
        5. On failure, write record into fallback JSON storage file.
        6. Return saved record dictionary.

    Returns:
        dict[str, Any]: The persisted post record dictionary.

    Raises:
        None.
    """
    init_storage()
    now_iso = datetime.now(timezone.utc).isoformat()
    status_str = status.value if isinstance(status, PostStatus) else str(status)

    record = {
        "post_id": post_id,
        "content": content,
        "profile_id": profile_id,
        "access_token": access_token,
        "status": status_str,
        "created_at": now_iso,
        "updated_at": now_iso,
        "linkedin_response": None,
        "response_url": None,
        "decision_action": None,
    }

    # Tier 1: PostgreSQL
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO posts (
                            post_id, content, profile_id, access_token, status,
                            created_at, updated_at, linkedin_response, response_url, decision_action
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (post_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            profile_id = EXCLUDED.profile_id,
                            access_token = EXCLUDED.access_token,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at,
                            linkedin_response = EXCLUDED.linkedin_response,
                            response_url = EXCLUDED.response_url,
                            decision_action = EXCLUDED.decision_action;
                        """,
                        (
                            post_id,
                            content,
                            profile_id,
                            access_token,
                            status_str,
                            now_iso,
                            now_iso,
                            None,
                            None,
                            None,
                        ),
                    )
            return record
        except Exception as err:
            logger.warning(f"failed to save post to postgres, falling back to sqlite: {err}")

    # Tier 2: SQLite
    try:
        with get_sqlite_connection() as sqlite_conn:
            sqlite_conn.execute(
                """
                INSERT INTO posts (
                    post_id, content, profile_id, access_token, status,
                    created_at, updated_at, linkedin_response, response_url, decision_action
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    content = excluded.content,
                    profile_id = excluded.profile_id,
                    access_token = excluded.access_token,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    linkedin_response = excluded.linkedin_response,
                    response_url = excluded.response_url,
                    decision_action = excluded.decision_action;
                """,
                (
                    post_id,
                    content,
                    profile_id,
                    access_token,
                    status_str,
                    now_iso,
                    now_iso,
                    None,
                    None,
                    None,
                ),
            )
            sqlite_conn.commit()
        return record
    except Exception as err:
        logger.warning(f"failed to save post to sqlite, saving to json file: {err}")

    # Tier 3: JSON File fallback
    json_posts = _load_posts_from_json()
    json_posts[post_id] = record
    _save_posts_to_json(json_posts)
    return record


def get_post_by_id(post_id: str) -> dict[str, Any] | None:
    """Retrieve single post record by unique post identifier.

    Purpose:
        Queries PostgreSQL, then SQLite, then JSON file for matching post record.

    Steps:
        1. Initialize storage tables if needed.
        2. Query PostgreSQL if reachable. Parse linkedin_response if found.
        3. Query SQLite if PostgreSQL unavailable or misses.
        4. Query JSON file fallback if relational databases miss.
        5. Return record dictionary or None if not found.

    Returns:
        dict[str, Any] | None: Post record dictionary, or None if not found.

    Raises:
        None.
    """
    init_storage()
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT post_id, content, profile_id, access_token, status,
                               created_at, updated_at, linkedin_response, response_url, decision_action
                        FROM posts
                        WHERE post_id = %s;
                        """,
                        (post_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        parsed_response = json.loads(row[7]) if row[7] else None
                        return {
                            "post_id": row[0],
                            "content": row[1],
                            "profile_id": row[2],
                            "access_token": row[3],
                            "status": row[4],
                            "created_at": row[5],
                            "updated_at": row[6],
                            "linkedin_response": parsed_response,
                            "response_url": row[8],
                            "decision_action": row[9],
                        }
        except Exception as err:
            logger.warning(f"failed to fetch post from postgres: {err}")

    # SQLite check
    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.cursor()
            cur.execute(
                """
                SELECT post_id, content, profile_id, access_token, status,
                       created_at, updated_at, linkedin_response, response_url, decision_action
                FROM posts
                WHERE post_id = ?;
                """,
                (post_id,),
            )
            row = cur.fetchone()
            if row:
                parsed_response = json.loads(row[7]) if row[7] else None
                return {
                    "post_id": row[0],
                    "content": row[1],
                    "profile_id": row[2],
                    "access_token": row[3],
                    "status": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                    "linkedin_response": parsed_response,
                    "response_url": row[8],
                    "decision_action": row[9],
                }
    except Exception as err:
        logger.warning(f"failed to fetch post from sqlite: {err}")

    # JSON fallback check
    json_posts = _load_posts_from_json()
    return json_posts.get(post_id)


def update_post_status(
    post_id: str,
    status: str | PostStatus,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update post status and optional metadata across active storage backends.

    Purpose:
        Modifies post status, updated_at timestamp, and optional extra metadata.

    Steps:
        1. Look up existing post record by post_id. Return None if not found.
        2. Update status and updated_at in in-memory record.
        3. Merge extra_data attributes (linkedin_response, response_url, decision_action).
        4. Persist update to PostgreSQL if reachable.
        5. Persist update to SQLite fallback.
        6. Persist update to JSON file fallback.
        7. Return updated record dictionary.

    Returns:
        dict[str, Any] | None: Updated post dictionary, or None if post does not exist.

    Raises:
        None.
    """
    init_storage()
    record = get_post_by_id(post_id)
    if not record:
        return None

    status_str = status.value if isinstance(status, PostStatus) else str(status)
    now_iso = datetime.now(timezone.utc).isoformat()
    record["status"] = status_str
    record["updated_at"] = now_iso

    if extra_data:
        for key, val in extra_data.items():
            record[key] = val

    linkedin_resp_str = json.dumps(record.get("linkedin_response")) if record.get("linkedin_response") else None
    response_url = record.get("response_url")
    decision_action = record.get("decision_action")

    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE posts
                        SET status = %s,
                            updated_at = %s,
                            linkedin_response = %s,
                            response_url = COALESCE(%s, response_url),
                            decision_action = COALESCE(%s, decision_action)
                        WHERE post_id = %s;
                        """,
                        (status_str, now_iso, linkedin_resp_str, response_url, decision_action, post_id),
                    )
            return record
        except Exception as err:
            logger.warning(f"failed to update post in postgres: {err}")

    # SQLite update
    try:
        with get_sqlite_connection() as sqlite_conn:
            sqlite_conn.execute(
                """
                UPDATE posts
                SET status = ?,
                    updated_at = ?,
                    linkedin_response = ?,
                    response_url = COALESCE(?, response_url),
                    decision_action = COALESCE(?, decision_action)
                WHERE post_id = ?;
                """,
                (status_str, now_iso, linkedin_resp_str, response_url, decision_action, post_id),
            )
            sqlite_conn.commit()
        return record
    except Exception as err:
        logger.warning(f"failed to update post in sqlite: {err}")

    # JSON fallback
    json_posts = _load_posts_from_json()
    json_posts[post_id] = record
    _save_posts_to_json(json_posts)
    return record


def list_posts() -> list[dict[str, Any]]:
    """List all stored post records sorted by creation date descending.

    Purpose:
        Retrieves all post records across PostgreSQL, SQLite, or JSON fallback.

    Steps:
        1. Initialize storage tables if needed.
        2. Attempt querying all rows from PostgreSQL ordered by created_at DESC.
        3. If unavailable, query SQLite rows ordered by created_at DESC.
        4. If unavailable, load all records from JSON file fallback.
        5. Return list of post dictionaries.

    Returns:
        list[dict[str, Any]]: List of post records.

    Raises:
        None.
    """
    init_storage()
    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT post_id, content, profile_id, access_token, status,
                               created_at, updated_at, linkedin_response, response_url, decision_action
                        FROM posts
                        ORDER BY created_at DESC;
                        """
                    )
                    rows = cur.fetchall()
                    if rows:
                        posts = []
                        for row in rows:
                            parsed_response = json.loads(row[7]) if row[7] else None
                            posts.append(
                                {
                                    "post_id": row[0],
                                    "content": row[1],
                                    "profile_id": row[2],
                                    "access_token": row[3],
                                    "status": row[4],
                                    "created_at": row[5],
                                    "updated_at": row[6],
                                    "linkedin_response": parsed_response,
                                    "response_url": row[8],
                                    "decision_action": row[9],
                                }
                            )
                        return posts
        except Exception as err:
            logger.warning(f"failed to list posts from postgres: {err}")

    # SQLite query
    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.cursor()
            cur.execute(
                """
                SELECT post_id, content, profile_id, access_token, status,
                       created_at, updated_at, linkedin_response, response_url, decision_action
                FROM posts
                ORDER BY created_at DESC;
                """
            )
            rows = cur.fetchall()
            if rows:
                posts = []
                for row in rows:
                    parsed_response = json.loads(row[7]) if row[7] else None
                    posts.append(
                        {
                            "post_id": row[0],
                            "content": row[1],
                            "profile_id": row[2],
                            "access_token": row[3],
                            "status": row[4],
                            "created_at": row[5],
                            "updated_at": row[6],
                            "linkedin_response": parsed_response,
                            "response_url": row[8],
                            "decision_action": row[9],
                        }
                    )
                return posts
    except Exception as err:
        logger.warning(f"failed to list posts from sqlite: {err}")

    # JSON fallback
    json_posts = _load_posts_from_json()
    return list(json_posts.values())


def delete_post_record(post_id: str) -> bool:
    """Delete a post record by its unique post identifier.

    Purpose:
        Removes post record across PostgreSQL, SQLite, and JSON fallback stores.

    Steps:
        1. Initialize storage tables if needed.
        2. Attempt deleting matching row from PostgreSQL posts table.
        3. Attempt deleting matching row from SQLite posts table.
        4. Remove post_id from JSON storage dictionary if present.
        5. Return True if deleted in any backend, False otherwise.

    Returns:
        bool: True if post record was deleted, False if not found.

    Raises:
        None.
    """
    init_storage()
    deleted = False

    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM posts WHERE post_id = %s;", (post_id,))
                    if cur.rowcount > 0:
                        deleted = True
        except Exception as err:
            logger.warning(f"failed to delete post from postgres: {err}")

    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.cursor()
            cur.execute("DELETE FROM posts WHERE post_id = ?;", (post_id,))
            if cur.rowcount > 0:
                deleted = True
            sqlite_conn.commit()
    except Exception as err:
        logger.warning(f"failed to delete post from sqlite: {err}")

    json_posts = _load_posts_from_json()
    if post_id in json_posts:
        del json_posts[post_id]
        _save_posts_to_json(json_posts)
        deleted = True

    return deleted


# ---------------------------------------------------------------------------
# Token Operations
# ---------------------------------------------------------------------------

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
    """Save or update LinkedIn credentials across active storage backends.

    Purpose:
        Persists LinkedIn OAuth tokens, expiry timestamps, and profile metadata.

    Steps:
        1. Initialize storage tables if needed.
        2. Calculate absolute expiry timestamps from UTC now + offsets.
        3. Attempt upsert into PostgreSQL linkedin_tokens table.
        4. If PostgreSQL fails or is inactive, upsert into SQLite linkedin_tokens table.
        5. Write access token backup file to local disk for offline resilience.
        6. Return confirmation dictionary with token details.

    Returns:
        dict[str, Any]: Summary dictionary with success flag, token_id, and expiry.

    Raises:
        None.
    """
    init_storage()
    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + timedelta(seconds=expires_in)) if expires_in else None
    refresh_expires_at = (now_utc + timedelta(seconds=refresh_expires_in)) if refresh_expires_in else None

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

    # SQLite fallback
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

    # Save local backup file
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


def get_linkedin_token_record(token_id: str = "default") -> dict[str, Any] | None:
    """Retrieve stored LinkedIn token record across storage tiers.

    Purpose:
        Fetches credentials from PostgreSQL, SQLite, environment, or backup file.

    Steps:
        1. Initialize storage tables if needed.
        2. Query PostgreSQL if reachable.
        3. Query SQLite if PostgreSQL unavailable or misses.
        4. Fall back to LINKEDIN_ACCESS_TOKEN environment variable.
        5. Fall back to local .linkedin_token backup file.
        6. Return credentials dictionary or None if absent.

    Returns:
        dict[str, Any] | None: Stored token data dictionary or None if not found.

    Raises:
        None.
    """
    init_storage()
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

    # SQLite check
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

    # Environment variable check
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

    # Backup file check
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


def refresh_linkedin_access_token(token_id: str = "default") -> dict[str, Any]:
    """Exchange stored refresh token for fresh LinkedIn access token.

    Purpose:
        Renews an expiring access token using the stored OAuth 2.0 refresh token.

    Steps:
        1. Retrieve stored token record. Return error if no refresh token exists.
        2. Validate client ID and client secret configurations.
        3. Send POST request to LinkedIn OAuth token endpoint.
        4. On success, persist new tokens and expiration timestamps to storage.
        5. Return updated access token and persistence details.

    Returns:
        dict[str, Any]: Dictionary containing refreshed access token or error details.

    Raises:
        None.
    """
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


def get_valid_linkedin_access_token(token_id: str = "default") -> str | None:
    """Retrieve active LinkedIn access token, auto-refreshing if near expiration.

    Purpose:
        Ensures a valid access token is always available, refreshing automatically
        if expiry is within 24 hours.

    Steps:
        1. Fetch stored token record. Return None if not found.
        2. Check if token has expires_at timestamp and a refresh token.
        3. If current time is within 24 hours of expiration, call refresh_linkedin_access_token.
        4. Return refreshed or existing valid access token string.

    Returns:
        str | None: Valid access token string, or None if unavailable.

    Raises:
        None.
    """
    record = get_linkedin_token_record(token_id=token_id)
    if not record:
        return None

    access_token = record.get("access_token")
    expires_at = record.get("expires_at")
    refresh_token = record.get("refresh_token")

    if expires_at and refresh_token:
        try:
            if isinstance(expires_at, str):
                exp_dt = datetime.fromisoformat(expires_at)
            else:
                exp_dt = expires_at

            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) + timedelta(hours=24) >= exp_dt:
                logger.info("LinkedIn token nearing expiration or expired. Triggering auto-refresh...")
                refresh_res = refresh_linkedin_access_token(token_id=token_id)
                if refresh_res.get("success"):
                    return refresh_res.get("token")
        except Exception as err:
            logger.warning(f"error checking token expiration date: {err}")

    return access_token


def get_stored_person_urn(token_id: str = "default") -> str | None:
    """Retrieve stored person URN from active token record.

    Purpose:
        Fetches the authenticated user's person URN string from storage.

    Steps:
        1. Query stored token record by token_id.
        2. Return person_urn value if present, else None.

    Returns:
        str | None: Stored person URN string, or None.

    Raises:
        None.
    """
    record = get_linkedin_token_record(token_id=token_id)
    if record and record.get("person_urn"):
        return record["person_urn"]
    return None


# ---------------------------------------------------------------------------
# Session Operations
# ---------------------------------------------------------------------------

def create_session(sub: str, lifetime_days: int = 14) -> str:
    """Create a new session record mapping a secure session_id to user sub.

    Purpose:
        Generates an unguessable session token and stores the session in
        PostgreSQL with SQLite fallback.
    """
    init_storage()
    session_id = secrets.token_urlsafe(32)
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(days=lifetime_days)

    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sessions (session_id, sub, created_at, expires_at)
                        VALUES (%s, %s, %s, %s);
                        """,
                        (session_id, sub, now_utc, expires_at),
                    )
            return session_id
        except Exception as err:
            logger.warning(f"failed to save session to postgres: {err}")

    with get_sqlite_connection() as sqlite_conn:
        sqlite_conn.execute(
            """
            INSERT INTO sessions (session_id, sub, created_at, expires_at)
            VALUES (?, ?, ?, ?);
            """,
            (session_id, sub, now_utc.isoformat(), expires_at.isoformat()),
        )
        sqlite_conn.commit()

    return session_id


def get_session_sub(session_id: str | None) -> str | None:
    """Retrieve user sub from session_id, ensuring the session is valid and active.

    Returns:
        str | None: Authenticated user sub, or None if invalid or expired.
    """
    if not session_id or not session_id.strip():
        return None

    clean_id = session_id.strip()
    init_storage()
    now_utc = datetime.now(timezone.utc)

    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sub, expires_at FROM sessions WHERE session_id = %s;",
                        (clean_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        sub, exp = row[0], row[1]
                        if exp:
                            if exp.tzinfo is None:
                                exp = exp.replace(tzinfo=timezone.utc)
                            if now_utc > exp:
                                delete_session(clean_id)
                                return None
                        return sub
        except Exception as err:
            logger.warning(f"failed checking session in postgres: {err}")

    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.cursor()
            cur.execute(
                "SELECT sub, expires_at FROM sessions WHERE session_id = ?;",
                (clean_id,),
            )
            row = cur.fetchone()
            if row:
                sub, exp_raw = row[0], row[1]
                if exp_raw:
                    exp = datetime.fromisoformat(exp_raw)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if now_utc > exp:
                        delete_session(clean_id)
                        return None
                return sub
    except Exception as err:
        logger.warning(f"failed checking session in sqlite: {err}")

    return None


def delete_session(session_id: str | None) -> bool:
    """Delete a session record by session_id."""
    if not session_id or not session_id.strip():
        return False

    clean_id = session_id.strip()
    init_storage()
    deleted = False

    conn = get_postgres_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sessions WHERE session_id = %s;", (clean_id,))
                    deleted = cur.rowcount > 0
        except Exception as err:
            logger.warning(f"failed deleting session in postgres: {err}")

    try:
        with get_sqlite_connection() as sqlite_conn:
            cur = sqlite_conn.execute("DELETE FROM sessions WHERE session_id = ?;", (clean_id,))
            sqlite_conn.commit()
            if cur.rowcount > 0:
                deleted = True
    except Exception as err:
        logger.warning(f"failed deleting session in sqlite: {err}")

    return deleted
