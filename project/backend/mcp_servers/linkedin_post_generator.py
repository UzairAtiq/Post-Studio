"""Compatibility module delegating to linkedin_server."""

from mcp_servers.linkedin_server import (
    approve_or_discard_post_directly,
    generate_and_request_approval,
    get_post_details,
    list_all_generated_posts,
    mcp,
    wait_for_approval_decision,
)

__all__ = [
    "approve_or_discard_post_directly",
    "generate_and_request_approval",
    "get_post_details",
    "list_all_generated_posts",
    "mcp",
    "wait_for_approval_decision",
]

if __name__ == "__main__":
    mcp.run()
