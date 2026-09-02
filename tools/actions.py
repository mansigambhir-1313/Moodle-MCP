"""The one non-read-only tool: create_report — trigger on-demand generation of a student's
interactive report on the moodle-agent report service and return a shareable link.

Boundary: this MCP server's own DB credential stays SELECT-only. Generation (the LLM
insight + its cache write) happens entirely in the agent service, reached server-to-server
over https with HTTP Basic credentials from env. The tool never emails anything.
"""
import logging
import re
from urllib.parse import quote

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, field_validator

from annotations import GENERATE_ANNOTATIONS
from config import settings
from security import MSG_DENIED

log = logging.getLogger("moodle-mcp.actions")

MSG_UNCONFIGURED = ("Report generation is not configured on this server "
                    "(AGENT_API_BASE is unset). Ask the administrator to enable it.")
MSG_AGENT_DOWN = "The report service could not be reached right now. Please retry shortly."
# Generation includes one LLM call (~15s) when the insight is not yet cached.
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
# Response fields passed through to the caller — never internal paths. narrative +
# subject_table carry the COMPLETE report content, so the host renders the report
# in-conversation instead of only handing the user a link.
_PASS_FIELDS = ("student_id", "name", "attendance_pct", "ce_pct", "subjects",
                "insight_headline", "from_cache", "report_url", "trimester",
                "narrative", "subject_table")


# These three values are built into a URL PATH on the agent API, which this tool
# calls with ADMIN credentials — so they must never be able to change the route.
# One conservative charset (no dots, slashes, '?', '#', '%', spaces) kills path
# traversal, query smuggling, and fragment tricks outright; real campus/batch/
# enrolment ids are all plain [A-Za-z0-9_-].
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class CreateReportParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2025-27'", max_length=64)
    student_id: str = Field(description="enrolment id, e.g. 'JN25PG067'", max_length=64)
    refresh: bool = Field(default=False,
                          description="regenerate the insight instead of using the cache")

    @field_validator("campus", "batch", "student_id")
    @classmethod
    def _plain_segment(cls, v: str, info):
        v = (v or "").strip()
        if not _SEGMENT_RE.fullmatch(v):
            raise ValueError("only letters, digits, '-' and '_' are allowed")
        return v


async def _create_impl(svc, p: CreateReportParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        raise PermissionError(MSG_DENIED)  # campus outside the caller's grant
    if not settings.report_generation_enabled():
        raise ToolError(MSG_UNCONFIGURED)
    # Roster-first, data-second: a whole batch can be on the roster with no completed
    # gradebook run (e.g. a first-year batch weeks into term). Answer that truthfully
    # here instead of letting the agent's generic 404 read as "no such student".
    from guardrails import enrolled_no_data
    from tools.common import roster_member
    if svc.latest_run(p.campus, p.batch) is None:
        stu = roster_member(svc, p.student_id, p.campus, p.batch)
        if stu is not None:
            return enrolled_no_data(stu)
        return {"found": False,
                "note": (f"{p.student_id} is not enrolled in {p.campus}/{p.batch} — "
                         "check the enrolment id, campus and batch.")}
    # belt and braces on top of the validator: URL-encode each path segment
    url = (f"{settings.agent_api_base.rstrip('/')}/generate/"
           f"{quote(p.campus, safe='')}/{quote(p.batch, safe='')}/"
           f"{quote(p.student_id, safe='')}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, params={"refresh": str(p.refresh).lower()},
                                  auth=(settings.agent_admin_user,
                                        settings.agent_admin_pass))
    except httpx.HTTPError as e:
        log.warning("create_report: agent unreachable: %s", type(e).__name__)
        raise ToolError(MSG_AGENT_DOWN)
    if r.status_code == 404:
        # surface the agent's real reason (e.g. "student not in run", "no scored
        # subjects") rather than a blanket "no student" — the batch has a run (checked
        # above), so this is a student- or data-level miss, not a missing batch.
        detail = ""
        try:
            detail = (r.json() or {}).get("detail", "")
        except Exception:  # noqa: BLE001
            detail = ""
        return {"found": False,
                "note": detail or f"No report data for {p.student_id} in {p.campus}/{p.batch}."}
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
        WHAT: Generate (or refresh) one student's report on demand and return it COMPLETE,
        right here: the validated narrative (headline, personal pattern, attendance line, the
        four moves) plus the per-subject numbers table — AND a shareable expiring link to the
        interactive page (unguessable URL, safe to send to the student/mentor).
        USE WHEN they say: 'create/generate the report for <id>', 'make a fresh report',
        'show me the full report', 'get me a link I can share', 'regenerate with latest data'.
        DO NOT USE WHEN they only want raw data (use get_student / student_marks) or the
        cached narrative without generating (use get_student_report). Never emails anyone.
        RETURNS: name, narrative{headline, subtitle, pattern_title, pattern_text,
        attendance_line, tracks[]}, subject_table[], attendance/CE %, report_url.
        found:false if the student is not in that scope. ~15s when not yet cached.
        PRESENT the narrative and subject table to the user in full — that IS the report.
        """
        return await _create_impl(await get_service(), params)
