# Slack App, Webhook, and Interactive Cards Setup

This guide details how to create and configure a Slack Application for receiving Block Kit approval cards and processing interactive human-in-the-loop decisions (*Approve & Post* vs. *Discard*).

---

## 1. Create a Slack App

1. Visit [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Select **From scratch**.
3. Enter an App Name (e.g. `LinkedIn Agent Approvals`) and pick your target development Slack Workspace.
4. Click **Create App**.

---

## 2. Configure Incoming Webhooks

The agent dispatches interactive Block Kit cards to your designated Slack channel via an Incoming Webhook:

1. In the app settings sidebar, click **Incoming Webhooks**.
2. Toggle **Activate Incoming Webhooks** to **On**.
3. Click the **Add New Webhook to Workspace** button at the bottom of the page.
4. Select the channel where post approvals should be sent (e.g. `#linkedin-approvals` or `#general`) and click **Allow**.
5. Copy the generated **Webhook URL**:
   * Format: `https://hooks.slack.com/services/T000.../B000.../XXXX...`
   * Set this value as `SLACK_WEBHOOK_URL` in your `.env` file or Render environment variables.

---

## 3. Enable Slack Interactivity

When a user clicks **✅ Yes (Approve & Post)** or **❌ No (Discard)** in Slack, Slack sends an HTTP POST request to your backend receiver.

1. In the app settings sidebar, select **Interactivity & Shortcuts**.
2. Switch the **Interactivity** toggle to **On**.
3. Set the **Request URL**:

### A. Local Development (via Ngrok)
Slack requires a publicly accessible HTTPS endpoint:
```bash
# Terminal 1: Run local backend
uvicorn main:app --reload --port 8000

# Terminal 2: Expose port 8000
ngrok http 8000
```
Set the Slack **Request URL** to:
```
https://<your-ngrok-subdomain>.ngrok-free.app/slack/interactions
```

### B. Cloud Deployment (Render)
For production deployments on Render, set the **Request URL** directly to:
```
https://<your-render-service-name>.onrender.com/slack/interactions
```

4. Click **Save Changes** in the bottom right corner.

---

## 4. Understanding the Human-in-the-Loop Interaction Flow

```
[Agent generates post]
         │
         ▼
[Slack Block Kit card delivered to channel]
┌────────────────────────────────────────────────────────┐
│  📝 *New LinkedIn Post Draft For Review*               │
│  "Building autonomous AI pipelines with LangGraph..."  │
│                                                        │
│  [ ✅ Yes (Approve & Post) ]    [ ❌ No (Discard) ]     │
└────────────────────────────────────────────────────────┘
         │
         │  (User clicks "Approve")
         ▼
[Slack POST payload -> /slack/interactions]
         │
         ├─► Validates post ID in database
         ├─► Updates post status: APPROVED -> PUBLISHED
         ├─► Publishes content directly to LinkedIn API
         └─► Updates original Slack message with confirmation badge:
             "🚀 Post #12 published to LinkedIn by @user"
```

---

## 5. Testing the Slack Integration

To test your Slack integration without running the full agent workflow:

```bash
# Activate your virtual environment and run the Slack test suite
source .venv/bin/activate
pytest project/backend/tests/test_mcp_servers.py -k test_slack
```
This sends a test message directly to your configured channel and verifies webhook delivery.
