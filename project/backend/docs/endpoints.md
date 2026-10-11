# Backend Endpoints and Architecture Overview

Overview of all endpoints, MCP tools, and services exposed by the LinkedIn Post Automation backend.

## 1. Route Table

> For endpoint parameters, security verification, and handler mechanics, see the [API Routers Layer Guide](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/docs/routers.md).

| Method | Path | File | One-Line Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | [`main.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/main.py) | Returns service health status, active execution flow mode (`graph` or `legacy`), active database backend, and LinkedIn authentication state. |
| `GET` | `/` | [`routers/frontend.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/frontend.py) | Serves root dashboard HTML or built React application. |
| `GET` | `/{full_path:path}` | [`routers/frontend.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/frontend.py) | SPA catch-all route handler serving `index.html` while rejecting reserved API prefixes with 404. |
| `GET` | `/linkedin/login` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Redirects user's browser to LinkedIn OAuth authorization endpoint with CSRF state token. |
| `GET` | `/linkedin/callback` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Exchanges authorization code for tokens, resolves profile/URN, and persists credentials. |
| `GET` | `/callback` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Legacy redirect alias for `/linkedin/callback`. |
| `GET` | `/linkedin/status` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Returns authentication status, masked token preview, expiry time, and refresh token availability. |
| `POST` | `/linkedin/refresh` | [`routers/auth.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/auth.py) | Renews access token using stored refresh token without requiring re-authentication. |
| `POST` | `/slack/interactions` | [`routers/slack.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/slack.py) | Verifies Slack HMAC signature/timestamp, acknowledges within 3 seconds, and resumes graph or legacy flow in background based on `POST_FLOW_MODE`. |
| `GET` | `/posts` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Lists saved posts with optional status filter, or serves the frontend for direct browser visits. |
| `GET` | `/posts/{post_id}` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Retrieves details of a single post by ID with sensitive tokens masked. |
| `DELETE` | `/posts/{post_id}` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Deletes a stored post record from the database. |
| `POST` | `/posts/generate` | [`routers/posts.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/routers/posts.py) | Generates a new post, saves it with pending status, and sends Slack approval buttons (routes to LangGraph or legacy flow based on `POST_FLOW_MODE`). |

---

## 2. MCP Tool Table

> For protocol architecture, FastMCP definitions, and composite tools, see the [MCP Servers Layer Guide](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/docs/mcp_servers.md).

| Tool | File | One-Line Purpose |
| :--- | :--- | :--- |
| `generate_post` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Generates LinkedIn post content using Groq LLM via `post_service`. |
| `save_post` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Saves drafted post with pending status into storage via `post_service`. |
| `request_approval` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Delivers interactive Slack Block Kit approval card via `post_service`. |
| `publish_post` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Publishes approved post to LinkedIn REST API via `post_service`. |
| `discard_post` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Disposes of rejected post and updates Slack card via `post_service`. |
| `get_post` | [`mcp_servers/linkedin_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/linkedin_server.py) | Fetches single post record by post ID via `post_service`. |
| `lookup_customer` | [`mcp_servers/database_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/database_server.py) | Queries database customers table for customer details by ID. |
| `send_slack_message` | [`mcp_servers/slack_server.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/mcp_servers/slack_server.py) | Dispatches plain text notification to Slack channel via incoming webhook. |

---

## 3. Service Table

> For detailed function listings, dependency rules, and coding standards, see the [Services Layer Guide](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/docs/services.md).

| File | Responsibility | Callers | Error Paradigm |
| :--- | :--- | :--- | :--- |
| [`services/storage.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/services/storage.py) | Single home for PostgreSQL, SQLite, and JSON fallback; post persistence; token lifecycle; schema init and status migrations. | `routers/auth.py`, `routers/posts.py`, `services/approval.py`, `mcp_servers/linkedin_server.py`, `main.py` | Graceful fallback across backends (PostgreSQL -> SQLite -> JSON); handles errors internally or returns None. |
| [`services/approval.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/services/approval.py) | Decision recording (`record_slack_decision`), idempotent decision execution (`execute_post_decision`), and 2-second polling loop (`wait_for_decision`). | `routers/slack.py`, `mcp_servers/linkedin_server.py` | Returns structured outcome dictionaries (`{'success': bool, ...}`). |
| [`services/slack_service.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/services/slack_service.py) | Slack Block Kit message formatting (`build_approval_blocks`), webhook dispatch (`send_approval_request`), and single home for card replacement (`update_slack_message`). | `routers/posts.py`, `services/approval.py`, `mcp_servers/linkedin_server.py` | Webhook delivery returns result dict; card replacement returns boolean success; raises `ValueError` if webhook URL unconfigured. |
| [`services/groq_service.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/services/groq_service.py) | Groq LLM prompt construction and LinkedIn post content generation with Langfuse tracing. | `routers/posts.py`, `mcp_servers/linkedin_server.py` | **Raises exceptions**: Always raises `ValueError` or `RuntimeError` on config/API/rate-limit failures; never returns error dict. |
| [`services/linkedin_service.py`](file:///Users/uzair/Developer/mcp-langraph-agent/project/backend/services/linkedin_service.py) | LinkedIn profile/URN lookup (`fetch_linkedin_person_urn`) and UGC post publication (`publish_post_to_linkedin`). | `routers/auth.py`, `services/approval.py` | **Returns result dict**: Catches HTTP/network failures and returns `{'success': False, 'error': ...}`; does not raise on API errors. |

---

## 4. Short Step-by-Step Flows

### Post Generation Flow
1. Client requests `POST /posts/generate?topic=...` or MCP invokes `generate_and_request_approval`.
2. Validates that a non-expired LinkedIn OAuth access token and author person URN are available.
3. Generates high-impact post text via `groq_service.generate_linkedin_post_content`.
4. Generates next post ID (`post_{count}_{hash}`) and saves post with `pending` status via `services.storage`.
5. Sends Block Kit message with "Approve" and "Discard" buttons to Slack via `slack_service.send_approval_request`.

### Slack Approval Flow
1. Reviewer clicks "Approve" or "Discard" in Slack.
2. Slack sends interaction payload to `POST /slack/interactions`.
3. Endpoint verifies `X-Slack-Signature` and `X-Slack-Request-Timestamp` (<5 min old), rejecting forged or replayed requests with `401`.
4. Endpoint synchronously records human choice in storage via `approval.record_slack_decision`.
5. Endpoint enqueues `approval.execute_post_decision` in FastAPI `BackgroundTasks` and immediately returns HTTP `200` to satisfy Slack's 3-second limit.
6. In background task, `execute_post_decision` checks idempotency. If not already finalized, it publishes to LinkedIn via `linkedin_service.publish_post_to_linkedin` (or marks discarded) and replaces Slack card via `slack_service.update_slack_message`.

### LinkedIn OAuth Flow
1. User visits `/linkedin/login` and is redirected to LinkedIn authorization URL with requested scopes.
2. LinkedIn redirects user to `/linkedin/callback?code=...&state=...`.
3. Backend exchanges code for access and refresh tokens, handling duplicate codes gracefully.
4. Backend resolves person author URN via `/v2/userinfo` (or `/v2/me` fallback).
5. Tokens and profile metadata are securely stored via `services.storage.save_linkedin_token`.

### MCP Sync vs Async Approval Flow
- **Async Mode (`wait_for_decision=False`):** `generate_and_request_approval` generates post, saves record, sends Slack notification, and immediately returns `status="pending_approval"` with post ID. The AI agent or caller can inspect post status later via `get_post_details`.
- **Sync Mode (`wait_for_decision=True` or `wait_for_approval_decision`):** Tool calls `approval.wait_for_decision(post_id, timeout_seconds)`. Timeout is strictly capped to `MAX_APPROVAL_TIMEOUT_SECONDS` (300s). The polling loop checks storage every 2 seconds for Slack button clicks, triggers publication/discard upon detection, and returns the final execution result.
