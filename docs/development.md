# Development & Testing Guide

This guide covers running the LangGraph agent directly via CLI, running backend tests, and executing evaluation benchmarks.

---

## 1. Run LangGraph Agent

To run the LangGraph agent workflow directly:

```bash
# Activate virtual environment
source .venv/bin/activate

# Run from backend directory
cd project/backend
python -m agent.graph
```

This triggers the state graph to generate a post via Groq LLM, save it to the database with `pending` status, and send an interactive approval card to Slack.

---

## 2. Running Tests

### Unit Tests

Run the test suite across agent nodes, tools, and MCP servers:

```bash
# Run post graph tests
pytest project/backend/tests/test_post_graph.py

# Run MCP server tests
pytest project/backend/tests/test_mcp_servers.py

# Run all backend tests
pytest project/backend/tests/
```

---

## 3. Running Evaluations

To run benchmark evaluation suites or LLM-as-a-judge tests (when configured):

```bash
python evals/run_evals.py
```
