import logging
from typing import Any
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from config.settings import (
    DATABASE_URL,
    PORT,
)
from data.token_storage import (
    get_valid_linkedin_access_token,
)
from routers.auth import router as auth_router
from routers.posts import router as posts_router
from routers.slack import router as slack_router
from utils import (
    ASSETS_DIR,
    FRONTEND_DIR,
    get_index_html_response,
)

# configure application logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main_api")

# initialize fastapi app
app = FastAPI(
    title="LinkedIn Post Automation & Slack Approval API",
    description="Unified API service managing LinkedIn OAuth, automated token refreshes, Slack interactive approvals, and post publishing.",
    version="2.0.0",
)

# mount frontend static and built react assets
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# register api routers
app.include_router(auth_router)
app.include_router(posts_router)
app.include_router(slack_router)

# root dashboard endpoint
@app.get("/", response_class=HTMLResponse)
async def root_dashboard() -> HTMLResponse:
    return get_index_html_response()

# health check endpoint for cloud hosts
@app.get("/health")
def health_check() -> dict[str, Any]:
    token_valid = bool(get_valid_linkedin_access_token())
    return {
        "status": "healthy",
        "service": "linkedin-slack-agent",
        "database": "postgresql" if DATABASE_URL else "sqlite",
        "linkedin_authenticated": token_valid,
    }

# client-side routing fallback for React SPA
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str, request: Request) -> Any:
    if full_path.startswith(("health", "linkedin", "slack", "posts", "docs", "redoc", "openapi.json", "assets", "static")):
        raise HTTPException(status_code=404, detail="Not found")
    return get_index_html_response()

# run server directly when executed
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
