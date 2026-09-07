#!/usr/bin/env python3
"""Validate the standalone and team-marketplace Codex plugin bundles."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ValueError(f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def require_text(document: dict, field: str, path: Path) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be a non-empty string")
    return value


def validate_plugin(plugin_root: Path) -> tuple[dict, str]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path)
    name = require_text(manifest, "name", manifest_path)
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"{manifest_path}: invalid plugin name {name!r}")
    if plugin_root.name != name:
        raise ValueError(
            f"{manifest_path}: folder {plugin_root.name!r} must match plugin name {name!r}"
        )
    version = require_text(manifest, "version", manifest_path)
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"{manifest_path}: version {version!r} is not SemVer")
    require_text(manifest, "description", manifest_path)

    mcp_reference = require_text(manifest, "mcpServers", manifest_path)
    mcp_path = (plugin_root / mcp_reference).resolve()
    if not mcp_path.is_relative_to(plugin_root.resolve()):
        raise ValueError(f"{manifest_path}: mcpServers must stay inside the plugin")
    mcp_document = load_json(mcp_path)
    servers = mcp_document.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"{mcp_path}: mcpServers must be a non-empty object")
    server = servers.get(name)
    if not isinstance(server, dict) or server.get("type") != "http":
        raise ValueError(f"{mcp_path}: {name} must declare an HTTP MCP server")
    url = server.get("url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.netloc or parsed.path != "/mcp":
        raise ValueError(f"{mcp_path}: MCP URL must be public HTTPS and end at /mcp")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise ValueError(f"{manifest_path}: interface must be an object")
    for field in ("displayName", "shortDescription", "longDescription", "developerName"):
        require_text(interface, field, manifest_path)
    prompts = interface.get("defaultPrompt")
    if not isinstance(prompts, list) or not prompts or not all(
        isinstance(prompt, str) and prompt.strip() for prompt in prompts
    ):
        raise ValueError(f"{manifest_path}: interface.defaultPrompt must be a string array")

    for field in ("composerIcon", "logo"):
        reference = interface.get(field)
        if reference is None:
            continue
        if not isinstance(reference, str):
            raise ValueError(f"{manifest_path}: interface.{field} must be a path")
        asset_path = (plugin_root / reference).resolve()
        if not asset_path.is_relative_to(plugin_root.resolve()) or not asset_path.is_file():
            raise ValueError(f"{manifest_path}: interface.{field} does not resolve in plugin")
    return manifest, url


def validate_marketplace(repo_root: Path) -> None:
    marketplace_path = repo_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = load_json(marketplace_path)
    marketplace_name = require_text(marketplace, "name", marketplace_path)
    if not NAME_RE.fullmatch(marketplace_name):
        raise ValueError(f"{marketplace_path}: invalid marketplace name")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        raise ValueError(f"{marketplace_path}: expected exactly one plugin entry")
    entry = plugins[0]
    if not isinstance(entry, dict) or entry.get("name") != "moodle-mcp":
        raise ValueError(f"{marketplace_path}: Moodle plugin entry is missing")
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        raise ValueError(f"{marketplace_path}: team plugin must use a local repo source")
    if source.get("path") != "./plugins/moodle-mcp":
        raise ValueError(f"{marketplace_path}: source path must be ./plugins/moodle-mcp")
    policy = entry.get("policy")
    if not isinstance(policy, dict) or policy.get("installation") != "AVAILABLE" \
            or policy.get("authentication") != "ON_INSTALL":
        raise ValueError(f"{marketplace_path}: explicit install/auth policies are required")

    team_manifest, team_url = validate_plugin(repo_root / "plugins" / "moodle-mcp")
    root_manifest, root_url = validate_plugin(repo_root)
    for field in ("name", "version", "description", "homepage", "repository"):
        if team_manifest.get(field) != root_manifest.get(field):
            raise ValueError(f"standalone and team manifests disagree on {field}")
    if team_url != root_url:
        raise ValueError("standalone and team plugins target different MCP URLs")


def main() -> int:
    repo_root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    try:
        validate_marketplace(repo_root)
    except ValueError as exc:
        print(f"Codex packaging invalid: {exc}", file=sys.stderr)
        return 1
    print("Codex packaging valid: standalone plugin + jaipuria-ai-labs marketplace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
