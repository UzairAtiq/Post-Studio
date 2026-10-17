import json
import os
from pathlib import Path
from dotenv import load_dotenv

# base project directory
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent.parent

# load environment variables from .env file in backend directory or project root
for env_candidate in [BASE_DIR / ".env", REPO_ROOT / ".env"]:
    if env_candidate.exists():
        load_dotenv(dotenv_path=env_candidate)
load_dotenv()

# port configuration for cloud host (Render, Railway, Heroku)
PORT = int(os.getenv("PORT", "8000"))

# groq llm configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# database and slack configuration
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("CONNECTION_STRING_SUPABASE")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_WORKSPACE_INVITE_URL = os.getenv("SLACK_WORKSPACE_INVITE_URL", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
SLACK_IGNORE_LINKEDIN_EMAIL = os.getenv(
    "SLACK_IGNORE_LINKEDIN_EMAIL", "false"
).lower().strip() in ("true", "1", "yes")

# linkedin oauth credentials and configuration
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI = os.getenv(
    "LINKEDIN_REDIRECT_URI",
    "http://localhost:8000/linkedin/callback",
)
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")

# langfuse observability configuration
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST")

# sync langfuse host environment variable if base url is configured
if LANGFUSE_BASE_URL and not os.getenv("LANGFUSE_HOST"):
    os.environ["LANGFUSE_HOST"] = LANGFUSE_BASE_URL

# storage path for posts fallback
POSTS_STORAGE_PATH = os.getenv(
    "POSTS_STORAGE_PATH",
    str(BASE_DIR / "data" / "posts.json"),
)

# post execution flow mode: 'legacy' or 'graph' (default 'graph')
POST_FLOW_MODE = os.getenv("POST_FLOW_MODE", "graph").lower().strip()

# helper function to load valid linkedin access token dynamically
def get_linkedin_access_token() -> str | None:
    try:
        from services.storage import get_valid_linkedin_access_token
        token = get_valid_linkedin_access_token()
        if token:
            return token
    except Exception:
        pass

    # check explicit environment variable fallback
    token_from_env = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if token_from_env:
        return token_from_env.strip()

    # fallback to local .linkedin_token file
    token_file_path = BASE_DIR / ".linkedin_token"
    if not token_file_path.exists() and (REPO_ROOT / ".linkedin_token").exists():
        token_file_path = REPO_ROOT / ".linkedin_token"
    if token_file_path.exists():
        token_content = token_file_path.read_text(encoding="utf-8").strip()
        if token_content:
            return token_content

    return None

# helper function to extract person urn dynamically from database or env
def get_linkedin_person_urn() -> str | None:
    try:
        from services.storage import get_stored_person_urn
        stored_urn = get_stored_person_urn()
        if stored_urn:
            return stored_urn.strip()
    except Exception:
        pass

    # check explicit person urn in environment
    direct_urn = os.getenv("LINKEDIN_PERSON_URN")
    if direct_urn:
        return direct_urn.strip()

    # check profile id dictionary variable in environment
    raw_profile_id = os.getenv("LINKEDIN_PROFILE_ID")
    if raw_profile_id:
        try:
            profile_data = json.loads(raw_profile_id)
            if isinstance(profile_data, dict):
                sub_identifier = profile_data.get("sub") or profile_data.get("id")
                if sub_identifier:
                    return f"urn:li:person:{sub_identifier}"
        except (json.JSONDecodeError, TypeError):
            cleaned_id = raw_profile_id.strip()
            if cleaned_id.startswith("urn:li:person:"):
                return cleaned_id
            return f"urn:li:person:{cleaned_id}"

    return None

# lazy resolution properties
LINKEDIN_ACCESS_TOKEN = get_linkedin_access_token()
LINKEDIN_PERSON_URN = get_linkedin_person_urn()