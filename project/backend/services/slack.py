"""Slack direct message approval service for multi-user workspace integration."""

import logging
import urllib.parse
from typing import Any

import requests

from config.settings import SLACK_BOT_TOKEN
from services.storage import update_post_slack_ref

logger = logging.getLogger("slack_dm_service")

__all__ = [
    "invite_user_to_channel",
    "lookup_slack_user_id",
    "send_approval_dm",
    "send_welcome_dm",
    "update_message",
]


def lookup_slack_user_id(email: str) -> str | None:
    """Lookup Slack user ID by their email address using the bot token.

    Purpose:
        Resolves a user's Slack member ID (e.g. U12345678) from their LinkedIn account email
        to enable sending direct messages in the shared Slack workspace.

    Steps:
        1. Validate email and check that SLACK_BOT_TOKEN is configured.
        2. Send GET request to https://slack.com/api/users.lookupByEmail.
        3. Parse the JSON response. If ok is True, return the user ID.
        4. If user is not found or error occurs, log warning and return None.

    Returns:
        str | None: Slack member ID if found, None otherwise.

    Raises:
        None.
    """
    if not email or not email.strip():
        return None

    token = SLACK_BOT_TOKEN
    if not token:
        logger.warning("SLACK_BOT_TOKEN is not configured; cannot lookup Slack user ID.")
        return None

    clean_email = email.strip()
    encoded_email = urllib.parse.quote(clean_email)
    url = f"https://slack.com/api/users.lookupByEmail?email={encoded_email}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if data.get("ok"):
            user_id = data.get("user", {}).get("id")
            logger.info(f"Resolved Slack user ID '{user_id}' for email '{clean_email}'.")
            return user_id

        error_reason = data.get("error", "unknown_error")
        logger.info(f"Slack lookupByEmail for '{clean_email}' returned: {error_reason}")
        return None
    except requests.RequestException as err:
        logger.warning(f"Network error looking up Slack user by email '{clean_email}': {err}")
        return None


def send_approval_dm(slack_user_id: str, post: dict[str, Any]) -> dict[str, Any] | None:
    """Send an interactive Block Kit approval direct message to a user.

    Purpose:
        Delivers a private DM containing post preview and Approve / Reject buttons
        to the user's personal Slack account, persisting the message timestamp and channel ID.

    Steps:
        1. Validate slack_user_id, post dictionary, and SLACK_BOT_TOKEN.
        2. Construct Slack Block Kit blocks containing preview and interactive buttons.
        3. Dispatch chat.postMessage to the user's Slack user ID.
        4. On success, store the channel and message ts on the post record via update_post_slack_ref.
        5. Return the delivery payload or None on failure.

    Returns:
        dict[str, Any] | None: Dictionary with channel and ts if sent, None on failure.

    Raises:
        None.
    """
    if not slack_user_id or not slack_user_id.strip():
        logger.warning("Cannot send approval DM: empty slack_user_id.")
        return None

    token = SLACK_BOT_TOKEN
    if not token:
        logger.warning("SLACK_BOT_TOKEN is not configured; cannot send approval DM.")
        return None

    post_id = post.get("post_id", "unknown_post")
    content = post.get("content", "")
    clean_user_id = slack_user_id.strip()

    blocks: list[dict[str, Any]] = [
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
                        "text": "✅ Approve & Post",
                        "emoji": True,
                    },
                    "style": "primary",
                    "value": post_id,
                    "action_id": "approve_post",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "❌ Reject & Discard",
                        "emoji": True,
                    },
                    "style": "danger",
                    "value": post_id,
                    "action_id": "reject_post",
                },
            ],
        },
    ]

    fallback_text = f"New LinkedIn Post Approval Request for ID: {post_id}"
    payload = {
        "channel": clean_user_id,
        "text": fallback_text,
        "blocks": blocks,
    }
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        if data.get("ok"):
            channel = data.get("channel")
            ts = data.get("ts")
            logger.info(f"Approval DM sent to '{clean_user_id}' for post '{post_id}' (channel={channel}, ts={ts}).")
            if post_id and channel and ts:
                update_post_slack_ref(post_id=post_id, channel=channel, ts=ts)
            return {"channel": channel, "ts": ts, "ok": True}

        error_reason = data.get("error", "unknown_error")
        logger.error(f"Failed to send Slack DM to '{clean_user_id}': {error_reason}")
        return None
    except requests.RequestException as err:
        logger.error(f"Network error sending approval DM to '{clean_user_id}': {err}")
        return None


def update_message(channel: str, ts: str, text: str) -> bool:
    """Update an existing Slack message using chat.update API with the bot token.

    Purpose:
        Replaces interactive approval buttons in a user's Slack DM with a final resolution message
        (e.g., approved and published, or rejected).

    Steps:
        1. Validate channel, ts, text, and SLACK_BOT_TOKEN.
        2. Construct payload with replaced blocks and plain text summary.
        3. Dispatch POST request to https://slack.com/api/chat.update.
        4. Return True if ok is True in response, False otherwise.

    Returns:
        bool: True if message was successfully updated, False otherwise.

    Raises:
        None.
    """
    if not channel or not ts:
        logger.warning("Cannot update Slack message: missing channel or ts.")
        return False

    token = SLACK_BOT_TOKEN
    if not token:
        logger.warning("SLACK_BOT_TOKEN is not configured; cannot update Slack message.")
        return False

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text,
            },
        }
    ]

    payload = {
        "channel": channel,
        "ts": ts,
        "text": text,
        "blocks": blocks,
    }
    url = "https://slack.com/api/chat.update"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        if data.get("ok"):
            logger.info(f"Updated Slack message in channel '{channel}' at ts '{ts}'.")
            return True

        error_reason = data.get("error", "unknown_error")
        logger.error(f"Failed to update Slack message (channel={channel}, ts={ts}): {error_reason}")
        return False
    except requests.RequestException as err:
        logger.error(f"Network error updating Slack message (channel={channel}, ts={ts}): {err}")
        return False


def invite_user_to_channel(channel: str, slack_user_id: str) -> bool:
    """Invite a Slack member into a specific channel using the bot token.

    Purpose:
        Adds a workspace member to a designated Slack approvals or alerts channel.

    Steps:
        1. Validate channel and slack_user_id, and verify SLACK_BOT_TOKEN is set.
        2. Post request to https://slack.com/api/conversations.invite.
        3. Return True if invited or already present; False otherwise.

    Returns:
        bool: True if member is in channel or successfully invited, False otherwise.
    """
    if not channel or not channel.strip() or not slack_user_id or not slack_user_id.strip():
        return False

    token = SLACK_BOT_TOKEN
    if not token:
        logger.warning("SLACK_BOT_TOKEN is not configured; cannot invite user to channel.")
        return False

    clean_channel = channel.strip()
    clean_user = slack_user_id.strip()
    url = "https://slack.com/api/conversations.invite"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "channel": clean_channel,
        "users": clean_user,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        if data.get("ok") or data.get("error") == "already_in_channel":
            logger.info(f"User '{clean_user}' invited to or already in channel '{clean_channel}'.")
            return True
        logger.warning(f"Could not invite user '{clean_user}' to channel '{clean_channel}': {data.get('error')}")
        return False
    except requests.RequestException as err:
        logger.warning(f"Network error inviting user '{clean_user}' to channel '{clean_channel}': {err}")
        return False


def send_welcome_dm(slack_user_id: str, email: str | None = None) -> bool:
    """Send a friendly welcome direct message confirming Slack account link.

    Purpose:
        Notifies the user via private DM that their Slack account has been linked.

    Returns:
        bool: True if message sent successfully, False otherwise.
    """
    if not slack_user_id or not slack_user_id.strip():
        return False

    token = SLACK_BOT_TOKEN
    if not token:
        return False

    clean_user = slack_user_id.strip()
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    email_note = f" (linked email: `{email}`)" if email else ""
    payload = {
        "channel": clean_user,
        "text": f"👋 Your Slack account{email_note} has been successfully connected to Post Studio! You will receive post approval notifications right here.",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        return bool(data.get("ok"))
    except requests.RequestException as err:
        logger.warning(f"Failed sending welcome DM to '{clean_user}': {err}")
        return False

