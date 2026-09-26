# Connect the Moodle MCP in Jaipuria OS (faculty guide) — AIA-1390

How a faculty member connects the Moodle student-performance assistant inside **Jaipuria OS**
and asks an agent for reports. One-time setup, then it stays connected.

## One-time: add the connector
1. Open **https://jaipuria-os.ailabs-c94.workers.dev** and sign in with your **@jaipuria.ac.in**
   Google account (Cloudflare Access). If you're blocked at the sign-in screen, ask the admin
   (ailabs@jaipuria.ac.in) to add your email.
2. Go to **Gatekeepers → MCP Server → +**.
3. Paste the endpoint: **`https://moodle-mcp.tryrehearsal.ai/mcp`**
4. A connection request appears — **approve it**, then complete the **Google sign-in** popup with
   your Jaipuria account.
5. The server shows under **Connected**. Its tools are now available to agents.

*(First call may take a few seconds while the service wakes; retry once if it times out.)*

## Using it
Ask an agent in plain language, e.g.:
- "Which trimesters do we have data for in Noida 2024-26?" (read — runs without a prompt)
- "How is <student> doing overall?" / "What subjects are they weak in?"
- "Prepare a report for <student>." → this uses `create_report`; the OS shows an **approval
   prompt** before it runs (it's the one action tool). Approve to generate the shareable report.

## What you can see
Every verified Jaipuria account can query student performance across campuses and generate
reports. **Every action is logged** (who, which tool, which student, when, source) for
accountability — access records you have no legitimate need to see is traceable and subject to
institutional policy.

## If something fails
- **Blocked at Jaipuria OS sign-in** → your email isn't allowed yet (ask the admin).
- **"issuer mismatch" / connection fails before Google sign-in** → tell the platform team
  (this was fixed; the server base URL must be `https://moodle-mcp.tryrehearsal.ai`).
- **Any other error** (client registration / token exchange / scopes) → copy the exact text and
  send it to the platform team.
