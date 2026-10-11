"""LangGraph workflow definition with fixed nodes, edges, checkpointer, and interruption."""

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from graphs.post_flow.nodes import (
    discard_node,
    final_check_node,
    generate_node,
    publish_node,
    request_approval_node,
    save_node,
)
from graphs.post_flow.state import PostFlowState

logger = logging.getLogger("post_flow_graph")

# Step limit preventing infinite execution loops
POST_FLOW_RECURSION_LIMIT: int = 25


def route_after_decision(state: PostFlowState) -> str:
    """Route workflow to publication or disposal based on verified human decision.

    Purpose:
        Enforces human approval requirement: routes to publish ONLY if decision is approved.
        Any other decision (or missing approval) routes to discard.

    Args:
        state: Current post flow state containing human decision.

    Returns:
        str: Next node identifier ('publish' or 'discard').
    """
    decision = str(state.get("decision", "")).strip().lower()
    if decision in ("approved", "approve_linkedin_post"):
        return "publish"
    return "discard"


def build_post_flow_graph(checkpointer: Any = None) -> CompiledStateGraph:
    """Compile fixed post flow graph with checkpointer and human-in-the-loop interruption.

    Purpose:
        Constructs and compiles the post generation, storage, approval, and publishing graph:
        generate -> save -> request_approval (Slack) -> interrupt -> publish | discard -> final_check -> END

    Args:
        checkpointer: State checkpointer for thread persistence across interruptions and reboots.

    Returns:
        CompiledStateGraph: Compiled LangGraph executable graph.
    """
    builder = StateGraph(PostFlowState)

    # Register workflow nodes
    builder.add_node("generate", generate_node)
    builder.add_node("save", save_node)
    builder.add_node("request_approval", request_approval_node)
    builder.add_node("publish", publish_node)
    builder.add_node("discard", discard_node)
    builder.add_node("final_check", final_check_node)

    # Establish linear and conditional edges
    builder.add_edge(START, "generate")
    builder.add_edge("generate", "save")
    builder.add_edge("save", "request_approval")
    builder.add_conditional_edges(
        "request_approval",
        route_after_decision,
        ["publish", "discard"],
    )
    builder.add_edge("publish", "final_check")
    builder.add_edge("discard", "final_check")
    builder.add_edge("final_check", END)

    compiled = builder.compile(checkpointer=checkpointer)
    logger.info("Compiled post flow LangGraph with checkpointer and fixed structure.")
    return compiled
