from enum import Enum

# centralized post status enumeration
class PostStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DISCARDED = "discarded"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    # Legacy alias mapping to canonical discarded status
    REJECTED = "discarded"

# Maximum timeout in seconds for synchronous MCP approval waiting
MAX_APPROVAL_TIMEOUT_SECONDS: int = 300
