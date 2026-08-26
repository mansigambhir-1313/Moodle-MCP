"""Report accuracy — the two-scheme validation scores (faithfulness panel + two-turn LLM judge).
This is the exclusive layer: no raw Moodle view exposes per-report accuracy. Read-only."""
from collections import Counter

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit, not_found
from tools.common import accuracy_rows

_ACC = "student_id,overall_pct,overall_label,agent_accuracy,panel_verdict,panel_score,claims_total,claims_matched"


class StudentAcc(BaseModel):
    student_id: str = Field(description="enrolment id", max_length=64)
    campus: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)


class ScopeAcc(BaseModel):
    campus: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    trimester: str | None = Field(default=None, max_length=8)
    limit: int = Field(default=25, description="max rows for flagged list, 1-50", ge=1, le=50)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _student_impl(svc, p: StudentAcc) -> dict:
    rows = accuracy_rows(svc, campus=p.campus, batch=p.batch, cols=_ACC)
    r = next((a for a in rows if a.get("student_id") == p.student_id), None)
    if not r:
        return not_found("accuracy score")
    return {"found": True, "student_id": p.student_id,
            "overall_accuracy_pct": r.get("overall_pct"), "label": r.get("overall_label"),
            "agent_claim_accuracy": r.get("agent_accuracy"),
            "claims_checked": r.get("claims_total"), "claims_matched": r.get("claims_matched"),
            "panel_verdict": r.get("panel_verdict"), "panel_score": r.get("panel_score"),
            "interpretation": _label_help(r.get("overall_label"))}


def _label_help(label):
    return {"verified": "Both schemes agree the report faithfully matches the data.",
            "minor-drift": "Mostly accurate; a few claims drifted from the data — worth a glance.",
            "flagged": "Interpretive drift or panel dissent — human review recommended."}.get(
        label, "Not yet audited.")


def _overview_impl(svc, p: ScopeAcc) -> dict:
    rows = accuracy_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_ACC)
    if not rows:
        return {"available": False, "note": "no accuracy scores for this scope yet"}
    labels = Counter(r.get("overall_label") for r in rows)
    verdicts = Counter(r.get("panel_verdict") for r in rows)
    return {"scored": len(rows),
            "mean_overall_accuracy_pct": _mean([r.get("overall_pct") for r in rows]),
            "mean_agent_claim_accuracy": _mean([100 * (r.get("agent_accuracy") or 0) for r in rows]),
            "labels": {"verified": labels.get("verified", 0),
                       "minor_drift": labels.get("minor-drift", 0),
                       "flagged": labels.get("flagged", 0)},
            "panel_verdicts": dict(verdicts)}


def _flagged_impl(svc, p: ScopeAcc) -> dict:
    rows = accuracy_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester,
                         label="flagged", cols=_ACC)
    rows.sort(key=lambda r: r.get("overall_pct") or 0)
    rows = rows[:clamp_limit(p.limit)]
    return {"flagged_count": len(rows),
            "reports": [{"student_id": r.get("student_id"), "overall_pct": r.get("overall_pct"),
                         "agent_claim_accuracy": r.get("agent_accuracy"),
                         "panel_verdict": r.get("panel_verdict")} for r in rows],
            "note": "These reports need a human look before they go to the student."}


def register(mcp, get_service):
    @mcp.tool(title="Get Report Accuracy", annotations=READONLY_ANNOTATIONS)
    async def get_report_accuracy(params: StudentAcc) -> dict:
        """
        WHAT: The trustworthiness score for ONE student's report — overall accuracy %, label,
        how many factual claims matched the data, and the faithfulness-panel verdict.
        USE WHEN they say: 'is <id>'s report accurate', 'can I trust this report', 'why is this
        flagged', 'how many claims checked out'.
        DO NOT USE WHEN they want the report content (get_student_report) or a cohort roll-up
        (accuracy_overview).
        RETURNS: overall_accuracy_pct, label, claims, panel_verdict, interpretation.
        """
        return _student_impl(await get_service(), params)

    @mcp.tool(title="Accuracy Overview", annotations=READONLY_ANNOTATIONS)
    async def accuracy_overview(params: ScopeAcc) -> dict:
        """
        WHAT: Cohort-level accuracy — mean overall accuracy % and the verified / minor-drift /
        flagged breakdown for a campus/batch/trimester.
        USE WHEN they say: 'how accurate are the reports', 'validation summary', 'what's our mean
        accuracy in indore', 'how many reports drifted'.
        DO NOT USE WHEN they want a specific student (get_report_accuracy) or the flagged list.
        RETURNS: scored, mean_overall_accuracy_pct, labels, panel_verdicts. available:false if none.
        """
        return _overview_impl(await get_service(), params)

    @mcp.tool(title="Flagged Reports", annotations=READONLY_ANNOTATIONS)
    async def flagged_reports(params: ScopeAcc) -> dict:
        """
        WHAT: The reports the validation flagged (interpretive drift or panel dissent) — the human
        review queue, worst first.
        USE WHEN they say: 'which reports need review', 'show me the flagged ones', 'what should I
        check before sending', 'review queue'.
        DO NOT USE WHEN they want the overall rate (accuracy_overview) or a single score.
        RETURNS: flagged_count + reports (student_id, overall_pct, panel_verdict).
        """
        return _flagged_impl(await get_service(), params)
