import sys
import time
from pathlib import Path

# ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient
from api.main import app
from config.settings import get_linkedin_access_token
from services.storage import (
    generate_next_post_id,
    get_post_by_id,
    save_post_record,
    update_post_status,
)
from mcp_servers.linkedin_server import get_post_details
from services.approval import execute_post_decision
from services.groq_service import generate_linkedin_post_content
from services.linkedin_service import fetch_linkedin_person_urn
from services.slack_service import (
    build_approval_blocks,
    send_approval_request,
)


# test storage functions lifecycle
def test_storage_lifecycle():
    post_id = generate_next_post_id()
    assert post_id.startswith("post_")

    record = save_post_record(
        post_id=post_id,
        content="Excited to share insights on AI agents!",
        profile_id="urn:li:person:test_user_123",
        access_token="test_token_xyz",
        status="pending",
    )
    assert record["post_id"] == post_id
    assert record["status"] == "pending"

    fetched = get_post_by_id(post_id)
    assert fetched is not None
    assert fetched["content"] == "Excited to share insights on AI agents!"

    updated = update_post_status(post_id, "published", extra_data={"urn": "urn:li:share:999"})
    assert updated["status"] == "published"
    assert updated["urn"] == "urn:li:share:999"


# test slack block kit structure
def test_slack_block_structure():
    post_id = "post_100_abc"
    content = "Sample LinkedIn post body"
    blocks = build_approval_blocks(post_id=post_id, content=content)

    assert len(blocks) == 4
    action_block = blocks[3]
    assert action_block["type"] == "actions"
    elements = action_block["elements"]
    assert len(elements) == 2

    approve_button = elements[0]
    assert approve_button["action_id"] == "approve_linkedin_post"
    assert approve_button["value"] == post_id

    discard_button = elements[1]
    assert discard_button["action_id"] == "discard_linkedin_post"
    assert discard_button["value"] == post_id


# test api health and posts endpoints
def test_api_endpoints():
    client = TestClient(app)

    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json().get("status") == "healthy"

    posts_res = client.get("/posts")
    assert posts_res.status_code == 200
    data = posts_res.json()
    assert "total_count" in data
    assert "posts" in data


# single unified live test: generate post, send to slack, wait for human decision, execute action, and save to storage
def test_live_slack_approval_and_publish_flow(timeout_seconds: int = 120) -> dict:
    topic = "AI Agents and Model Context Protocol in Production"
    print(f"\n[Live Flow] Generating LinkedIn post about: '{topic}'...")

    # step 1: resolve linkedin token and person urn
    access_token = get_linkedin_access_token()
    if not access_token:
        raise ValueError("LinkedIn access token not found. Please set LINKEDIN_ACCESS_TOKEN in .env or .linkedin_token.")

    person_urn = fetch_linkedin_person_urn(access_token=access_token)

    # step 2: generate post content with groq llm and unique identifier
    post_content = generate_linkedin_post_content(topic=topic)
    post_id = generate_next_post_id()

    # step 3: persist pending post record to storage so webhook and decision handlers can retrieve it
    save_post_record(
        post_id=post_id,
        content=post_content,
        profile_id=person_urn,
        access_token=access_token,
        status="pending",
    )

    # step 4: send block kit approval request with yes/no interactive buttons to slack
    slack_delivery = send_approval_request(
        post_id=post_id,
        content=post_content,
    )
    assert slack_delivery.get("success") is True, f"slack delivery failed: {slack_delivery.get('error')}"

    print(f"  ✓ Post `{post_id}` generated and delivered to Slack.")
    print(f"  👉 Please check your Slack channel and click either:")
    print(f"     • '✅ Yes (Approve & Post)' -> publishes live to LinkedIn")
    print(f"     • '❌ No (Discard)'          -> marks as rejected and updates storage")
    print(f"  ⏳ Waiting up to {timeout_seconds} seconds for your button click...\n")

    # step 5: poll storage for real human interaction received from slack webhook
    start_time = time.time()
    poll_interval_seconds = 2.0
    detected_status = None

    while time.time() - start_time < timeout_seconds:
        current_record = get_post_by_id(post_id)
        if current_record:
            status_value = current_record.get("status")
            if status_value in ("approved", "discarded", "published", "rejected", "publish_failed"):
                detected_status = status_value
                break
        time.sleep(poll_interval_seconds)

    assert detected_status is not None, f"timed out after {timeout_seconds}s waiting for Slack button click"
    print(f"  ✓ Slack response detected: '{detected_status}'")

    # step 6: pass post_id to execute_post_decision to retrieve post from storage and execute publish or discard
    decision_result = execute_post_decision(post_id=post_id)
    assert decision_result.get("success") is True, f"execution failed: {decision_result.get('error')}"

    # step 7: retrieve finalized post record from storage and report outcome
    final_post_details = get_post_details(post_id)
    assert final_post_details.get("found") is True
    final_record = final_post_details["post"]
    final_status = final_record.get("status")

    if final_status == "published":
        response_payload = final_record.get("linkedin_response", {})
        post_urn = response_payload.get("post_urn", "unknown")
        print(f"  🎉 Post `{post_id}` was APPROVED and published to LinkedIn! (URN: {post_urn})")
    elif final_status == "rejected":
        print(f"  🛑 Post `{post_id}` was DISCARDED and marked as rejected in database.")
    elif final_status == "publish_failed":
        print(f"  ⚠️ Post `{post_id}` was approved but publishing to LinkedIn failed.")
    else:
        print(f"  ℹ️ Post `{post_id}` finalized with status: '{final_status}'")

    return final_post_details


if __name__ == "__main__":
    print("Running LinkedIn post flow unit tests...")

    print("\n1. Testing storage lifecycle...")
    test_storage_lifecycle()
    print("   ✓ test_storage_lifecycle passed")

    print("\n2. Testing Slack block structure...")
    test_slack_block_structure()
    print("   ✓ test_slack_block_structure passed")

    print("\n3. Testing API endpoints...")
    test_api_endpoints()
    print("   ✓ test_api_endpoints passed")

    print("\n🎉 All LinkedIn post flow unit tests passed successfully!")
    print("\nExecuting live interactive Slack approval and publish flow...")
    test_live_slack_approval_and_publish_flow()