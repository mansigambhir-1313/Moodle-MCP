# Moodle MCP — Innovation Roadmap

The current 18 tools answer *snapshot* questions ("what did X score", "cohort mean marks"). The
data holds far more: **one run contains every trimester (T1–T6), every section, every component
`kind`, and per-session attendance dates.** These tools turn that into foresight, not just lookup.

## Why these are exclusive
No raw DB view or generic BI tool gives a faculty dashboard *longitudinal risk*, *single-pane
student views*, or *teaching-quality signals* through one natural-language interface. That is the
moat.

---

## Theme 1 — Longitudinal (the biggest gap: we have T1–T6 in one run)
- **`student_trajectory`** ⭐ — a student's mean-mark and attendance **trend across trimesters**;
  labels improving / declining / stable. Catches a strong student sliding.
- **`declining_students`** — cohort-wide, students whose last-trimester marks/attendance dropped
  most vs their own baseline. The single highest-value early-warning list.
- **`cohort_trend`** — how the whole batch's marks/attendance evolved term over term.

## Theme 2 — Single-pane heroes (dashboard landing tools)
- **`student_360`** ⭐ — ONE call: profile + trajectory + cohort percentile rank + risk flags +
  strengths/weaknesses + report-accuracy. The student-drawer hero.
- **`cohort_pulse`** ⭐ — ONE call: size, mean marks, mean attendance, pass rate, at-risk count,
  zeros, mark distribution. The dashboard landing screen.
- **`watchlist`** ⭐ — the auto-generated **intervention list**: composite risk + the *reason* +
  a *suggested action* per student.

## Theme 3 — Comparative / benchmarking
- **`student_percentile`** — a student's rank/percentile vs their cohort, per subject and overall.
- **`section_compare`** ⭐ — compare sections of the same subject (A vs B vs C). A teaching-quality /
  fairness signal no other tool surfaces.
- **`subject_difficulty`** — subjects ranked by lowest pass rate + most zeros (curriculum signal).

## Theme 4 — Assessment / curriculum insight
- **`assessment_breakdown`** ⭐ — cohort performance by component `kind` (quiz vs assignment vs
  project vs class-participation). Shows which *assessment types* drag scores.
- **`ungraded_components`** — what still needs grading / missing submissions (faculty workflow).
- **`grade_distribution`** — a subject's full distribution + skew (spot grading anomalies).

## Theme 5 — Correlation / data-story
- **`attendance_marks_link`** — the correlation between attendance and marks in this cohort, with
  the "students who attend but underperform" and "low attendance yet strong" outliers.
- **`anomalies`** — statistical outliers: a normally-strong student who cratered one component; a
  subject where the whole class scored low (possible marking/exam issue); attendance cliffs.

## Theme 6 — Eligibility / operational (institution rules)
- **`attendance_eligibility`** — students at/near the exam-eligibility attendance bar (e.g. <75%),
  and how many sessions from crossing it. Actionable before it's too late.
- **`roster_health`** — data completeness per scope (missing marks/attendance, ungraded rows) — a
  data-quality panel for the programme office.

---

## Build order
1. **Phase 1 (heroes, build now):** `student_trajectory`, `student_360`, `cohort_pulse`,
   `watchlist` — cross-trimester + single-pane, the biggest differentiation.
2. **Phase 2:** `section_compare`, `assessment_breakdown`, `subject_difficulty`, `declining_students`.
3. **Phase 3:** `attendance_eligibility`, `attendance_marks_link`, `anomalies`, `roster_health`.

All reuse the existing raw-data helpers (`cohort_rollup`, `courses_for`, `marks_for`,
`attendance_for`) + the cache, stay read-only and campus-scoped, and keep the response-budget
contract.
