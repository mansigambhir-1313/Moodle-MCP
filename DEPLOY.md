# Test & Deploy — Jaipuria Moodle MCP

## A. Test locally (5 min)

```bash
cd "moodle-mcp"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# grab the service key from the agent project (or paste your own)
export SUPABASE_URL="https://sadbfvfcmmxgtatfjfmc.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="<your service role key — in moodle-agent/.env>"
export MCP_ADMIN_TOKEN="test-token-123"

# 1) start the server
uvicorn server:app --port 8899
```

In a second terminal:
```bash
# 2) health check
curl localhost:8899/health          # -> {"status":"ok",...}

# 3) full MCP round-trip (lists tools + calls a few with real data)
cd "moodle-mcp" && source .venv/bin/activate
MCP_URL="http://localhost:8899/mcp" MCP_TOKEN="test-token-123" python test_client.py
```
Expected: 16 tools listed; `whoami` → admin/all; `accuracy_overview` → mean accuracy %; `at_risk_students` → a count.

## B. Deploy to Render (blueprint, ~3 min)

1. **Render → New → Blueprint** → connect **`mansigambhir-1313/Moodle-MCP`**. Render reads
   `render.yaml` and creates the `jaipuria-moodle-mcp` web service.
2. Set the env vars it prompts for (`sync:false`):
   | Var | Value |
   |---|---|
   | `SUPABASE_URL` | `https://sadbfvfcmmxgtatfjfmc.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | *(your service key — never commit it)* |
   | `MCP_ADMIN_TOKEN` | a long random string (e.g. `mcp_z6kL6UTqk8nKYHH8JpLvs8251W8MwDBXUL9eh2eQctA`) |
3. **Create** → build (`pip install -r requirements.txt`) → start (`uvicorn server:app`) →
   Render health-checks `/health`.
4. (Optional) set `MCP_SERVER_BASE_URL` to the assigned URL and add a custom domain
   (`moodle-mcp.tryrehearsal.ai`).

**Verify the deploy:**
```bash
curl https://<render-url>/health
MCP_URL="https://<render-url>/mcp" MCP_TOKEN="<your admin token>" python test_client.py
```

## C. Connect an MCP host

- **URL:** `https://<render-url>/mcp`
- **Header:** `Authorization: Bearer <MCP_ADMIN_TOKEN>`

Claude CLI:
```bash
claude mcp add moodle --transport http https://<render-url>/mcp \
  --header "Authorization: Bearer <MCP_ADMIN_TOKEN>"
```
Then ask: *"which reports are flagged in jaipur T5?"*, *"who's at risk in 2024-26?"*,
*"campus overview for jaipur"*, *"open JJ24PG099's report"*.

## D. Per-campus faculty tokens (instead of one admin token)

Set `MCP_TOKENS` (JSON) so each faculty sees only their campus:
```
MCP_TOKENS={"tok_indore":{"name":"Indore TNP","campuses":["indore"]},"tok_office":{"name":"Programme Office","campuses":null}}
```
A campus outside a token's grant returns `found:false` — verified.
