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
