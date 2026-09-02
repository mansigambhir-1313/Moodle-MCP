"""Configuration for the Moodle Reports MCP (pydantic-settings, .env + env vars)."""
import json
import logging
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase (read-only service role — server-side only, never exposed to the host)
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    # Anon/publishable key. When set, it is used as the API-gateway `apikey` while
    # SUPABASE_SERVICE_ROLE_KEY is attached as the bearer — so the DB key can be a
    # least-privilege custom-role JWT (e.g. reporting_readonly) that the gateway
    # would otherwise reject as an apikey. Leave empty to use the DB key for both.
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")

    # Access: a single admin token (all campuses) and/or a JSON map token -> {name, campuses}
    mcp_admin_token: str = Field(default="", alias="MCP_ADMIN_TOKEN")
    mcp_tokens_raw: str = Field(default="", alias="MCP_TOKENS")

    # Google OAuth sign-in (interactive faculty auth via the Jaipuria Google Workspace).
    # When both client id and secret are set, the server exposes the full MCP OAuth flow
    # (discovery metadata, dynamic client registration, Google consent) so hosts like
    # Claude.ai onboard each user via their jaipuria.ac.in Google account — no manual
    # bearer token. Static MCP_TOKENS/MCP_ADMIN_TOKEN stay as a headless fallback only
    # when OAuth is NOT configured.
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_SECRET")
    # Comma-separated allowed email domains for signed-in users (server-side gate,
    # in addition to marking the Google OAuth app "Internal" to the Workspace).
    oauth_allowed_domains_raw: str = Field(default="jaipuria.ac.in",
                                           alias="OAUTH_ALLOWED_DOMAINS")
    # Campus grant for a verified sign-in not listed in MCP_FACULTY:
    # "all" (default) = every campus; "none" = deny unless listed; or a JSON list.
    oauth_default_campuses_raw: str = Field(default="all", alias="OAUTH_DEFAULT_CAMPUSES")
    # Optional per-email overrides: JSON map email -> {name?, campuses} (null = all).
    mcp_faculty_raw: str = Field(default="", alias="MCP_FACULTY")
    # Optional stable key so issued OAuth tokens survive a restart/redeploy.
    oauth_jwt_signing_key: str = Field(default="", alias="OAUTH_JWT_SIGNING_KEY")

    # Report generation (create_report tool): the moodle-agent report service.
    # When all three are set, create_report can trigger on-demand generation there
    # (server-to-server HTTP Basic). Unset -> the tool reports itself unavailable.
    # The DB credential here stays SELECT-only; all writes happen in the agent.
    agent_api_base: str = Field(default="", alias="AGENT_API_BASE")
    agent_admin_user: str = Field(default="", alias="AGENT_ADMIN_USER")
    agent_admin_pass: str = Field(default="", alias="AGENT_ADMIN_PASS")

    # Identity / reports
    server_name: str = Field(default="jaipuria-moodle-mcp", alias="MCP_SERVER_NAME")
    server_version: str = Field(default="1.0.0", alias="MCP_SERVER_VERSION")
    server_base_url: str = Field(default="", alias="MCP_SERVER_BASE_URL")
    report_public_base_url: str = Field(
        default="https://reports.tryrehearsal.ai", alias="REPORT_PUBLIC_BASE_URL")
    storage_bucket: str = Field(default="student-reports", alias="STORAGE_BUCKET")
    report_purpose: str = Field(default="final", alias="REPORT_PURPOSE")

    # Rate limiting — tool calls per token per window (bounded, in-process)
    rate_limit: int = Field(default=90, alias="MCP_RATE_LIMIT")
    rate_window_seconds: int = Field(default=60, alias="MCP_RATE_WINDOW_SECONDS")
    # Transport hardening: max /mcp request body, and a per-IP pre-auth request cap
    # per window (blunts unauthenticated floods / token-guessing before auth).
    max_body_bytes: int = Field(default=262144, alias="MCP_MAX_BODY_BYTES")
    ip_rate_limit: int = Field(default=240, alias="MCP_IP_RATE_LIMIT")
    # Reject access tokens shorter than this at boot (set ALLOW_WEAK_TOKENS to skip).
    allow_weak_tokens: bool = Field(default=False, alias="ALLOW_WEAK_TOKENS")

    def tokens(self) -> dict:
        """token -> {name, campuses(None=all)}. Admin token grants all campuses."""
        out = {}
        if self.mcp_tokens_raw.strip():
            try:
                out.update(json.loads(self.mcp_tokens_raw))
            except json.JSONDecodeError:
                log.warning("MCP_TOKENS is not valid JSON; ignoring")
        if self.mcp_admin_token:
            out[self.mcp_admin_token] = {"name": "admin", "campuses": None}
        return out

    def oauth_enabled(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

    def report_generation_enabled(self) -> bool:
        return bool(self.agent_api_base and self.agent_admin_user and self.agent_admin_pass)

    def oauth_allowed_domains(self) -> list:
        """Lower-cased email domains allowed to sign in (empty = deny everyone)."""
        return [d.strip().lower() for d in self.oauth_allowed_domains_raw.split(",")
                if d.strip()]

    def faculty(self) -> dict:
        """email (lower) -> {name?, campuses} overrides. Malformed JSON -> {} (boot validates)."""
        if not self.mcp_faculty_raw.strip():
            return {}
        try:
            parsed = json.loads(self.mcp_faculty_raw)
            return {str(k).strip().lower(): v for k, v in parsed.items()} \
                if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            log.warning("MCP_FACULTY is not valid JSON; ignoring")
            return {}

    def oauth_default_campuses(self):
        """Default grant for a domain-verified email with no MCP_FACULTY entry.
        Returns None (all campuses), a list, or the sentinel string 'deny'."""
        raw = self.oauth_default_campuses_raw.strip()
        if raw.lower() in ("", "all", "null"):
            return None
        if raw.lower() == "none":
            return "deny"
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else "deny"
        except json.JSONDecodeError:
            return "deny"


settings = Settings()


def _valid_expires(exp) -> bool:
    """True if `exp` parses as an ISO date or datetime."""
    try:
        from datetime import datetime
        datetime.fromisoformat(str(exp).strip().replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def key_role(key: str) -> str | None:
    """Best-effort read of a Supabase key's JWT `role` claim (payload only, no
    signature check — used purely to warn when the RLS-bypassing service_role key
    is in use). Returns None for a non-JWT / opaque key."""
    try:
        import base64
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def validate_config() -> None:
    """Fail-closed boot check: required Supabase vars, and a well-formed access-token config.
    Malformed MCP_TOKENS raises at boot (visible) rather than silently locking everyone out."""
    missing = [k for k, v in {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"missing required config: {', '.join(missing)}")

    # Strict, fail-closed validation of MCP_TOKENS shape.
    if settings.mcp_tokens_raw.strip():
        try:
            parsed = json.loads(settings.mcp_tokens_raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP_TOKENS is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise RuntimeError("MCP_TOKENS must be a JSON object of token -> {name, campuses}")
        for tok, pr in parsed.items():
            if not isinstance(pr, dict) or "campuses" not in pr:
                raise RuntimeError("each MCP_TOKENS entry must be an object with a 'campuses' key")
            campuses = pr.get("campuses")
            if not (campuses is None or isinstance(campuses, list)):
                raise RuntimeError("MCP_TOKENS 'campuses' must be null (all) or a list of campuses")
            if len(tok) < 24 and not settings.allow_weak_tokens:
                raise RuntimeError("an MCP token is short (<24 chars) — use "
                                   "`secrets.token_urlsafe(24)`, or set ALLOW_WEAK_TOKENS=true")
            exp = pr.get("expires")
            if exp is not None and not _valid_expires(exp):
                raise RuntimeError("MCP_TOKENS 'expires' must be an ISO date/datetime "
                                   "(e.g. '2026-12-31' or '2026-12-31T23:59:59Z')")

    if settings.mcp_admin_token and len(settings.mcp_admin_token) < 24 \
            and not settings.allow_weak_tokens:
        raise RuntimeError("MCP_ADMIN_TOKEN is short (<24 chars) — use a high-entropy "
                           "value, or set ALLOW_WEAK_TOKENS=true")

    # Least-privilege DB credential: warn when the RLS-bypassing service_role key
    # is configured. Prefer a scoped, SELECT-only role.
    role = key_role(settings.supabase_service_role_key)
    if role == "service_role":
        log.warning("SUPABASE_SERVICE_ROLE_KEY is a full service_role key (bypasses RLS and can "
                    "write). Prefer a SELECT-only 'reporting_readonly' JWT — see "
                    "sql/2026-08-26_reporting_readonly_role.sql and the README.")
    elif role:
        log.info("DB key role: %s (non-service_role, least-privilege)", role)

    # OAuth mode: fail-closed on a half-configured setup.
    if settings.google_oauth_client_id or settings.google_oauth_client_secret:
        if not settings.oauth_enabled():
            raise RuntimeError("Google OAuth is half-configured: set BOTH "
                               "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")
        if not settings.server_base_url.startswith("https://"):
            raise RuntimeError("OAuth requires MCP_SERVER_BASE_URL to be the server's "
                               "public https URL (used for OAuth discovery + the "
                               "Google redirect URI)")
        if not settings.oauth_allowed_domains():
            raise RuntimeError("OAUTH_ALLOWED_DOMAINS is empty — every sign-in would "
                               "be rejected; set it (default: jaipuria.ac.in)")
        if settings.mcp_faculty_raw.strip():
            try:
                parsed = json.loads(settings.mcp_faculty_raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"MCP_FACULTY is not valid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise RuntimeError("MCP_FACULTY must be a JSON object of "
                                   "email -> {name, campuses}")
            for email, pr in parsed.items():
                if not isinstance(pr, dict):
                    raise RuntimeError("each MCP_FACULTY entry must be an object")
                campuses = pr.get("campuses")
                if not (campuses is None or isinstance(campuses, list)):
                    raise RuntimeError("MCP_FACULTY 'campuses' must be null (all) "
                                       "or a list of campuses")
        if not settings.oauth_jwt_signing_key:
            log.warning("OAUTH_JWT_SIGNING_KEY not set — issued OAuth tokens are "
                        "invalidated on every restart/redeploy (users must re-login)")

    # create_report backend: fail-closed on a half-configured setup, and require https
    # so the Basic credentials never travel in the clear.
    agent_bits = (settings.agent_api_base, settings.agent_admin_user, settings.agent_admin_pass)
    if any(agent_bits) and not all(agent_bits):
        raise RuntimeError("report generation is half-configured: set ALL of AGENT_API_BASE, "
                           "AGENT_ADMIN_USER, AGENT_ADMIN_PASS (or none)")
    if settings.agent_api_base and not settings.agent_api_base.startswith("https://"):
        raise RuntimeError("AGENT_API_BASE must be an https URL")

    if not settings.tokens() and not settings.oauth_enabled():
        log.warning("no auth configured (Google OAuth or MCP_ADMIN_TOKEN / MCP_TOKENS) — "
                    "server will reject every request until one is set")
