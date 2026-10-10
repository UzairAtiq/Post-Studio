from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from utils import get_index_html_response

router = APIRouter(tags=["Frontend"])

# root dashboard endpoint
@router.get("/", response_class=HTMLResponse)
async def root_dashboard() -> HTMLResponse:
    return get_index_html_response()

# client-side routing fallback for React SPA
@router.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str, request: Request) -> Any:
    if full_path.startswith(("health", "linkedin", "slack", "posts", "docs", "redoc", "openapi.json", "assets", "static")):
        raise HTTPException(status_code=404, detail="Not found")
    return get_index_html_response()
