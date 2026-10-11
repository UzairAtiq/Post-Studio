# Backend Endpoints Overview

Overview of all endpoints exposed by the backend API service, organized by router file and execution pipelines.

## Endpoints Summary

| Method | Path | File | One-Line Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | [`main.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/main.py) | Returns service health status, active database backend, and LinkedIn authentication state. |
| `GET` | `/` | [`routers/frontend.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/frontend.py) | Serves root dashboard HTML or built React application. |
| `GET` | `/{full_path:path}` | [`routers/frontend.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/frontend.py) | SPA catch-all route handler serving `index.html` while rejecting reserved API prefixes with 404. |
| `GET` | `/linkedin/login` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Redirects user's browser to LinkedIn OAuth authorization endpoint with CSRF state token. |
| `GET` | `/linkedin/callback` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Exchanges authorization code for tokens, resolves profile/URN, and persists credentials. |
| `GET` | `/callback` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Legacy redirect alias for `/linkedin/callback`. |
| `GET` | `/linkedin/status` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Returns authentication status, masked token preview, expiry time, and refresh token availability. |
| `POST` | `/linkedin/refresh` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Renews access token using stored refresh token without requiring re-authentication. |
| `POST` | `/slack/interactions` | [`routers/slack.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/slack.py) | Receives interactive Slack button actions (approve/discard) and triggers post publication or disposal. |
| `GET` | `/posts` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Lists saved posts with optional status filter, or serves the frontend for direct browser visits. |
| `GET` | `/posts/{post_id}` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Retrieves details of a single post by ID with sensitive tokens masked. |
| `DELETE` | `/posts/{post_id}` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Deletes a stored post record from the database. |
| `POST` | `/posts/generate` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Generates a new post using Groq LLM, saves it with pending status, and sends Slack approval buttons. |

---

## Main Pipelines

### 1. LinkedIn OAuth 2.0 Flow
1. **Initiate:** User requests `GET /linkedin/login`. Backend builds authorization URL with scopes (`openid profile w_member_social email`) and CSRF `state`, issuing a 302 redirect.
2. **Consent & Callback:** LinkedIn redirects user to `GET /linkedin/callback?code=...&state=...`.
3. **Token Exchange:** Backend exchanges authorization code with `https://www.linkedin.com/oauth/v2/accessToken` for access and refresh tokens.
4. **Duplicate Request Recovery:** If token exchange fails because the one-time code was already redeemed (e.g. page reload), checks database for an existing active token and redirects to `/` if present.
5. **Profile Resolution & Storage:** Fetches user's identity via `https://api.linkedin.com/v2/userinfo` (or URN fallback), stores tokens and metadata in the database, and renders `callback.html`.

### 2. Post Generation Pipeline
1. **Trigger:** Client invokes `POST /posts/generate?topic=...`.
2. **Credential Validation:** Verifies that a valid, non-expired LinkedIn access token exists in storage.
3. **Content Generation:** Calls Groq LLM to generate professional LinkedIn post content for the requested topic.
4. **Persistence:** Generates a unique post ID and saves the post in storage with status `pending`.
5. **Slack Notification:** Sends an interactive Slack Block Kit message with "Approve" and "Discard" buttons to the configured Slack webhook.

### 3. Slack Approval & Publishing Pipeline
1. **Interaction:** Reviewer clicks "Approve" or "Discard" on the Slack interactive message.
2. **Webhook Dispatch:** Slack sends form-encoded action payload to `POST /slack/interactions`.
3. **Decision Recording:** Backend parses action ID (`approve_linkedin_post` or `discard_linkedin_post`) and stores the decision.
4. **Execution:**
   - **Approved:** Publishes content to LinkedIn using the UGC post API (`https://api.linkedin.com/v2/ugcPosts`), updates post status to `published` with share URN, and updates the Slack message with confirmation.
   - **Discarded:** Updates post status to `discarded` and updates the Slack message to reflect rejection.
5. **Acknowledgment:** Returns HTTP 200 acknowledgment to Slack.
