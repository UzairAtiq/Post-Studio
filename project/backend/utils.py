from pathlib import Path
from fastapi import Request
from fastapi.responses import HTMLResponse
from config.settings import BASE_DIR, LINKEDIN_REDIRECT_URI

# frontend paths
FRONTEND_DIR = (BASE_DIR.parent / "frontend").resolve() if (BASE_DIR.parent / "frontend").exists() else (BASE_DIR / "frontend").resolve()
DIST_DIR = FRONTEND_DIR / "dist"
ASSETS_DIR = DIST_DIR / "assets"

def get_index_html_response() -> HTMLResponse:
    index_file = DIST_DIR / "index.html" if DIST_DIR.exists() else FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(
            content=index_file.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse(content="<h2>LinkedIn Agent API Running</h2><a href='/docs'>Swagger Docs</a>")

# resolve effective redirect uri from explicit config or incoming request headers
def get_effective_redirect_uri(request: Request) -> str:
    # check explicit non-localhost redirect uri in environment
    if LINKEDIN_REDIRECT_URI and not LINKEDIN_REDIRECT_URI.startswith("http://localhost") and not LINKEDIN_REDIRECT_URI.startswith("http://127.0.0.1"):
        return LINKEDIN_REDIRECT_URI.strip()

    # detect protocol and host from cloud reverse proxy headers (e.g. Render, Railway)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    if host and "localhost" not in host and "127.0.0.1" not in host:
        return f"{proto}://{host}/linkedin/callback"

    return LINKEDIN_REDIRECT_URI or f"{proto}://{host}/linkedin/callback"
