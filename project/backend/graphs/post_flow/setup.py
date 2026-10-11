"""Checkpointer and MCP client setup for post flow graph execution."""

import json
import logging
import os
import pickle
import sys
from collections import defaultdict
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("post_flow_setup")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHECKPOINT_PATH = BACKEND_DIR / "data" / "graph_checkpoints.pkl"


def _get_mcp_env() -> dict[str, str]:
    """Assemble process environment with backend path prepended to PYTHONPATH."""
    env = dict(os.environ)
    backend_path = str(BACKEND_DIR)
    current_pythonpath = env.get("PYTHONPATH", "")
    if backend_path not in current_pythonpath.split(os.pathsep):
        env["PYTHONPATH"] = f"{backend_path}{os.pathsep}{current_pythonpath}" if current_pythonpath else backend_path
    return env


class PersistentCheckpointer(InMemorySaver):
    """File-backed checkpointer ensuring graph states survive process restarts.

    Inherits from InMemorySaver and persists storage, writes, and blobs to a
    local binary file upon every checkpoint write.
    """

    def __init__(self, file_path: Path | str | None = None) -> None:
        super().__init__()
        self.file_path = Path(file_path or DEFAULT_CHECKPOINT_PATH)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """Load persisted checkpoints from disk if file exists."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "rb") as f:
                    data = pickle.load(f)
                    self.storage = defaultdict(dict, data.get("storage", {}))
                    self.writes = defaultdict(dict, data.get("writes", {}))
                    self.blobs = defaultdict(None, data.get("blobs", {}))
                logger.info(f"Loaded persistent graph checkpoints from {self.file_path}")
            except Exception as load_err:
                logger.warning(f"Failed loading checkpoints from {self.file_path}: {load_err}")

    def _sync(self) -> None:
        """Serialize current state and writes to persistent storage file."""
        try:
            with open(self.file_path, "wb") as f:
                pickle.dump(
                    {
                        "storage": dict(self.storage),
                        "writes": dict(self.writes),
                        "blobs": dict(self.blobs),
                    },
                    f,
                )
        except Exception as sync_err:
            logger.error(f"Failed saving checkpoints to {self.file_path}: {sync_err}")

    def put(self, *args: Any, **kwargs: Any) -> Any:
        res = super().put(*args, **kwargs)
        self._sync()
        return res

    def put_writes(self, *args: Any, **kwargs: Any) -> Any:
        res = super().put_writes(*args, **kwargs)
        self._sync()
        return res

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        res = await super().aput(*args, **kwargs)
        self._sync()
        return res

    async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
        res = await super().aput_writes(*args, **kwargs)
        self._sync()
        return res

    def close(self) -> None:
        """Flush final state and close checkpointer resources."""
        self._sync()


class GraphMCPClient:
    """Dedicated MCP client for post flow graph node tool invocations.

    Connects to the linkedin-post-generator MCP server over stdio transport
    and provides structured JSON tool invocation methods.
    """

    def __init__(self, server_module: str = "mcp_servers.linkedin_server") -> None:
        self.server_module = server_module
        self.session: ClientSession | None = None
        self._stack = AsyncExitStack()
        self._is_connected = False

    async def connect(self) -> None:
        """Spawn MCP server process and establish stdio communication session."""
        if self._is_connected and self.session:
            return

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", self.server_module],
            cwd=str(BACKEND_DIR),
            env=_get_mcp_env(),
        )

        read_stream, write_stream = await self._stack.enter_async_context(
            stdio_client(params)
        )
        self.session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()
        self._is_connected = True
        logger.info(f"Graph MCP client connected to {self.server_module}")

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke MCP tool and parse resulting JSON text response.

        Args:
            name: Tool name registered on MCP server.
            args: Keyword arguments for tool execution.

        Returns:
            dict[str, Any]: Parsed response dictionary.
        """
        if not self.session or not self._is_connected:
            raise RuntimeError("GraphMCPClient is not connected. Call connect() first.")

        result = await self.session.call_tool(name, args)
        if not result.content:
            return {}

        text_content = result.content[0].text
        try:
            return json.loads(text_content)
        except (json.JSONDecodeError, TypeError):
            return {"result": text_content}

    async def close(self) -> None:
        """Tear down MCP subprocess and close communication streams."""
        if self._is_connected:
            try:
                await self._stack.aclose()
            except Exception as close_err:
                logger.warning(f"Error closing GraphMCPClient stack: {close_err}")
            finally:
                self.session = None
                self._is_connected = False
                logger.info("Graph MCP client connection closed.")


def create_checkpointer() -> PersistentCheckpointer:
    """Create a persistent checkpointer instance for graph state persistence."""
    return PersistentCheckpointer()


def create_mcp_client() -> GraphMCPClient:
    """Create a GraphMCPClient instance configured for linkedin server tools."""
    return GraphMCPClient()
