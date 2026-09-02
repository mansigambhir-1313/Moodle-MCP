"""The one non-read-only tool: create_report — trigger on-demand generation of a student's
interactive report on the moodle-agent report service and return a shareable link.

Boundary: this MCP server's own DB credential stays SELECT-only. Generation (the LLM
insight + its cache write) happens entirely in the agent service, reached server-to-server
over https with HTTP Basic credentials from env. The tool never emails anything.
"""
import logging

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from annotations import GENERATE_ANNOTATIONS
from config import settings
from security import MSG_DENIED

log = logging.getLogger("moodle-mcp.actions")

MSG_UNCONFIGURED = ("Report generation is not configured on this server "
                    "(AGENT_API_BASE is unset). Ask the administrator to enable it.")
MSG_AGENT_DOWN = "The report service could not be reached right now. Please retry shortly."
# Generation includes one LLM call (~15s) when the insight is not yet cached.
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
# Response fields passed through to the caller — never internal paths.
_PASS_FIELDS = ("student_id", "name", "attendance_pct", "ce_pct", "subjects",
                "insight_headline", "from_cache", "report_url")


class CreateReportParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2025-27'", max_length=64)
    student_id: str = Field(description="enrolment id, e.g. 'JN25PG067'", max_length=64)
    refresh: bool = Field(default=False,
                          description="regenerate the insight instead of using the cache")


async def _create_impl(svc, p: CreateReportParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        raise PermissionError(MSG_DENIED)  # campus outside the caller's grant
    if not settings.report_generation_enabled():
        raise ToolError(MSG_UNCONFIGURED)
    url = (f"{settings.agent_api_base.rstrip('/')}/generate/"
           f"{p.campus}/{p.batch}/{p.student_id}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, params={"refresh": str(p.refresh).lower()},
                                  auth=(settings.agent_admin_user,
                                        settings.agent_admin_pass))
    except httpx.HTTPError as e:
        log.warning("create_report: agent unreachable: %s", type(e).__name__)
        raise ToolError(MSG_AGENT_DOWN)
    if r.status_code == 404:
        return {"found": False,
                "note": f"no student {p.student_id} in {p.campus}/{p.batch}"}
    if r.status_code != 200:
        log.warning("create_report: agent returned %s", r.status_code)
        raise ToolError(MSG_AGENT_DOWN)
    data = r.json()
    out = {"found": True, "generated": True}
    out.update({k: data.get(k) for k in _PASS_FIELDS if k in data})
    return out


def register(mcp, get_service):
    @mcp.tool(title="Create Student Report", annotations=GENERATE_ANNOTATIONS)
    async def create_report(params: CreateReportParams) -> dict:
        """
        WHAT: Generate (or refresh) one student's interactive report on demand and return a
        shareable link (an expiring, unguessable URL — safe to send to the student/mentor).
        USE WHEN they say: 'create/generate the report for <id>', 'make a fresh report',
        'get me a link I can share for this student', 'regenerate with latest data'.
        DO NOT USE WHEN they only want to read data (use get_student / get_student_report) —
        this triggers real generation work. Never emails anyone.
        RETURNS: name, headline, attendance/CE %, report_url. found:false if the student is
        not in that scope. Takes ~15s when the insight is not yet cached.
        """
        return await _create_impl(await get_service(), params)
