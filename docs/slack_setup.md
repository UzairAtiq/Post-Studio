# Slack Direct Message (DM) Approval Setup Guide

This guide details the single-bot multi-user Slack approval architecture for the LinkedIn Post Automation platform.

---

## Architecture Overview: Single Bot, Shared Workspace

Unlike traditional webhook setups where each user must create their own Slack app and configure webhooks, this system uses a **single shared Slack Bot**:

1. **One Slack App**: Installed once in the workspace administrator's Slack workspace.
2. **Private Direct Messages**: The bot sends interactive approval cards directly to each user's private Slack DM.
3. **No Setup for Users**: End users do not configure Slack apps or webhooks. They simply join the administrator's Slack workspace using their LinkedIn account email.
4. **Zero-Friction Email Matching**: When a user logs in via LinkedIn OAuth, the backend automatically calls `users.lookupByEmail` on Slack to find their Slack Member ID and maps it to their LinkedIn `sub`.
5. **Dashboard Fallback**: If a user hasn't joined Slack yet, post generation succeeds, and the user can approve/reject posts directly from the web dashboard.

---

## 1. Environment Variables

Configure the following variables in your `.env` file or cloud deployment settings:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `SLACK_BOT_TOKEN` | Bot User OAuth Token with `xoxb-` prefix | `xoxb-12012884372583-...` |
| `SLACK_SIGNING_SECRET` | Slack Signing Secret for verifying incoming interactions | `746f03588bce8c09...` |
| `SLACK_WORKSPACE_INVITE_URL` | Shareable workspace invite link for users | `https://join.slack.com/t/...` |

---

## 2. Slack App Configuration (Admin Setup)

Only the workspace administrator performs these steps once at [api.slack.com/apps](https://api.slack.com/apps):

### A. Bot Token Scopes (`OAuth & Permissions`)
In your Slack App settings under **OAuth & Permissions** -> **Scopes** -> **Bot Token Scopes**, add:
* `chat:write` — Send direct messages and interactive cards.
* `users:read.email` — Look up workspace members by email address (`users.lookupByEmail`).
* `im:write` — Open direct message channels with users.

Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`) to `SLACK_BOT_TOKEN`.

### B. Signing Secret (`Basic Information`)
Under **Basic Information** -> **App Credentials**, copy the **Signing Secret** to `SLACK_SIGNING_SECRET`.

### C. Enable Interactivity (`Interactivity & Shortcuts`)
1. In the sidebar, select **Interactivity & Shortcuts**.
2. Turn **Interactivity** to **On**.
3. Set the **Request URL** to your backend `/slack/interactions` endpoint:
   - **Local development (via ngrok)**: `https://<your-subdomain>.ngrok-free.app/slack/interactions`
   - **Production (Render / Cloud)**: `https://<your-app-domain>.onrender.com/slack/interactions`
4. Click **Save Changes**.

---

## 3. How the Multi-User DM Approval Flow Works

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Author)
    participant Auth as Backend OAuth (/linkedin/callback)
    participant SlackAPI as Slack API
    participant Agent as Post Flow / MCP
    participant Webhook as /slack/interactions
    participant LinkedIn as LinkedIn REST API

    User->>Auth: Log in with LinkedIn OAuth
    Auth->>SlackAPI: users.lookupByEmail(email)
    SlackAPI-->>Auth: slack_user_id (e.g. U12345678)
    Auth->>Auth: Persist slack_user_id mapped to sub

    User->>Agent: POST /posts/generate (topic)
    Agent->>Agent: Save post with owner_sub and status=pending
    Agent->>SlackAPI: chat.postMessage(channel=slack_user_id, blocks)
    SlackAPI-->>User: Delivers private DM with Approve/Reject buttons

    User->>SlackAPI: Clicks "✅ Approve & Post"
    SlackAPI->>Webhook: POST /slack/interactions (signature + action payload)
    Webhook->>Webhook: Verify signature & verify clicking user == post.owner_sub
    Webhook->>LinkedIn: Publish approved post
    Webhook->>SlackAPI: chat.update (replaces buttons with "🚀 Published" confirmation)
```

---

## 4. Endpoints for Slack Connection Management

### `GET /slack/status`
Checks if the currently authenticated user has their Slack account linked:
```json
{
  "connected": true,
  "slack_user_id": "U08XXXXXXXX",
  "invite_url": "https://join.slack.com/t/...",
  "message": "Slack DM approval is active."
}
```

### `POST /slack/recheck`
If a user logged in to LinkedIn *before* joining the Slack workspace, clicking **"Check Slack Connection"** invokes this endpoint. It fetches their stored LinkedIn profile email, queries `users.lookupByEmail`, and links their `slack_user_id` without requiring re-authentication.

### `POST /slack/interactions`
Handles button clicks from Slack DMs:
* **HMAC Signature Check**: Validates `X-Slack-Signature` and timestamp freshness (< 5 minutes).
* **Ownership Verification**: Verifies the clicking Slack user matches `get_slack_user_id(post.owner_sub)`.
* **Idempotent Execution**: Dispatches LinkedIn publishing and updates the DM message card.

---

## 5. Web Dashboard Approvals

If a user chooses not to join Slack or has not connected their account yet:
* Generated posts remain in `pending` status.
* Users can review and approve/reject directly from the web dashboard:
  - `POST /posts/{post_id}/approve`
  - `POST /posts/{post_id}/reject`
* Both endpoints verify session ownership (`owner_sub == user_sub`) and synchronize Slack message cards if a DM was sent.
