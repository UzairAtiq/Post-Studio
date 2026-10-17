# Local Setup & Running Guide

This guide walks through setting up and running the entire LinkedIn Post Automation Agent locally, including the FastAPI backend, React dashboard, and Model Context Protocol (MCP) servers.

---

## Prerequisites

- **Python 3.12+**
- **Node.js 18+** & `npm`
- **Git**
- Optional: LinkedIn Developer App credentials, Slack Bot credentials, and Groq / OpenAI API key.

---

## 1. Clone & Environment Setup

Clone the repository and set up a Python virtual environment:

```bash
# Clone the repository
git clone https://github.com/UzairAtiq/mcp-langraph-agent.git
cd mcp-langraph-agent

# Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install backend dependencies
pip install -r project/backend/requirements.txt

# Install frontend dependencies
cd project/frontend
npm install
cd ../..
```

---

## 2. Configure Environment Variables

Copy the example environment template:

```bash
cp .env.example .env
```

Open `.env` and fill in the required configuration:

### Core AI & Storage
| Variable | Description | Default / Example |
|---|---|---|
| `GROQ_API_KEY` | Groq API Key for default post generation (optional if using BYOK in browser) | `gsk_...` |
| `GROQ_MODEL` | Default model name for Groq generation | `openai/gpt-oss-120b` |
| `DATABASE_URL` | PostgreSQL connection URL (optional; automatically falls back to SQLite `app.db`) | `postgresql://user:pass@host:5432/db` |
| `POST_FLOW_MODE` | Flow engine: `graph` (LangGraph) or `legacy` (procedural) | `graph` |

### LinkedIn OAuth
| Variable | Description |
|---|---|
| `LINKEDIN_CLIENT_ID` | OAuth 2.0 Client ID from LinkedIn Developer Portal |
| `LINKEDIN_CLIENT_SECRET` | OAuth 2.0 Client Secret |
| `LINKEDIN_REDIRECT_URI` | `http://localhost:8000/linkedin/callback` (or your production callback URL) |

*For complete instructions on generating these keys, see the [LinkedIn Setup Guide](linkedin_setup.md).*

### Slack Integration
| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token (`xoxb-...`) with `chat:write`, `users:read`, `users:read.email` scopes |
| `SLACK_SIGNING_SECRET` | Secret to verify incoming Slack interactivity webhook requests |
| `SLACK_WORKSPACE_INVITE_URL` | Invite link shown to users not yet in your Slack workspace |
| `SLACK_CHANNEL_ID` | Fallback Slack channel ID for general alerts |

*For complete instructions on configuring Slack, see the [Slack Setup Guide](slack_setup.md).*

---

## 3. Running the FastAPI Backend

With your virtual environment activated:

```bash
cd project/backend
uvicorn main:app --reload --port 8000
```

- **API Root**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 4. Running the Frontend Dashboard

You can run the React frontend either in development mode (hot-reloading) or by building static assets served directly by FastAPI.

### Option A: Development Mode (Hot Reloading)

In a separate terminal:

```bash
cd project/frontend
npm run dev
```

The Vite dev server runs at [http://localhost:5173](http://localhost:5173) and proxies API requests to port `8000`.

### Option B: Production Build (Served by FastAPI)

```bash
cd project/frontend
npm run build
```

This compiles the React SPA into `project/frontend/dist`. The FastAPI backend automatically serves this build at [http://localhost:8000](http://localhost:8000).

---

## 5. Running MCP Servers

The backend includes standalone Model Context Protocol (MCP) servers using FastMCP over stdio transport. You can run them individually for testing or connect them to AI agent orchestrators (Claude Desktop, Cursor, LangGraph):

From the repository root (with `PYTHONPATH=project/backend`):

```bash
# 1. LinkedIn Server (Post generation, polling, approval tools)
PYTHONPATH=project/backend python -m mcp_servers.linkedin_server

# 2. Database Server (Customer & post record query tool)
PYTHONPATH=project/backend python -m mcp_servers.database_server

# 3. Slack Server (Slack DM and channel notification tool)
PYTHONPATH=project/backend python -m mcp_servers.slack_server
```

*For more details on MCP tool definitions and schemas, see the [MCP Servers Guide](../project/backend/docs/mcp_servers.md).*

---

## 6. Running Tests

Run the test suite to verify everything is operating properly:

```bash
# Run unit & graph tests
uv run pytest project/backend/tests -k "not live"

# Verify frontend TypeScript build
cd project/frontend && npm run build
```
