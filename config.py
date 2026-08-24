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

    # Access: a single admin token (all campuses) and/or a JSON map token -> {name, campuses}
    mcp_admin_token: str = Field(default="", alias="MCP_ADMIN_TOKEN")
    mcp_tokens_raw: str = Field(default="", alias="MCP_TOKENS")

    # Identity / reports
    server_name: str = Field(default="jaipuria-moodle-mcp", alias="MCP_SERVER_NAME")
    server_version: str = Field(default="1.0.0", alias="MCP_SERVER_VERSION")
    server_base_url: str = Field(default="", alias="MCP_SERVER_BASE_URL")
    report_public_base_url: str = Field(
        default="https://reports.tryrehearsal.ai", alias="REPORT_PUBLIC_BASE_URL")
    storage_bucket: str = Field(default="student-reports", alias="STORAGE_BUCKET")
    report_purpose: str = Field(default="final", alias="REPORT_PURPOSE")

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


settings = Settings()


def validate_config() -> None:
    """Fail-closed boot check on the required Supabase vars + at least one access token."""
    missing = [k for k, v in {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"missing required config: {', '.join(missing)}")
    if not settings.tokens():
        log.warning("no access tokens configured (MCP_ADMIN_TOKEN / MCP_TOKENS) — "
                    "server will reject every request until one is set")
