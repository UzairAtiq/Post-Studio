"""State schema definition for post flow graph orchestration."""

from typing import Any, TypedDict


class PostFlowState(TypedDict, total=False):
    """Execution state tracked across post generation and approval graph nodes.

    Attributes:
        topic: Seed subject or theme requested for post generation.
        post_id: Unique post record identifier in persistent storage.
        content: AI-generated post body text.
        decision: Human reviewer decision from Slack ('approved' or 'discarded').
        status: Current post lifecycle status ('pending', 'approved', 'discarded', 'published', 'publish_failed', 'failed').
        slack_delivery: Dispatch metadata from Slack Block Kit notification.
        linkedin_response: Response payload and post URN returned from LinkedIn publish API.
        error: Failure explanation string if node execution encounters an error.
        is_complete: Boolean indicating whether the post flow satisfies final completion criteria.
        final_check_notes: Evaluation summary produced during the agent final check step.
    """

    topic: str
    post_id: str
    content: str
    decision: str
    status: str
    slack_delivery: dict[str, Any] | None
    linkedin_response: dict[str, Any] | None
    error: str | None
    is_complete: bool
    final_check_notes: str | None
