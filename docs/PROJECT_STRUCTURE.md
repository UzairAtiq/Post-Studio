# Project Structure & Repository Reference

## Overview
This repository implements an automated, human-in-the-loop LinkedIn post creation and publishing pipeline. It combines:
- **Groq LLM**: Automated generation of technical and engaging LinkedIn posts.
- **LangGraph**: Agent workflow orchestration with modular state management and node transitions.
- **Model Context Protocol (MCP)**: Tool and context server endpoints (`db_server`, `slack_server`, `linkedin_post_generator`).
- **Slack Block Kit**: Interactive human review cards with interactive *Approve* and *Discard* actions.
- **FastAPI**: Backend REST API handling OAuth 2.0 handshakes, interactive Slack webhooks, and frontend endpoints.
- **React + Vite Dashboard**: Modern frontend UI for managing connections, generating posts, and reviewing histories.
- **PostgreSQL / SQLite**: Dual-mode persistence for token lifecycle management and post history.
- **Langfuse**: Observability and trace tracking for LLM executions.

---

## Directory Tree

```
mcp-langraph-agent/
├── README.md               # Main project documentation and quickstart
├── render.yaml             # Render Blueprint infrastructure-as-code configuration
├── .gitignore              # Git ignore rules
├── project/
│   ├── backend/            # Python backend service
│   │   ├── graphs/         # LangGraph post flow state graph, guards, nodes, and setup
│   │   ├── api/            # API compatibility wrappers and entry points
│   │   ├── config/         # Centralized configuration and constants
│   │   ├── data/           # Database models, token storage, and post storage
│   │   ├── mcp_servers/    # Model Context Protocol (MCP) server implementations
│   │   ├── observability/  # Langfuse tracing and monitoring setup
│   │   ├── services/       # External service integrations (Groq, LinkedIn, Slack)
│   │   ├── tests/          # Unit and integration test suite
│   │   ├── conftest.py     # Pytest configuration and sys.path setup
│   │   ├── main.py         # Core FastAPI application and primary server entry point
│   │   ├── Procfile        # Web process start command for cloud deployments
│   │   └── requirements.txt# Python dependency manifest
│   └── frontend/           # React + TypeScript + Vite + Tailwind dashboard
│       ├── src/            # Components, pages, context, and client API
│       ├── index.html      # Main dashboard HTML entry
│       ├── callback.html   # OAuth redirect handler template
│       ├── error.html      # OAuth failure template
│       ├── package.json    # Frontend dependencies and npm scripts
│       ├── tailwind.config.js # Tailwind CSS design configuration
│       └── vite.config.ts  # Vite build configuration and proxy setup
├── evals/                  # Benchmark evaluations (LLM-as-a-judge, test cases)
│   ├── dataset.json        # Evaluation benchmark scenarios and results
│   ├── dataset.py          # Benchmark test dataset definitions
│   ├── judge.py            # LLM-as-a-judge scoring model
│   └── run_evals.py        # Benchmark execution runner
├── docs/                   # System and setup documentation
│   ├── PROJECT_STRUCTURE.md# Repository architecture and directory map
│   ├── linkedin_setup.md   # LinkedIn developer app and OAuth setup
│   ├── slack_setup.md      # Slack app, webhook, and interactive cards setup
│   └── deployment.md       # Render cloud deployment and environment guide
└── _notes/                 # Development plans, prompts, and analysis
    ├── plans/              # Architecture and implementation plans
    ├── prompts/            # Agent and LLM prompt iterations
    └── graphify-out/       # Code knowledge graph outputs and reports
```

---

## Core Modules & Directory Breakdown

### 1. `project/backend/`
Contains the complete Python application, LangGraph workflow, MCP tool servers, and FastAPI REST endpoints.

| Directory / File | Description |
| :--- | :--- |
| `main.py` | **Primary FastAPI Entry Point**: Serves REST APIs (`/health`, `/posts`, `/generate`), handles OAuth callbacks, receives interactive Slack button payloads (`/slack/interactions`), mounts frontend static assets, and manages lifespan lifecycle for checkpointers and MCP clients. |
| `graphs/post_flow/` | **LangGraph Post Flow**: Production post generation & approval graph with human-in-the-loop interruption: state schema (`state.py`), guards (`guards.py`), setup & persistent checkpointer (`setup.py`), node implementations (`nodes.py`), and graph compiler (`graph.py`). |
| `legacy/` | **Legacy Flow Preserved**: Procedural post generation (`post_flow.py`), polling (`polling.py`), and Slack handling (`slack_flow.py`). |
| `services/` | **Centralized Services**: Single source of truth for post operations (`post_service.py`), graph workflow interface (`post_graph.py`), Slack messaging (`slack_service.py`), Groq LLM (`groq_service.py`), LinkedIn REST API (`linkedin_service.py`), and storage (`storage.py`). |
| `config/` | **Application Config**: Environment settings including `POST_FLOW_MODE` ("legacy" \| "graph"), base directory resolution, and constants (`constants.py`, `settings.py`). |
| `mcp_servers/` | **Model Context Protocol Servers**: Stdio tool servers for database queries (`db_server.py`), post lifecycle tools (`linkedin_server.py`), and Slack message dispatching (`slack_server.py`). Thin wrappers delegating directly to centralized services. |
| `observability/` | **Telemetry**: Langfuse tracing integration and callback handler setup (`tracing.py`). |
| `tests/` | **Automated Tests**: Unit and integration test suites (`test_post_graph.py`, `test_mcp_servers.py`, `test_linkedin_post_flow.py`). |
| `Procfile` | **Cloud Process Config**: Declares the web process command (`uvicorn main:app --host 0.0.0.0 --port $PORT`). |
| `requirements.txt` | **Dependencies**: Packages required to run the backend service. |

---

### 2. `project/frontend/`
Modern client dashboard built with React 18, Vite, TypeScript, and Tailwind CSS.
- **`src/pages/`**: `Dashboard.tsx` for real-time status and post triggering; `Posts.tsx` for post history.
- **`src/components/`**: Modular UI components (`ConnectionCard.tsx`, `GenerateCard.tsx`, `PostsCard.tsx`, `Sidebar.tsx`, `StatusBadge.tsx`, `ThemeToggle.tsx`).
- **`src/api.ts`**: Typed client methods communicating with backend endpoints.
- **`dist/`**: Compiled production bundle served directly by FastAPI.

---

### 3. `evals/`
Standalone evaluation benchmark suite maintained at the repository root.
- **`dataset.json`**: Evaluation test inputs, expected tool calls, and evaluation results.
- **`dataset.py`**: Evaluation test cases across scenarios (full flow, lookup only, slack only).
- **`judge.py`**: LLM-as-a-judge prompt evaluation that grades agent tool calls against expected behaviors.
- **`run_evals.py`**: Automated runner executing test cases and reporting scores.

---

### 4. `docs/`
Dedicated project documentation:
- **[`PROJECT_STRUCTURE.md`](file:///Users/uzair/Developer/mcp-langraph-agent/docs/PROJECT_STRUCTURE.md)**: Repository layout and architectural guide.
- **[`linkedin_setup.md`](file:///Users/uzair/Developer/mcp-langraph-agent/docs/linkedin_setup.md)**: LinkedIn Developer app registration, OAuth permissions, and token refresh guide.
- **[`slack_setup.md`](file:///Users/uzair/Developer/mcp-langraph-agent/docs/slack_setup.md)**: Slack App creation, webhook creation, interactive buttons, and ngrok tunneling.
- **[`deployment.md`](file:///Users/uzair/Developer/mcp-langraph-agent/docs/deployment.md)**: 1-click Render blueprint deployment, PostgreSQL database linking, and environment variables.

---

### 5. `_notes/`
Internal staging and planning directory:
- **`plans/`**: Architectural plans, migrations, and technical tasks.
- **`prompts/`**: LLM system prompts, iterations, and evaluation prompt templates.
- **`graphify-out/`**: Code knowledge graphs and dependency analysis maps.

---

## Architectural Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Frontend
    participant API as FastAPI (main.py)
    participant Graph as LangGraph Agent
    participant Groq as Groq LLM
    participant DB as Database (data/db.py)
    participant Slack as Slack Channel
    participant LI as LinkedIn API

    User->>API: Submit Topic (Generate Request)
    API->>Graph: Invoke Workflow (graphs/post_flow/graph.py)
    Graph->>Groq: Generate Post Content (services/groq_service.py)
    Groq-->>Graph: Draft Text
    Graph->>DB: Save Post (Status: PENDING)
    Graph->>Slack: Send Interactive Card (services/slack_service.py)
    Note over Slack: Slack user clicks "Approve" or "Discard"
    Slack->>API: POST /slack/interactions
    alt User Approved
        API->>DB: Update Status to APPROVED
        API->>LI: Publish Post (services/linkedin_service.py)
        LI-->>API: 201 Created (URN)
        API->>DB: Update Status to PUBLISHED
        API->>Slack: Update Card (Confirmation Banner)
    else User Discarded
        API->>DB: Update Status to DISCARDED
        API->>Slack: Update Card (Cancelled Banner)
    end
```
