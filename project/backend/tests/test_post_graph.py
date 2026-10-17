"""Automated tests for LangGraph post flow execution, interruption, persistence, and modes."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from api.main import app
from graphs.post_flow.graph import build_post_flow_graph
from graphs.post_flow.setup import PersistentCheckpointer
from services.post_graph import (
    resume_post_flow,
    set_post_graph_runtime,
    start_post_flow,
)
from services.storage import get_post_by_id


@pytest.fixture(autouse=True)
def reset_graph_runtime():
    """Ensure clean runtime state before and after each test."""
    set_post_graph_runtime(None, None, None)
    yield
    set_post_graph_runtime(None, None, None)


@pytest.mark.anyio
async def test_graph_approval_flow_success():
    """Test full happy path: generate -> pause at interrupt -> approve -> published."""
    with (
        patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_linkedin_person_urn", return_value="urn:li:person:mock_user"),
        patch("services.post_service.generate_linkedin_post_content", return_value="AI revolutionizes dev!"),
        patch("graphs.post_flow.nodes.generate_linkedin_post_content", return_value="COMPLETED"),
        patch("services.post_service.send_slack_approval_request", return_value={"success": True, "ts": "111.222"}),
        patch("services.post_service.publish_post_to_linkedin", return_value={"success": True, "post_urn": "urn:li:share:999"}),
        patch("services.post_service.update_slack_message", return_value={"ok": True}),
    ):
        start_res = await start_post_flow(topic="AI engineering")
        assert start_res["success"] is True
        assert start_res["status"] == "pending_approval"
        post_id = start_res["post_id"]
        assert post_id.startswith("post_")
        assert start_res["generated_content"] == "AI revolutionizes dev!"

        # Resume with human approval
        resume_res = await resume_post_flow(post_id=post_id, decision="approved")
        assert resume_res["status"] == "published"
        assert resume_res["is_complete"] is True

        stored = get_post_by_id(post_id)
        assert stored is not None
        assert stored["status"] == "published"


@pytest.mark.anyio
async def test_graph_rejection_flow_discarded():
    """Test rejection path: generate -> pause at interrupt -> reject -> discarded."""
    with (
        patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_linkedin_person_urn", return_value="urn:li:person:mock_user"),
        patch("services.post_service.generate_linkedin_post_content", return_value="Draft post for reject"),
        patch("graphs.post_flow.nodes.generate_linkedin_post_content", return_value="COMPLETED"),
        patch("services.post_service.send_slack_approval_request", return_value={"success": True, "ts": "111.222"}),
        patch("services.post_service.update_slack_message", return_value={"ok": True}),
    ):
        start_res = await start_post_flow(topic="Discard test")
        post_id = start_res["post_id"]

        # Resume with discard decision
        resume_res = await resume_post_flow(post_id=post_id, decision="discard")
        assert resume_res["status"] == "discarded"
        assert resume_res["is_complete"] is True

        stored = get_post_by_id(post_id)
        assert stored is not None
        assert stored["status"] == "discarded"


@pytest.mark.anyio
async def test_graph_persistence_across_process_reboot():
    """Test that interrupted graph states persist to disk and resume cleanly after reboot."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        checkpoint_file = Path(tmp_dir) / "checkpoints.pkl"

        with (
            patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
            patch("services.post_service.get_valid_linkedin_access_token", return_value="mock_token"),
            patch("services.post_service.get_linkedin_person_urn", return_value="urn:li:person:mock_user"),
            patch("services.post_service.generate_linkedin_post_content", return_value="Reboot test post"),
            patch("graphs.post_flow.nodes.generate_linkedin_post_content", return_value="COMPLETED"),
            patch("services.post_service.send_slack_approval_request", return_value={"success": True, "ts": "333.444"}),
            patch("services.post_service.publish_post_to_linkedin", return_value={"success": True, "post_urn": "urn:li:share:888"}),
            patch("services.post_service.update_slack_message", return_value={"ok": True}),
        ):
            # Phase 1: Start on initial server instance
            cp1 = PersistentCheckpointer(file_path=checkpoint_file)
            graph1 = build_post_flow_graph(checkpointer=cp1)
            set_post_graph_runtime(graph=graph1, mcp_client=None, checkpointer=cp1)

            start_res = await start_post_flow(topic="Reboot resilience")
            post_id = start_res["post_id"]
            assert checkpoint_file.exists()

            # Phase 2: Simulate complete server reboot by creating a fresh instance from disk
            set_post_graph_runtime(None, None, None)
            cp2 = PersistentCheckpointer(file_path=checkpoint_file)
            graph2 = build_post_flow_graph(checkpointer=cp2)
            set_post_graph_runtime(graph=graph2, mcp_client=None, checkpointer=cp2)

            # Phase 3: Resume post flow using restored graph
            resume_res = await resume_post_flow(post_id=post_id, decision="approved")
            assert resume_res["status"] == "published"
            assert resume_res["is_complete"] is True

            stored = get_post_by_id(post_id)
            assert stored is not None
            assert stored["status"] == "published"


@pytest.mark.anyio
async def test_graph_failure_when_unauthenticated():
    """Test start_post_flow raises HTTP 401 when LinkedIn token is missing."""
    with patch("services.post_graph.get_valid_linkedin_access_token", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await start_post_flow(topic="Unauthenticated test")
        assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_graph_failure_when_groq_fails():
    """Test graceful handling when LLM generation fails."""
    with (
        patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.generate_linkedin_post_content", side_effect=RuntimeError("Groq 500 error")),
        patch("graphs.post_flow.nodes.notify_failure") as mock_notify,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await start_post_flow(topic="Failing LLM")
        assert exc_info.value.status_code in (500, 502)
        assert mock_notify.called


@pytest.mark.anyio
async def test_graph_failure_when_slack_dispatch_fails():
    """Test failure when Slack approval request cannot be sent."""
    with (
        patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_linkedin_person_urn", return_value="urn:li:person:mock_user"),
        patch("services.post_service.generate_linkedin_post_content", return_value="Post content"),
        patch("services.post_service.send_slack_approval_request", return_value={"success": False, "error": "Slack down"}),
        patch("graphs.post_flow.nodes.notify_failure") as mock_notify,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await start_post_flow(topic="Failing Slack")
        assert exc_info.value.status_code in (500, 502)
        assert mock_notify.called


@pytest.mark.anyio
async def test_graph_publish_failure_flow():
    """Test handling when publishing to LinkedIn fails during resume."""
    with (
        patch("services.post_graph.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_valid_linkedin_access_token", return_value="mock_token"),
        patch("services.post_service.get_linkedin_person_urn", return_value="urn:li:person:mock_user"),
        patch("services.post_service.generate_linkedin_post_content", return_value="Valid post"),
        patch("graphs.post_flow.nodes.generate_linkedin_post_content", return_value="COMPLETED"),
        patch("services.post_service.send_slack_approval_request", return_value={"success": True, "ts": "123"}),
        patch("services.post_service.publish_post_to_linkedin", side_effect=Exception("LinkedIn API error")),
        patch("graphs.post_flow.nodes.notify_failure") as mock_notify,
    ):
        start_res = await start_post_flow(topic="Publish error test")
        post_id = start_res["post_id"]

        resume_res = await resume_post_flow(post_id=post_id, decision="approved")
        assert resume_res["status"] == "publish_failed"
        assert resume_res["is_complete"] is False
        assert mock_notify.called


def test_endpoint_mode_switching_legacy():
    """Test /posts/generate endpoint executes legacy flow when POST_FLOW_MODE=legacy."""
    client = TestClient(app)
    with (
        patch("routers.posts.POST_FLOW_MODE", "legacy"),
        patch("legacy.post_flow.run_legacy_post_flow", return_value={"success": True, "mode": "legacy"}),
    ):
        resp = client.post("/posts/generate?topic=Testing")
        assert resp.status_code == 200
        assert resp.json().get("mode") == "legacy"


def test_endpoint_mode_switching_graph():
    """Test /posts/generate endpoint executes graph flow when POST_FLOW_MODE=graph."""
    client = TestClient(app)
    async def mock_start(topic: str = "", *args, **kwargs):
        return {"success": True, "mode": "graph", "post_id": "post_mock"}

    with (
        patch("routers.posts.POST_FLOW_MODE", "graph"),
        patch("services.post_graph.start_post_flow", side_effect=mock_start),
    ):
        resp = client.post("/posts/generate?topic=Testing")
        assert resp.status_code == 200
        assert resp.json().get("mode") == "graph"


def test_endpoint_custom_llm_headers():
    """Test /posts/generate forwards custom LLM headers in llm_config."""
    client = TestClient(app)
    captured_config = {}

    async def mock_start(topic: str = "", **kwargs):
        captured_config.update(kwargs.get("llm_config") or {})
        return {"success": True, "mode": "graph", "post_id": "post_mock"}

    with (
        patch("routers.posts.POST_FLOW_MODE", "graph"),
        patch("services.post_graph.start_post_flow", side_effect=mock_start),
    ):
        headers = {
            "X-LLM-API-Key": "test-key-123",
            "X-LLM-Base-URL": "https://api.together.xyz/v1",
            "X-LLM-Model": "meta-llama/Llama-3-70b-chat-hf",
        }
        resp = client.post("/posts/generate?topic=Testing", headers=headers)
        assert resp.status_code == 200
        assert captured_config.get("api_key") == "test-key-123"
        assert captured_config.get("base_url") == "https://api.together.xyz/v1"
        assert captured_config.get("model") == "meta-llama/Llama-3-70b-chat-hf"


@pytest.mark.anyio
async def test_lifespan_resource_cleanup():
    """Test that lifespan startup initializes graph and shutdown closes resources in try/finally."""
    from main import lifespan

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.close = AsyncMock()
    mock_checkpointer = MagicMock()
    mock_checkpointer.close = MagicMock()

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("main.POST_FLOW_MODE", "graph"),
        patch("graphs.post_flow.setup.create_mcp_client", return_value=mock_client),
        patch("graphs.post_flow.setup.create_checkpointer", return_value=mock_checkpointer),
        patch("graphs.post_flow.graph.build_post_flow_graph", return_value=MagicMock()),
        patch("services.post_graph.set_post_graph_runtime") as mock_set_runtime,
    ):
        async with lifespan(mock_app):
            assert mock_client.connect.called
            assert mock_set_runtime.called

        # After lifespan exits, verify teardown in finally block
        assert mock_client.close.called
        assert mock_checkpointer.close.called

