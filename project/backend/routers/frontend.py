"""Frontend dashboard and SPA client-side fallback router."""

from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from utils import get_index_html_response

router = APIRouter(tags=["Frontend"])


# root dashboard endpoint
@router.get("/", response_class=HTMLResponse)
async def root_dashboard() -> HTMLResponse:
    """Serve the root dashboard HTML page.

    Purpose:
        Returns the main dashboard view, loading either the production built React
        assets (frontend/dist/index.html) or a fallback HTML page.

    Steps:
        1. Check if built frontend dist/index.html or static index.html exists.
        2. Read HTML content with cache-control headers.
        3. If no file exists, return default status HTML.

    Returns:
        HTMLResponse: Rendered HTML content for the root dashboard.

    Raises:
        None.
    """
    return get_index_html_response()


# client-side routing fallback for React SPA
@router.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str, request: Request) -> Any:
    """Catch-all route handler supporting client-side routing for the React SPA.

    Purpose:
        Serves the frontend single-page application for unmatched paths while ensuring
        reserved API prefixes are not accidentally shadowed.

    Steps:
        1. Check if the requested path starts with any reserved API, doc, or static prefix
           (health, linkedin, slack, posts, docs, redoc, openapi.json, assets, static).
        2. If path is a reserved route that was not matched by previous routers, raise HTTP 404.
        3. Otherwise, return index.html to allow client-side routing to handle the URL.

    Returns:
        Any: HTMLResponse containing the application entry point.

    Raises:
        HTTPException: 404 Not Found for unmatched paths under reserved API prefixes.
    """
    if full_path.startswith(("health", "linkedin", "slack", "posts", "docs", "redoc", "openapi.json", "assets", "static")):
        raise HTTPException(status_code=404, detail="Not found")
    return get_index_html_response()
