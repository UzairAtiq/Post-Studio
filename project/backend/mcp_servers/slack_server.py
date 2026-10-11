"""Model Context Protocol (MCP) server for dispatching messages to Slack."""

from typing import Any
from mcp.server.fastmcp import FastMCP
from services.slack_service import send_slack_message as send_slack_message_service

# initialize slack mcp server
mcp = FastMCP("slack-server")


@mcp.tool()
def send_slack_message(message: str) -> dict[str, Any]:
    """Send a plain text notification message to Slack via incoming webhook.

    Purpose:
        Thin MCP tool wrapper delegating message delivery to Slack service.

    Steps:
        1. Delegate directly to slack_service.send_slack_message.
        2. Return delivery status dictionary.

    Returns:
        dict[str, Any]: Delivery status dictionary indicating sent or error details.

    Raises:
        None.
    """
    return send_slack_message_service(message=message)


if __name__ == "__main__":
    mcp.run()