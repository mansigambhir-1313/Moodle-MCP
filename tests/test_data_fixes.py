"""Data-correctness fixes: per-subject % must not exceed 100, and section labels
must be the real section token (not the subject abbreviation).

Run:  ../moodle-agent/.venv/bin/python tests/test_data_fixes.py   (from moodle-mcp/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}{('  → ' + detail) if detail else ''}")


def test_subject_rollup_no_over_100():
    print("FIX #8 — get_student per-subject % never exceeds 100 (end-term null-max excluded)")
    from tools.students import _subject_rollup

    courses = {"c1": {"subject": "Accounting for Business", "trimester": "1"}}
    # Continuous-eval components (56/60) + an End-Term (__total__) row: obtained 31, max NULL.
    marks = [
        {"course_id": "c1", "graded": True, "obtained_score": 8.0, "max_score": 10.0,
         "component_label": "Class Participation", "kind": "CE"},
        {"course_id": "c1", "graded": True, "obtained_score": 17.5, "max_score": 20.0,
         "component_label": "Group Project", "kind": "CE"},
        {"course_id": "c1", "graded": True, "obtained_score": 30.5, "max_score": 30.0,
         "component_label": "Quizzes", "kind": "CE"},
        {"course_id": "c1", "graded": True, "obtained_score": 31.0, "max_score": None,
         "component_label": "__total__", "kind": "ET"},
    ]
    out = _subject_rollup(courses, marks, [])
    pct = out[0]["overall_mark_pct"]
    # 56.0 / 60.0 = 93.3, NOT (8+17.5+30.5+31)/60 = 145%
    check("overall_mark_pct == 93.3 (CE only)", pct == 93.3, str(pct))
    check("overall_mark_pct <= 100", pct is not None and pct <= 100, str(pct))
    # the ET row is still visible as a component (transparency), just not double-counted
    check("end-term row still listed in components",
          any(c["component"] == "__total__" for c in out[0]["components"]))


def test_course_section_parsing():
    print("FIX #9 — course_section returns the real section, both code formats")
    from tools.common import course_section

    check("new 5-field '20201_27_1_A_AFB' -> 'A'", course_section("20201_27_1_A_AFB") == "A", course_section("20201_27_1_A_AFB"))
    check("new '20201_27_1_MA_AFB' -> 'MA'", course_section("20201_27_1_MA_AFB") == "MA")
    check("new '20201_27_1_SM_AFB' -> 'SM'", course_section("20201_27_1_SM_AFB") == "SM")
    check("old 4-field '30503_26_3_C' -> 'C'", course_section("30503_26_3_C") == "C")
    check("empty code -> None", course_section("") is None)


if __name__ == "__main__":
    test_subject_rollup_no_over_100()
    test_course_section_parsing()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
