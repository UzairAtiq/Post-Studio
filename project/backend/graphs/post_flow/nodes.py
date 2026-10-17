"""Node implementations for post generation, human approval, publishing, and final checks."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from graphs.post_flow.guards import can_publish_post, is_flow_complete
from graphs.post_flow.state import PostFlowState
from mcp_servers.linkedin_server import (
    discard_post as discard_post_tool,
    generate_post as generate_post_tool,
    get_post as get_post_tool,
    publish_post as publish_post_tool,
    request_approval as request_approval_tool,
    save_post as save_post_tool,
)
from services.groq_service import generate_linkedin_post_content
from services.post_service import notify_failure
from services.storage import get_post_by_id

logger = logging.getLogger("post_flow_nodes")


async def _execute_mcp_tool(
    name: str,
    args: dict[str, Any],
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool via active client session or direct tool fallback.

    Args:
        name: Name of tool to execute.
        args: Tool arguments dictionary.
        config: Runnable configuration optionally containing mcp_client.

    Returns:
        dict[str, Any]: Result dictionary returned by MCP tool execution.
    """
    configurable = (config or {}).get("configurable", {})
    client = configurable.get("mcp_client")

    if client and getattr(client, "_is_connected", False):
        try:
            return await client.call_tool(name, args)
        except Exception as client_err:
            logger.warning(f"MCP client call '{name}' failed, falling back to direct tool: {client_err}")

    # Fallback to direct tool call
    tool_map = {
        "generate_post": generate_post_tool,
        "save_post": save_post_tool,
        "request_approval": request_approval_tool,
        "publish_post": publish_post_tool,
        "discard_post": discard_post_tool,
        "get_post": get_post_tool,
    }
    tool_func = tool_map.get(name)
    if not tool_func:
        raise ValueError(f"Unknown MCP tool: {name}")
    return tool_func(**args)


async def generate_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Generate technical post content using Groq LLM via MCP tool.

    Purpose:
        Requests draft content for topic using generate_post MCP tool.

    Steps:
        1. Resolve topic from state or default.
        2. Execute generate_post MCP tool.
        3. If failed, notify failure to Slack and record failed status.
        4. Return generated content and status.

    Returns:
        dict[str, Any]: State update with content or failure details.
    """
    topic = state.get("topic") or "AI advancements and modern software engineering"
    post_id = state.get("post_id", "")
    try:
        res = await _execute_mcp_tool("generate_post", {"topic": topic}, config)
        if not res.get("success"):
            err_msg = res.get("error", "Failed generating post content")
            logger.error(f"generate node failed: {err_msg}")
            notify_failure(post_id=post_id, node="generate", reason=err_msg)
            return {"status": "failed", "error": err_msg}

        return {
            "content": res.get("content", ""),
            "status": "generated",
        }
    except Exception as err:
        err_msg = f"Unexpected exception in generate node: {err}"
        logger.error(err_msg)
        notify_failure(post_id=post_id, node="generate", reason=str(err))
        return {"status": "failed", "error": str(err)}


async def save_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Persist generated post into persistent storage with pending status.

    Purpose:
        Executes save_post MCP tool to obtain post_id and save record.

    Steps:
        1. Extract content from state.
        2. Execute save_post MCP tool.
        3. If failed, notify failure to Slack and record failed status.
        4. Return post_id and pending status.

    Returns:
        dict[str, Any]: State update with post_id and pending status.
    """
    if state.get("status") == "failed":
        return {"status": "failed", "error": state.get("error", "Generation step failed")}

    content = state.get("content", "")
    existing_post_id = state.get("post_id", "")
    owner_sub = state.get("user_sub") or state.get("owner_sub", "")
    try:
        res = await _execute_mcp_tool(
            "save_post",
            {"content": content, "post_id": existing_post_id, "owner_sub": owner_sub or ""},
            config,
        )
        if not res.get("success"):
            err_msg = res.get("error", "Failed saving post to storage")
            logger.error(f"save node failed: {err_msg}")
            notify_failure(post_id=existing_post_id, node="save", reason=err_msg)
            return {"status": "failed", "error": err_msg}

        post_id = res.get("post_id", existing_post_id)
        return {
            "post_id": post_id,
            "status": "pending",
        }
    except Exception as err:
        err_msg = f"Unexpected exception in save node: {err}"
        logger.error(err_msg)
        notify_failure(post_id=existing_post_id, node="save", reason=str(err))
        return {"status": "failed", "error": str(err)}


async def request_approval_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Send Slack approval card and interrupt execution awaiting human decision.

    Purpose:
        Dispatches Slack Block Kit card, then pauses graph execution until
        a human reviewer provides an approved or discarded decision.

    Steps:
        1. Call request_approval MCP tool if not already dispatched.
        2. If delivery failed, notify failure and record failed status.
        3. Call interrupt to pause workflow for human input.
        4. Upon resume, normalize decision string and return.

    Returns:
        dict[str, Any]: State update with human decision and delivery metadata.
    """
    if state.get("status") == "failed":
        return {"status": "failed", "error": state.get("error", "Previous step failed")}

    post_id = state.get("post_id", "")
    content = state.get("content", "")

    try:
        # Check whether Slack approval has already been delivered to avoid duplicate dispatch on resume
        slack_delivery = state.get("slack_delivery")
        record = get_post_by_id(post_id) if post_id else None
        if not slack_delivery and record:
            slack_delivery = record.get("slack_delivery")

        if not slack_delivery and not (record and record.get("decision_action")):
            res = await _execute_mcp_tool(
                "request_approval",
                {"post_id": post_id, "content": content},
                config,
            )
            if not res.get("success"):
                err_msg = res.get("error", "Failed dispatching Slack approval request")
                logger.error(f"request_approval node failed for post_id={post_id}: {err_msg}")
                notify_failure(post_id=post_id, node="request_approval", reason=err_msg)
                return {"status": "failed", "error": err_msg}
            slack_delivery = res.get("slack_delivery")

        # Interrupt execution awaiting human Slack decision
        user_decision = interrupt(
            {
                "post_id": post_id,
                "message": "Waiting for Slack human approval",
                "slack_delivery": slack_delivery,
            }
        )

        # Normalize decision string (handle string or dictionary payload from resume)
        raw_decision = user_decision
        if isinstance(raw_decision, dict):
            raw_decision = raw_decision.get("action_id") or raw_decision.get("decision") or raw_decision.get("action")
        norm_decision = str(raw_decision or state.get("decision", "")).strip().lower()
        if norm_decision in ("approve_linkedin_post", "approve_post", "approve", "yes", "approved"):
            clean_decision = "approved"
        else:
            clean_decision = "discarded"

        return {
            "decision": clean_decision,
            "status": "decision_received",
            "slack_delivery": slack_delivery,
        }
    except GraphInterrupt:
        raise
    except Exception as err:
        err_msg = f"Unexpected exception in request_approval node: {err}"
        logger.error(f"request_approval failed for post_id={post_id}: {err_msg}")
        notify_failure(post_id=post_id, node="request_approval", reason=str(err))
        return {"status": "failed", "error": str(err)}


async def publish_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Publish approved post to LinkedIn via MCP tool.

    Purpose:
        Validates approval and idempotency, then executes publish_post MCP tool.

    Steps:
        1. Verify publish pre-conditions using guards.can_publish_post.
        2. Execute publish_post MCP tool.
        3. If failed, notify failure and update status to publish_failed.
        4. Return publication result and published status.

    Returns:
        dict[str, Any]: State update with publication details.
    """
    if state.get("status") == "failed":
        return {"status": "publish_failed", "error": state.get("error", "Previous step failed")}

    post_id = state.get("post_id", "")

    # Check eligibility to prevent unauthorized or double posting
    if not can_publish_post(state):
        record = get_post_by_id(post_id)
        if record and record.get("status") in ("published", "discarded"):
            return {
                "status": record.get("status"),
                "linkedin_response": record.get("linkedin_response"),
            }
        logger.warning(f"publish_node rejected: post_id={post_id} not eligible for publishing.")
        return {"status": "publish_failed", "error": "Post not approved for publishing."}

    try:
        res = await _execute_mcp_tool("publish_post", {"post_id": post_id}, config)
        if not res.get("success"):
            err_msg = res.get("error", "Publish failed")
            logger.error(f"publish node failed for post_id={post_id}: {err_msg}")
            notify_failure(post_id=post_id, node="publish", reason=err_msg)
            return {"status": "publish_failed", "error": err_msg}

        return {
            "status": "published",
            "linkedin_response": res.get("linkedin_response") or res.get("publish_result"),
        }
    except Exception as err:
        err_msg = f"Unexpected exception in publish node: {err}"
        logger.error(f"publish failed for post_id={post_id}: {err_msg}")
        notify_failure(post_id=post_id, node="publish", reason=str(err))
        return {"status": "publish_failed", "error": str(err)}


async def discard_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Mark post as discarded via MCP tool.

    Purpose:
        Executes discard_post MCP tool to dispose of rejected post.

    Steps:
        1. Execute discard_post MCP tool.
        2. If failed, notify failure and record error.
        3. Return discarded status.

    Returns:
        dict[str, Any]: State update with discarded status.
    """
    if state.get("status") == "failed":
        return {"status": "failed", "error": state.get("error", "Previous step failed")}

    post_id = state.get("post_id", "")
    try:
        res = await _execute_mcp_tool("discard_post", {"post_id": post_id}, config)
        if not res.get("success"):
            err_msg = res.get("error", "Discard failed")
            logger.error(f"discard node failed for post_id={post_id}: {err_msg}")
            notify_failure(post_id=post_id, node="discard", reason=err_msg)
            return {"status": "failed", "error": err_msg}

        return {"status": "discarded"}
    except Exception as err:
        err_msg = f"Unexpected exception in discard node: {err}"
        logger.error(f"discard failed for post_id={post_id}: {err_msg}")
        notify_failure(post_id=post_id, node="discard", reason=str(err))
        return {"status": "failed", "error": str(err)}


async def final_check_node(
    state: PostFlowState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Agentic decision node evaluating completion with LLM review and strict guard checks.

    Purpose:
        Prompts an LLM to assess whether the flow reached a complete terminal state,
        then enforces guards.is_flow_complete as the definitive final authority.

    Steps:
        1. Inspect state status, decision, and linkedin_response.
        2. Query LLM to evaluate workflow completion.
        3. Evaluate deterministic guard via guards.is_flow_complete.
        4. Guard overrides LLM: is_complete is set strictly according to guard.
        5. Return evaluation notes and final is_complete boolean.

    Returns:
        dict[str, Any]: State update with is_complete flag and review notes.
    """
    status_val = state.get("status", "")
    decision_val = state.get("decision", "")
    post_id = state.get("post_id", "")

    # LLM agent evaluation
    eval_notes = f"Terminal evaluation for post_id={post_id}: status={status_val}, decision={decision_val}"
    try:
        eval_prompt = (
            f"Review the following post workflow outcome and confirm if complete:\n"
            f"- Post ID: {post_id}\n"
            f"- Status: {status_val}\n"
            f"- Decision: {decision_val}\n"
            f"Is the workflow concluded successfully? State COMPLETED or INCOMPLETE."
        )
        llm_response = generate_linkedin_post_content(topic=eval_prompt)
        eval_notes = f"{eval_notes} | LLM verdict: {llm_response[:60]}"
    except Exception as llm_err:
        logger.info(f"LLM final check evaluation skipped or unavailable: {llm_err}")
        eval_notes = f"{eval_notes} | LLM skipped"

    # Guards are the final authority: LLM may not override guard
    complete = is_flow_complete(state)
    logger.info(f"final_check guard decision for post_id={post_id}: is_complete={complete}")

    return {
        "is_complete": complete,
        "final_check_notes": eval_notes,
    }
