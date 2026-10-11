"""Slack service managing Block Kit message construction, delivery, and replacement."""

import logging
from typing import Any

import requests

from config.constants import PostStatus
from config.settings import SLACK_WEBHOOK_URL

logger = logging.getLogger("slack_service")

__all__ = [
    "build_approval_blocks",
    "send_approval_request",
    "send_slack_message",
    "update_slack_message",
]


def build_approval_blocks(post_id: str, content: str) -> list[dict[str, Any]]:
    """Construct Slack Block Kit interactive message payload with approval buttons.

    Purpose:
        Formats post preview text and interactive Approve/Discard buttons into Slack Block Kit components.

    Steps:
        1. Create header block indicating new LinkedIn post approval request.
        2. Create section block displaying post ID and pending status.
        3. Create section block displaying formatted generated post content.
        4. Create actions block with primary Approve and danger Discard buttons.
        5. Return structured Block Kit dictionary list.

    Returns:
        list[dict[str, Any]]: Block Kit layout elements for Slack message.

    Raises:
        None.
    """
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📝 New LinkedIn Post Approval Request",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Post ID:*\n`{post_id}`",
                },
                {
                    "type": "mrkdwn",
                    "text": "*Status:*\nPending Approval ⏳",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Generated Content:*\n>>> {content}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Yes (Approve & Post)",
                        "emoji": True,
                    },
                    "style": "primary",
                    "value": post_id,
                    "action_id": "approve_linkedin_post",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "❌ No (Discard)",
                        "emoji": True,
                    },
                    "style": "danger",
                    "value": post_id,
                    "action_id": "discard_linkedin_post",
                },
            ],
        },
    ]


def send_approval_request(
    post_id: str,
    content: str,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """Send an interactive post approval request to Slack channel via incoming webhook.

    Purpose:
        Delivers Block Kit cards with interactive buttons to Slack for human-in-the-loop review.

    Steps:
        1. Resolve webhook URL from parameter or SLACK_WEBHOOK_URL configuration.
        2. If webhook URL is missing, raise ValueError.
        3. Build Block Kit blocks and plain-text fallback text.
        4. Send HTTP POST request with JSON payload to Slack webhook.
        5. Return delivery confirmation or error details.

    Returns:
        dict[str, Any]: Dictionary containing delivery success flag, status, and post_id.

    Raises:
        ValueError: If SLACK_WEBHOOK_URL is not configured.
    """
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        raise ValueError("SLACK_WEBHOOK_URL is not set. Please provide it in the .env file.")

    blocks = build_approval_blocks(post_id=post_id, content=content)
    fallback_text = f"LinkedIn Post Approval Request for ID: {post_id}"

    payload = {
        "text": fallback_text,
        "blocks": blocks,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return {
                "success": True,
                "status": "sent",
                "post_id": post_id,
            }

        logger.error(f"Slack webhook failed ({response.status_code}): {response.text}")
        return {
            "success": False,
            "status": "failed",
            "status_code": response.status_code,
            "error": response.text,
        }

    except requests.RequestException as err:
        logger.error(f"network error sending message to Slack: {err}")
        return {
            "success": False,
            "status": "failed",
            "error": str(err),
        }


def update_slack_message(
    response_url: str,
    post_id: str,
    action_type: str,
    detail_message: str = "",
) -> bool:
    """Update original interactive Slack message card via action response_url.

    Purpose:
        Replaces interactive buttons with final confirmation message (approved or discarded).

    Steps:
        1. Determine status headline text according to action_type.
        2. Construct updated block kit payload with replace_original=True.
        3. If detail_message is provided, append detail section block.
        4. Send HTTP POST request to Slack response_url with 10-second timeout.
        5. Return True if Slack responded with 200 OK, False otherwise.

    Returns:
        bool: True if Slack card was successfully updated, False otherwise.

    Raises:
        None.
    """
    if action_type in ("publish_failed", PostStatus.PUBLISH_FAILED) or "Failed" in detail_message:
        status_text = f"⚠️ *Post `{post_id}` was approved, but publishing to LinkedIn failed.*"
    elif action_type in ("approved", "published", PostStatus.APPROVED, PostStatus.PUBLISHED):
        status_text = f"✅ *Post `{post_id}` was APPROVED and published to LinkedIn!*"
    else:
        status_text = f"❌ *Post `{post_id}` was DISCARDED by user.*"

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": status_text,
            },
        }
    ]

    if detail_message:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_{detail_message}_",
                },
            }
        )

    payload = {
        "replace_original": True,
        "text": status_text,
        "blocks": blocks,
    }

    try:
        response = requests.post(response_url, json=payload, timeout=10)
        return response.status_code == 200
    except requests.RequestException as err:
        logger.error(f"failed to update Slack message via response_url: {err}")
        return False


def send_slack_message(message: str) -> dict[str, Any]:
    """Send a plain text notification message to Slack via incoming webhook.

    Purpose:
        Delivers text messages to configured Slack channel for notifications and alerts.

    Steps:
        1. Verify SLACK_WEBHOOK_URL configuration in environment.
        2. Send HTTP POST request with JSON payload {"text": message}.
        3. Check response status code. If 200 OK, return sent status.
        4. If non-200 or network error, return error details dictionary.

    Returns:
        dict[str, Any]: Delivery status dictionary indicating sent or error details.

    Raises:
        None.
    """
    if not SLACK_WEBHOOK_URL:
        return {"error": "SLACK_WEBHOOK_URL not set in .env"}

    try:
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message},
            timeout=5,
        )

        if response.status_code == 200:
            return {"status": "sent", "message": message}

        return {
            "error": f"Slack returned status {response.status_code}",
            "details": response.text,
        }
    except requests.RequestException as e:
        return {"error": str(e)}

