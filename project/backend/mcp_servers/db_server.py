"""Compatibility module delegating to database_server."""

from mcp_servers.database_server import lookup_customer, mcp

__all__ = ["lookup_customer", "mcp"]

if __name__ == "__main__":
    mcp.run()