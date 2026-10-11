"""Model Context Protocol (MCP) server for querying database customer records."""

from typing import Any
import psycopg

from config.settings import DATABASE_URL
from mcp.server.fastmcp import FastMCP

# initialize database mcp server
mcp = FastMCP("database-server")


@mcp.tool()
def lookup_customer(customer_id: int) -> dict[str, Any]:
    """Look up a customer record by unique customer identifier.

    Purpose:
        Queries the database customers table to fetch customer details for agent workflows.

    Steps:
        1. Validate that DATABASE_URL is configured in environment.
        2. Establish PostgreSQL database connection.
        3. Execute parameterized SELECT query filtering by customer_id.
        4. Fetch matching row. Return error dictionary if not found.
        5. Assemble and return customer attributes dictionary.

    Returns:
        dict[str, Any]: Dictionary containing customer attributes or error details.

    Raises:
        None.
    """
    if not DATABASE_URL:
        return {"error": "DATABASE_URL is not set in environment."}

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, email, company, status
                    FROM customers
                    WHERE id = %s
                    """,
                    (customer_id,),
                )
                row = cur.fetchone()

    except psycopg.Error as err:
        return {"error": f"Database error: {err}"}

    if row is None:
        return {"error": "Customer not found"}

    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "company": row[3],
        "status": row[4],
    }


if __name__ == "__main__":
    mcp.run()
