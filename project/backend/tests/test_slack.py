import asyncio
import pytest
from agent.tools import MCPClient

# manually test invoking slack server tool over stdio
@pytest.mark.anyio
async def test_slack_invocation() -> None:
    client = MCPClient("mcp_servers.slack_server")
    await client.connect()
    result = await client.call_tool("send_slack_message", {"message": "Hello from MCP test"})
    print(result)
    await client.close()

if __name__ == "__main__":
    asyncio.run(test_slack_invocation())