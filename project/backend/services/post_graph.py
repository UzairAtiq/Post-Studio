"""Service interface for LangGraph post generation and approval workflow."""

import logging
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Response, status
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from config.settings import get_linkedin_person_urn
from graphs.post_flow.graph import POST_FLOW_RECURSION_LIMIT, build_post_flow_graph
from graphs.post_flow.setup import (
    GraphMCPClient,
    PersistentCheckpointer,
    create_checkpointer,
)
from services.approval import record_slack_decision
from services.post_service import notify_failure
from services.storage import (
    generate_next_post_id,
    get_valid_linkedin_access_token,
)

logger = logging.getLogger("post_graph_service")

# Global singleton storage for graph runtime components
_graph: CompiledStateGraph | None = None
_mcp_client: GraphMCPClient | None = None
_checkpointer: PersistentCheckpointer | None = None


def set_post_graph_runtime(
    graph: CompiledStateGraph | None = None,
    mcp_client: GraphMCPClient | None = None,
    checkpointer: PersistentCheckpointer | None = None,
) -> None:
    """Register active runtime graph, MCP client, and checkpointer."""
    global _graph, _mcp_client, _checkpointer
    _graph = graph
    _mcp_client = mcp_client
    _checkpointer = checkpointer


def get_post_graph_runtime() -> tuple[CompiledStateGraph, GraphMCPClient | None, PersistentCheckpointer]:
    """Retrieve active runtime components or lazily initialize defaults."""
    global _graph, _mcp_client, _checkpointer
    if _checkpointer is None:
        _checkpointer = create_checkpointer()
    if _graph is None:
        _graph = build_post_flow_graph(checkpointer=_checkpointer)
    return _graph, _mcp_client, _checkpointer


async def start_post_flow(
    topic: str = "AI advancements and modern software engineering",
    post_id: str | None = None,
    user_sub: str | None = None,
) -> dict[str, Any]:
    """Execute LangGraph post generation flow until human approval interruption."""
    token_id = user_sub or "default"
    token = get_valid_linkedin_access_token(token_id=token_id)
    if not token and token_id != "default":
        token = get_valid_linkedin_access_token(token_id="default")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LinkedIn is not authenticated. Please visit /linkedin/login first.",
        )

    resolved_post_id = post_id or generate_next_post_id()
    graph, mcp_client, _ = get_post_graph_runtime()

    config = {
        "configurable": {
            "thread_id": resolved_post_id,
            "mcp_client": mcp_client,
        },
        "recursion_limit": POST_FLOW_RECURSION_LIMIT,
    }

    try:
        result = await graph.ainvoke(
            {
                "topic": topic,
                "post_id": resolved_post_id,
            },
            config=config,
        )
    except Exception as err:
        logger.error(f"start_post_flow failed for post_id={resolved_post_id}: {err}")
        notify_failure(post_id=resolved_post_id, node="start_flow", reason=str(err))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Post flow execution failed: {err}",
        )

    if result.get("status") == "failed":
        err_msg = result.get("error", "Post generation failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=err_msg,
        )

    slack_delivery = None
    interrupts = result.get("__interrupt__")
    if interrupts:
        first_interrupt = interrupts[0]
        if hasattr(first_interrupt, "value") and isinstance(first_interrupt.value, dict):
            slack_delivery = first_interrupt.value.get("slack_delivery")
        elif isinstance(first_interrupt, dict):
            slack_delivery = first_interrupt.get("value", {}).get("slack_delivery")

    return {
        "success": True,
        "post_id": resolved_post_id,
        "person_urn": get_linkedin_person_urn(),
        "status": "pending_approval",
        "generated_content": result.get("content", ""),
        "slack_delivery": slack_delivery,
        "message": "Post generated and sent to Slack for user approval.",
    }


async def resume_post_flow(
    post_id: str,
    decision: str,
) -> dict[str, Any]:
    """Resume paused graph workflow execution with human approval decision.

    Purpose:
        Passes user decision into graph interruption, runs publishing or disposal,
        and executes final completion check.

    Args:
        post_id: Unique thread identifier matching post record.
        decision: Decision value ('approve', 'approved', 'discard', etc.).

    Returns:
        dict[str, Any]: Final graph state dictionary.

    Raises:
        Exception: If graph resume execution fails.
    """
    graph, mcp_client, _ = get_post_graph_runtime()

    config = {
        "configurable": {
            "thread_id": post_id,
            "mcp_client": mcp_client,
        },
        "recursion_limit": POST_FLOW_RECURSION_LIMIT,
    }

    try:
        result = await graph.ainvoke(
            Command(resume=decision),
            config=config,
        )
        logger.info(f"resume_post_flow completed for post_id={post_id}: status={result.get('status')}")
        return result
    except Exception as err:
        logger.error(f"resume_post_flow failed for post_id={post_id}: {err}")
        notify_failure(post_id=post_id, node="resume", reason=str(err))
        raise


def process_graph_slack_interaction(
    interaction_data: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> Response:
    """Process Slack Block Kit interactive actions for graph post flow.

    Purpose:
        Extracts action metadata, records decision in storage for Slack card refresh,
        schedules background resume_post_flow execution, and returns fast HTTP 200.

    Args:
        interaction_data: Parsed Slack interaction payload dictionary.
        background_tasks: FastAPI background tasks queue.

    Returns:
        Response: HTTP 200 OK acknowledgment.
    """
    actions = interaction_data.get("actions", [])
    if not actions:
        logger.warning("received Slack interaction without actions")
        return Response(status_code=status.HTTP_200_OK)

    primary_action = actions[0]
    action_id = primary_action.get("action_id", "")
    post_id = primary_action.get("value", "")
    response_url = interaction_data.get("response_url")

    logger.info(f"processing graph Slack action: '{action_id}' for post_id: '{post_id}'")

    if not post_id:
        return Response(status_code=status.HTTP_200_OK)

    # Persist decision and response_url for Slack card updating
    record_slack_decision(
        action_id=action_id,
        post_id=post_id,
        response_url=response_url,
    )

    # Enqueue resume execution in background tasks to meet 3-second deadline
    background_tasks.add_task(
        resume_post_flow,
        post_id=post_id,
        decision=action_id,
    )

    return Response(status_code=status.HTTP_200_OK)
