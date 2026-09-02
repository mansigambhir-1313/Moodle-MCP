"""Shared MCP tool annotations. Every tool except create_report is read-only."""

READONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# create_report triggers generation on the report service (writes an insight cache row
# there; overwrites nothing a caller could lose). Regenerating is safe to repeat.
GENERATE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
