import logging
from typing import Any
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from config.settings import (
    DATABASE_URL,
    PORT,
)
from services.storage import (
    get_valid_linkedin_access_token,
)
from routers.auth import router as auth_router
from routers.frontend import router as frontend_router
from routers.posts import router as posts_router
from routers.slack import router as slack_router
from utils import (
    ASSETS_DIR,
    FRONTEND_DIR,
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

# health check endpoint for cloud hosts
@app.get("/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    """Provide service health status and dependency checks.

    Purpose:
        Reports runtime health for cloud platform monitoring (Render, Railway),
        including database backend type and LinkedIn authentication status.

    Steps:
        1. Check whether a valid, non-expired LinkedIn access token is available.
        2. Inspect configured database provider (PostgreSQL if DATABASE_URL else SQLite).
        3. Assemble and return health status dictionary.

    Returns:
        dict[str, Any]: Health status dictionary with service name, database type, and auth state.

    Raises:
        None.
    """
    token_valid = bool(get_valid_linkedin_access_token())
    return {
        "status": "healthy",
        "service": "linkedin-slack-agent",
        "database": "postgresql" if DATABASE_URL else "sqlite",
        "linkedin_authenticated": token_valid,
    }

# register api routers
app.include_router(auth_router)
app.include_router(posts_router)
app.include_router(slack_router)
# register frontend router last so SPA fallback does not swallow API routes
app.include_router(frontend_router)

# run server directly when executed
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
