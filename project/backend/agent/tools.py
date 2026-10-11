import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parent.parent

def _get_mcp_env() -> dict[str, str]:
    env = dict(os.environ)
    backend_path = str(BACKEND_DIR)
    current_pythonpath = env.get("PYTHONPATH", "")
    if backend_path not in current_pythonpath.split(os.pathsep):
        env["PYTHONPATH"] = f"{backend_path}{os.pathsep}{current_pythonpath}" if current_pythonpath else backend_path
    return env

# standalone utility for testing single mcp servers in isolation
class MCPClient:
    def __init__(self, server_module: str) -> None:
        self.server_module = server_module
        self.session: ClientSession | None = None
        self._stack = AsyncExitStack()

    async def connect(self) -> None:
        # configure stdio server parameters with python executable
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", self.server_module],
            cwd=str(BACKEND_DIR),
            env=_get_mcp_env(),
        )

        # establish stdio read and write channels
        read_stream, write_stream = await self._stack.enter_async_context(
            stdio_client(params)
        )

        # initialize client session and handshake
        self.session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()

    async def call_tool(self, name: str, args: dict) -> str:
        # verify active session before invoking tool
        if not self.session:
            raise RuntimeError("MCPClient is not connected. Call connect() first.")

        result = await self.session.call_tool(name, args)
        return result.content[0].text

    async def close(self) -> None:
        # close all active async context managers and streams
        await self._stack.aclose()

# fetch all tools across registered mcp servers for langgraph agent
async def get_langgraph_tools() -> list:
    server_env = _get_mcp_env()
    # configure all mcp servers with stdio transport
    client = MultiServerMCPClient(
        {
            "database": {
                "command": sys.executable,
                "args": ["-m", "mcp_servers.database_server"],
                "transport": "stdio",
                "cwd": str(BACKEND_DIR),
                "env": server_env,
            },
            "slack": {
                "command": sys.executable,
                "args": ["-m", "mcp_servers.slack_server"],
                "transport": "stdio",
                "cwd": str(BACKEND_DIR),
                "env": server_env,
            },
            "linkedin_server": {
                "command": sys.executable,
                "args": ["-m", "mcp_servers.linkedin_server"],
                "transport": "stdio",
                "cwd": str(BACKEND_DIR),
                "env": server_env,
            },
        }
    )

    # retrieve converted langchain structured tools from all active servers
    tools = await client.get_tools()
    return tools