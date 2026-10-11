"""Model Context Protocol (MCP) server for dispatching messages to Slack."""

from typing import Any
import requests

from config.settings import SLACK_WEBHOOK_URL
from mcp.server.fastmcp import FastMCP

# initialize slack mcp server
mcp = FastMCP("slack-server")


@mcp.tool()
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


if __name__ == "__main__":
    mcp.run()