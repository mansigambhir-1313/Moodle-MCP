"""The one non-read-only tool: create_report — trigger on-demand generation of a student's
interactive report on the moodle-agent report service and return a shareable link.

Boundary: this MCP server's own DB credential stays SELECT-only. Generation (the LLM
insight + its cache write) happens entirely in the agent service, reached server-to-server
over https with HTTP Basic credentials from env. The tool never emails anything.
"""
import logging
import re
import hashlib
import hmac
import secrets
import time
import uuid
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, field_validator

from annotations import GENERATE_ANNOTATIONS, READONLY_ANNOTATIONS
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
    # All three are OPTIONAL. Leave student_id out to get a random student who has
    # graded data; leave batch out for the campus's latest graded batch; leave campus
    # out for any granted campus with data. So "a report for any student in noida" needs
    # only campus, and "any student" needs nothing.
    campus: str | None = Field(default=None, description="campus within your grant; omit to pick any granted campus with data", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2025-27'; omit for the campus's latest graded batch", max_length=64)
    student_id: str | None = Field(default=None, description="enrolment id e.g. 'JN25PG067'; omit to pick a random student who has graded data", max_length=64)
    trimester: str | None = Field(default=None, description="target trimester e.g. '3'; omit to auto-pick the latest trimester that has scored subjects for the student", max_length=4)
    refresh: bool = Field(default=False,
                          description="regenerate the insight instead of using the cache")

    @field_validator("campus", "batch", "student_id")
    @classmethod
    def _plain_segment(cls, v, info):
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None  # blank == omitted
        if not _SEGMENT_RE.fullmatch(v):
            raise ValueError("only letters, digits, '-' and '_' are allowed")
        if info.field_name == "campus":
            return v.lower()
        return v

    @field_validator("trimester")
    @classmethod
    def _digit(cls, v):
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None  # blank == omitted (auto-pick)
        if not v.isdigit() or not (1 <= int(v) <= 12):
            raise ValueError("trimester must be a number 1-12")
        return v


class ReportJobParams(BaseModel):
    request_id: str = Field(description="UUID returned by create_report while queued",
                            min_length=36, max_length=36,
                            pattern=r"^[0-9a-fA-F-]{36}$")


def _signed_agent_headers(method: str, url: str, actor: str) -> dict[str, str]:
    """Bind an internal request to its method, target, actor and a short time window."""
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    parsed = urlsplit(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    canonical = "\n".join((method.upper(), target, actor, timestamp, nonce))
    signature = hmac.new(
        settings.agent_shared_secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-MCP-Requester-Subject": actor,
        "X-MCP-Timestamp": timestamp,
        "X-MCP-Nonce": nonce,
        "X-MCP-Signature": f"v1={signature}",
    }

async def _agent_generate(campus, batch, student_id, refresh, trimester=None, principal=None):
    """POST the agent /generate for one student. Returns (status_code, json). Raises
    ToolError only on transport failure / 5xx (never on 404, which is a data-level miss).
    trimester is optional — omit it to let the agent auto-pick the latest trimester
    that has scored subjects for the student."""
    endpoint = "report-jobs" if settings.agent_report_queue else "generate"
    url = (f"{settings.agent_api_base.rstrip('/')}/{endpoint}/"  # segments URL-encoded
           f"{quote(campus, safe='')}/{quote(batch, safe='')}/{quote(student_id, safe='')}")
    params = {"refresh": str(refresh).lower()}
    if trimester is not None:
        params["trimester"] = str(trimester)
    try:
        from audit_store import principal_subject
        actor = principal_subject(principal)
        idem = hashlib.sha256(
            f"{actor}|{campus}|{batch}|{student_id}|{trimester}|{int(refresh)}".encode()
        ).hexdigest()
        if settings.agent_report_queue:
            url = f"{url}?{urlencode(params)}"
            headers = _signed_agent_headers("POST", url, actor)
            auth = None
        else:
            headers = {"X-MCP-Requester-Subject": actor,
                       "Idempotency-Key": idem}
            auth = (settings.agent_admin_user, settings.agent_admin_pass)
        headers["Idempotency-Key"] = idem
        request_kwargs = {"auth": auth, "headers": headers}
        if not settings.agent_report_queue:
            request_kwargs["params"] = params
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, **request_kwargs)
    except httpx.HTTPError as e:
        log.warning("create_report: agent unreachable: %s", type(e).__name__)
        raise ToolError(MSG_AGENT_DOWN)
    if r.status_code not in (200, 202, 404):
        log.warning("create_report: agent returned %s", r.status_code)
        raise ToolError(MSG_AGENT_DOWN)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        raise ToolError(MSG_AGENT_DOWN)
    if r.status_code == 202:
        try:
            uuid.UUID(str(body.get("request_id")))
        except (TypeError, ValueError):
            raise ToolError(MSG_AGENT_DOWN)
    return r.status_code, body


async def _agent_job(request_id: str, principal) -> dict:
    from audit_store import principal_subject
    actor = principal_subject(principal)
    url = f"{settings.agent_api_base.rstrip('/')}/report-jobs/{quote(request_id, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                url, headers=_signed_agent_headers("GET", url, actor))
    except httpx.HTTPError:
        raise ToolError(MSG_AGENT_DOWN)
    if response.status_code == 404:
        return {"found": False, "note": "Report job not found for this user."}
    if response.status_code != 200:
        raise ToolError(MSG_AGENT_DOWN)
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(MSG_AGENT_DOWN) from exc
    if not isinstance(body, dict):
        raise ToolError(MSG_AGENT_DOWN)
    return body


def _success(data, campus, batch, auto):
    out = {"found": True, "generated": True, "campus": campus, "batch": batch}
    if auto:  # tell the model we chose the student, so it can name who it generated for
        out["auto_selected"] = True
    out.update({k: data.get(k) for k in _PASS_FIELDS if k in data})
    report_url = out.get("report_url")
    if report_url and not _safe_report_url(report_url):
        log.error("report service returned an invalid report URL; suppressing it")
        out.pop("report_url", None)
        out["link_status"] = "not_generated"
    return out


def _queued(data, campus, batch, auto):
    return {
        "found": True,
        "generated": False,
        "queued": True,
        "request_id": data.get("request_id"),
        "status": data.get("status", "queued"),
        "campus": campus,
        "batch": batch,
        "auto_selected": bool(auto),
        "note": "Report generation is queued. Call get_report_job with request_id.",
    }


def _safe_report_url(value: object) -> bool:
    """Only pass through exact capability-link routes on the configured report host."""
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        candidate = urlsplit(value)
        expected = urlsplit(settings.report_public_base_url)
    except ValueError:
        return False
    return (
        candidate.scheme == "https"
        and candidate.hostname == expected.hostname
        and candidate.port == expected.port
        and bool(re.fullmatch(r"/(?:r|rv|s)/[A-Za-z0-9_-]+={0,2}", candidate.path))
        and not candidate.query
        and not candidate.fragment
        and not candidate.username
        and not candidate.password
    )


async def _create_impl(svc, p: CreateReportParams) -> dict:
    if p.campus and svc.campus_scope(p.campus) == []:
        raise PermissionError(MSG_DENIED)  # explicit campus outside the caller's grant
    if not settings.report_generation_enabled():
        raise ToolError(MSG_UNCONFIGURED)
    from guardrails import enrolled_no_data
    from tools.common import (cached_report_students, find_student, graded_scopes,
                              random_gradeable_students, roster_member)

    campus, batch, student_id = p.campus, p.batch, p.student_id

    # --- AUTO-SELECT: any part omitted -> pick a real graded target and GENERATE.
    # A student can have marks yet no scored subjects in the batch's in-progress
    # trimester (agent 404), so we try several candidates across graded scopes
    # (newest first) and return the first that actually builds — preferring
    # already-cached students so it's fast and reliably lands on a report.
    if not (campus and batch and student_id):
        scopes = graded_scopes(svc, campus=campus, batch=batch)
        if not scopes:
            where = (campus or "your campuses") + (f"/{batch}" if batch else "")
            return {"found": False,
                    "note": (f"No graded data is available in {where} yet, so there is no "
                             "student to build a report from. Batches appear here once "
                             "their gradebook run completes.")}
        if student_id:  # student id given; only campus/batch were omitted.
            # Locate the student's ACTUAL campus/batch instead of assuming the first
            # graded scope — a Noida student must never be looked up in Indore's run.
            loc = find_student(svc, student_id)
            if loc is None:
                return {"found": False,
                        "note": (f"{student_id} was not found in any campus you can access — "
                                 "check the enrolment id.")}
            campus, batch = loc["campus"], loc["batch"]
            if svc.latest_run(campus, batch) is None:
                return enrolled_no_data(loc)  # enrolled, but no graded run yet
            code, body = await _agent_generate(campus, batch, student_id, p.refresh,
                                               trimester=p.trimester,
                                               principal=getattr(svc, "principal", None))
            if code == 200:
                return _success(body, campus, batch, auto=False)
            if code == 202:
                return _queued(body, campus, batch, auto=False)
            return {"found": False,
                    "note": body.get("detail") or f"No report data for {student_id} in {campus}/{batch}."}
        # order scopes so ones PROVEN to generate (a batch with cached reports has a
        # scored trimester) come first — this skips batches whose latest trimester is
        # still ungraded — but pick a RANDOM student within them, so each call varies.
        ordered = sorted(scopes, key=lambda s: 0 if cached_report_students(svc, s[2], limit=1) else 1)
        candidates, seen = [], set()
        for c, b, rid in ordered:  # random graded students first (genuine variety)
            for sid in random_gradeable_students(svc, rid, n=3):
                if sid not in seen:
                    seen.add(sid)
                    candidates.append((c, b, sid))
        for c, b, rid in ordered:  # cached students as the guaranteed instant fallback
            for sid in cached_report_students(svc, rid, limit=3):
                if sid not in seen:
                    seen.add(sid)
                    candidates.append((c, b, sid))
        last = None
        for c, b, sid in candidates[:8]:  # cap agent round-trips
            code, body = await _agent_generate(c, b, sid, p.refresh, trimester=p.trimester,
                                               principal=getattr(svc, "principal", None))
            if code == 200:
                return _success(body, c, b, auto=True)
            if code == 202:
                return _queued(body, c, b, auto=True)
            last = body.get("detail") or last
        where = campus or "your campuses"
        return {"found": False,
                "note": (f"Couldn't find a student with a ready report in {where} right now "
                         f"({last or 'their latest trimester has no scored subjects yet'}). "
                         "Name a specific student, or try a batch whose trimester is graded.")}

    # --- EXPLICIT: all three given.
    # Roster-first, data-second: a whole batch can be on the roster with no completed
    # gradebook run (a first-year batch weeks into term). Answer that truthfully instead
    # of letting the agent's generic 404 read as "no such student".
    if svc.latest_run(campus, batch) is None:
        stu = roster_member(svc, student_id, campus, batch)
        if stu is not None:
            return enrolled_no_data(stu)
        return {"found": False,
                "note": (f"{student_id} is not enrolled in {campus}/{batch} — "
                         "check the enrolment id, campus and batch.")}
    code, body = await _agent_generate(campus, batch, student_id, p.refresh,
                                       trimester=p.trimester,
                                       principal=getattr(svc, "principal", None))
    if code == 404:
        return {"found": False,
                           "note": body.get("detail") or f"No report data for {student_id} in {campus}/{batch}."}
    if code == 202:
        return _queued(body, campus, batch, auto=False)
    return _success(body, campus, batch, auto=False)


def register(mcp, get_service):
    @mcp.tool(title="Create Student Report", annotations=GENERATE_ANNOTATIONS)
    async def create_report(params: CreateReportParams) -> dict:
        """
        WHAT: Queue generation (or refresh) of one student's report. The initial response
        returns a request_id; call get_report_job with that id until it is completed, then the
        completed response contains the validated narrative, per-subject numbers, and a
        shareable expiring link to the interactive page.
        ALL PARAMETERS ARE OPTIONAL. Omit student_id to have a random student WITH graded data
        picked for you; omit batch for the campus's latest graded batch; omit campus for any
        campus in your grant that has data. So 'a report for any student in noida' -> pass
        campus="noida" only; 'a report for any student' -> pass nothing. When only student_id
        is given, the tool finds that student's own campus/batch automatically — you do NOT
        need to know their campus. Omit trimester to auto-pick the latest trimester that has
        scored subjects (so a student mid-trimester still reports on their last graded one);
        pass trimester (e.g. '3') only to force a specific one. When a student is auto-picked
        the response sets auto_selected:true — tell the user which student (name + id) you
        generated for.
        USE WHEN they say: 'create/generate the report for <id>', 'report for any/a random
        student [in <campus>]', 'make a fresh report', 'get me a link I can share'.
        DO NOT USE WHEN they only want raw data (use get_student / student_marks) or the
        cached narrative without generating (use get_student_report). Never emails anyone.
        RETURNS: queued:true + request_id for the durable job. get_report_job returns status and,
        once completed, campus, batch, name, narrative, subject_table, attendance/CE %, and the
        exact report_url. found:false with a note if there is no graded data to build from.
        """
        return await _create_impl(await get_service(), params)

    @mcp.tool(title="Get Report Job", annotations=READONLY_ANNOTATIONS)
    async def get_report_job(params: ReportJobParams) -> dict:
        """Check a queued create_report request. Returns status and, once complete,
        the full report plus its exact shareable capability URL."""
        svc = await get_service()
        job = await _agent_job(params.request_id, svc.principal)
        if not job.get("found", True):
            return job
        if job.get("status") != "completed":
            return {"found": True, "request_id": params.request_id,
                    "status": job.get("status"), "error_code": job.get("error_code")}
        result = job.get("result") or {}
        campus, batch = job.get("campus"), job.get("batch")
        if not campus or svc.campus_scope(campus) == []:
            raise PermissionError(MSG_DENIED)
        return {**_success(result, campus, batch, auto=False),
                "request_id": params.request_id, "status": "completed"}
