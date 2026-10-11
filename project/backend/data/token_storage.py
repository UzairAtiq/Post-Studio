"""Compatibility module delegating LinkedIn token storage to services.storage."""

from services.storage import (
    get_linkedin_token_record,
    get_stored_person_urn,
    get_valid_linkedin_access_token,
    init_token_db,
    refresh_linkedin_access_token,
    save_linkedin_token,
)

__all__ = [
    "get_linkedin_token_record",
    "get_stored_person_urn",
    "get_valid_linkedin_access_token",
    "init_token_db",
    "refresh_linkedin_access_token",
    "save_linkedin_token",
]
