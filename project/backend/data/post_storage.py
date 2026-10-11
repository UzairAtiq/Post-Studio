"""Compatibility module delegating post persistence to services.storage."""

from services.storage import (
    delete_post_record,
    generate_next_post_id,
    get_post_by_id,
    init_posts_db,
    list_posts,
    save_post_record,
    update_post_status,
)

__all__ = [
    "delete_post_record",
    "generate_next_post_id",
    "get_post_by_id",
    "init_posts_db",
    "list_posts",
    "save_post_record",
    "update_post_status",
]
