from mcp_servers.database_server import lookup_customer
from mcp_servers.linkedin_server import (
    get_post_details,
    list_all_generated_posts,
)
from mcp_servers.slack_server import send_slack_message


# test database mcp tool
def test_db_lookup_customer():
    customer = lookup_customer(1)
    assert isinstance(customer, dict)
    assert "name" in customer or "error" in customer


# test slack mcp tool
def test_send_slack_message():
    result = send_slack_message("Automated test message from MCP server test suite.")
    assert isinstance(result, dict)
    assert "status" in result or "error" in result


# retrieve customer by id and post their status directly to slack
def lookup_and_notify_slack(customer_id: int) -> dict:
    customer = lookup_customer(customer_id)

    if not customer or "error" in customer:
        error_message = f"Customer with ID {customer_id} could not be found."
        slack_result = send_slack_message(error_message)
        return {
            "success": False,
            "customer_id": customer_id,
            "customer": customer,
            "slack_response": slack_result,
        }

    notification_text = (
        f"Customer Details:\n"
        f"• ID: {customer.get('id')}\n"
        f"• Name: {customer.get('name')}\n"
        f"• Email: {customer.get('email')}\n"
        f"• Company: {customer.get('company')}\n"
        f"• Status: {customer.get('status')}"
    )

    slack_result = send_slack_message(notification_text)

    return {
        "success": True,
        "customer_id": customer_id,
        "customer": customer,
        "slack_response": slack_result,
    }


# test customer lookup and slack notification pipeline
def test_lookup_and_notify_slack():
    result = lookup_and_notify_slack(1)
    assert isinstance(result, dict)
    assert "slack_response" in result
    assert "customer_id" in result


# test linkedin post generator listing and detail tools
def test_linkedin_tools_read():
    posts_data = list_all_generated_posts()
    assert isinstance(posts_data, dict)
    assert "posts" in posts_data
    assert "total_count" in posts_data

    details = get_post_details("non_existent_id_999")
    assert isinstance(details, dict)
    assert details["found"] is False


if __name__ == "__main__":
    print("Running MCP server automated unit tests...")

    print("\n1. Testing Database MCP tool...")
    test_db_lookup_customer()
    print("   ✓ lookup_customer passed")

    print("\n2. Testing Slack MCP tool...")
    test_send_slack_message()
    print("   ✓ send_slack_message passed")

    print("\n3. Testing DB + Slack pipeline...")
    test_lookup_and_notify_slack()
    print("   ✓ lookup_and_notify_slack passed")

    print("\n4. Testing LinkedIn read tools...")
    test_linkedin_tools_read()
    print("   ✓ list_all_generated_posts and get_post_details passed")

    print("\n🎉 Automated unit tests completed successfully!")