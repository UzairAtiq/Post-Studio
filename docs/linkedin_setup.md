# LinkedIn Multi-User Authentication & Setup Guide

This guide covers:
1. **How Multi-User Authentication Works** (Why end users do NOT need their own LinkedIn app).
2. **End-User Guide: Connecting LinkedIn** (Zero configuration, 1-click connect).
3. **Application Administrator Setup** (One-time developer portal setup).
4. **Security, Session Isolation & Token Encryption**.

---

## 1. How Multi-User Authentication Works (Why Users Do NOT Need Their Own App)

In traditional developer scripts, users often had to create their own LinkedIn Developer App and supply Client IDs and Secrets. **This project eliminates that requirement entirely.**

* **Single Central Developer App**: You (the administrator/host) register **one single LinkedIn Developer Application** on the LinkedIn Developer Portal.
* **Standard OAuth 2.0 Consent**: When any user visits your deployed website, they click **Connect LinkedIn**. LinkedIn's official OAuth consent screen opens and asks: *"Allow [Your App Name] to post on your behalf and view your profile?"*
* **Automatic Per-User Credential Storage**: Upon consent, LinkedIn returns an authorization code tied to *that specific user*. The backend exchanges it for an access token, resolves the user's unique Person URN (`urn:li:person:<id>`), encrypts the token at rest via Fernet, and stores it in the database keyed by the user's ID.
* **Session Cookie Mapping**: The user's browser receives an unguessable `session_id` cookie (`HttpOnly`, `SameSite=Lax`). All actions (generating posts, status checks, publishing) are isolated to that user's LinkedIn profile.
* **App Status & Permissions**:
  * **Verified / Standard App**: When your app is verified with the products *Share on LinkedIn* (`w_member_social`) and *Sign In with LinkedIn using OpenID Connect* (`openid profile email`), **any user with a standard personal LinkedIn account can authenticate and use the site immediately**.
  * **Development Mode (Before Verification)**: If your app is still unverified or in development mode, external test users must be added under the **Auth** -> **Developer access / Roles** tab in your LinkedIn Developer Portal to be able to sign in. Once verified, no role assignment is needed for external users.

---

## 2. End-User Guide: Connecting Your LinkedIn Account (No App Required)

If you are a user, team member, or client using this website:

> [!IMPORTANT]
> **You do NOT need a LinkedIn Developer account, and you do NOT need to create a LinkedIn app or generate API keys.**

### Step-by-Step Connection:
1. Open the website dashboard (`http://localhost:8000/` or your deployed URL).
2. Look at the **LinkedIn Connection Status** card on the dashboard.
3. Click **Connect LinkedIn** (navigates to `/linkedin/login`):
   * The backend generates a secure state token to protect against CSRF and redirects to LinkedIn.
4. Sign in to your personal or work LinkedIn account and click **Allow** to grant requested permissions.
5. Once authorized, LinkedIn redirects back to the dashboard with an **Active** status badge showing your profile identity and token expiry.
6. To disconnect your account at any time, click **Log Out** or visit `/linkedin/logout`.

---

## 3. Application Administrator Setup (One-Time Only)

> [!NOTE]
> This section is **ONLY for the deployer / system administrator** who hosts the backend service. End users can completely ignore this section.

### A. LinkedIn Developer Portal Setup
1. Navigate to the [LinkedIn Developer Portal](https://developer.linkedin.com/) and click **Create App**.
2. Complete the required fields:
   * **App Name**: e.g. `LinkedIn Post Automation`
   * **LinkedIn Page**: Associate your LinkedIn Company page or personal profile.
   * **App Logo**: Upload a logo image.
3. Agree to legal terms and click **Create app**.

### B. Request Required Product Access
In your app dashboard, open the **Products** tab and request access to:
1. **Share on LinkedIn**:
   * Grants the `w_member_social` permission.
   * Required to programmatically publish text and media posts to member feeds.
2. **Sign In with LinkedIn using OpenID Connect**:
   * Grants `openid`, `profile`, and `email` permissions.
   * Required to authenticate users and resolve the Member Person URN (`urn:li:person:<id>`).

> [!NOTE]
> Approval for these two products is instantaneous for standard applications.

### C. Configure OAuth 2.0 Credentials & Redirect URLs
Open the **Auth** tab in your app dashboard:
1. **Application Credentials**:
   * Copy **Client ID** -> set as `LINKEDIN_CLIENT_ID` in `.env` or cloud environment variables (e.g., Render).
   * Copy **Client Secret** -> set as `LINKEDIN_CLIENT_SECRET` in `.env` or cloud environment variables.
2. **OAuth 2.0 settings**:
   * Under **Authorized redirect URLs for your app**, add:
     * **Local Development**:
       ```
       http://localhost:8000/linkedin/callback
       ```
     * **Cloud Deployment (Render)**:
       ```
       https://<your-service-name>.onrender.com/linkedin/callback
       ```
   * Click **Update** to save redirect URLs.

---

## 4. Security, Session Isolation & Expiration Handling

* **Token Encryption at Rest**:
  * All access tokens and refresh tokens are encrypted at rest using AES-128-CBC symmetric encryption via Fernet (`services.crypto.encrypt_token`).
  * Configure `TOKEN_ENCRYPTION_KEY` in `.env` (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). If unconfigured, a deterministic key derived from `LINKEDIN_CLIENT_SECRET` is used as fallback.
  * Automatic migration fallback transparently reads legacy plaintext rows and re-encrypts them on subsequent saves.
* **Server-Side Session Isolation**:
  * The browser receives only an unguessable 32-byte `session_id` cookie (`HttpOnly=True`, `SameSite="Lax"`, `Secure` when HTTPS).
  * The server maps `session_id -> sub (person_urn)` via the `sessions` table.
  * Internal member URNs and access tokens are never exposed in cookies or accessible to client-side JavaScript.
* **Token Expiry & Re-Authentication**:
  * Standard LinkedIn access tokens are valid for **60 days**.
  * Refresh tokens are restricted to approved LinkedIn partner applications. For standard apps without refresh tokens, expiring tokens return a 401 Unauthorized / expired flag, prompting the user to re-authenticate seamlessly via `/linkedin/login`.
  * If a refresh token is present, the backend automatically performs a proactive refresh within 24 hours of expiration.
* **Storage Tiers**:
  * Credentials and sessions are persisted to PostgreSQL in production (`DATABASE_URL`), falling back to local SQLite (`project/backend/data/app.db`) in development. Default credentials maintain a fallback local file at `.linkedin_token`.

> [!WARNING]
> `.linkedin_token` and `.env` contain sensitive secrets. They must never be committed to git. Verify they are listed in `.gitignore`.
