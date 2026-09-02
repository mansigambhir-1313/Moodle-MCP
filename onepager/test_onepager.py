"""Onepager verification (plain asserts, same style as tests/test_hardening.py).

Run from this directory:  python test_onepager.py
No network, no credentials, no model calls.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = load("build_report")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}{('  → ' + str(detail)) if detail else ''}")


def subj(name, track, you, cls, att_you, att_cls, comps=None):
    return {"subject": name, "track": track, "you_pct": you, "class_pct": cls,
            "att_you": att_you, "att_class": att_cls,
            "components": comps or [{"component": "Assignment", "kind": "CE",
                                     "you_pct": you, "class_pct": cls}]}


def base(subjects):
    return {"student": {"id": "T1", "name": "Test Student", "campus": "Noida", "batch": "2024-26"},
            "trimester": "5", "data_date": "1 January 2026", "benchmark": "same-class average",
            "tracks": ["Finance", "General management"], "store": {"ios": "x", "android": "y"},
            "subjects": subjects}


def phase1_pattern_kinds():
    print("PHASE 1 — highlight selection: three kinds, each with a printed rationale")
    # split: attendance >= class+2 AND marks <= class-2
    f = br.facts(base([subj("Wealth Management", "Finance", 60.0, 70.0, 90.0, 80.0),
                       subj("Philosophy", "General management", 80.0, 75.0, 70.0, 75.0)]))
    check("attended-more-scored-less wins when margins are real", f["pattern_kind"] == "split", f["pattern_kind"])
    check("rationale names both margins", "+10" in f["pattern_why"] and "-10" in f["pattern_why"], f["pattern_why"])

    # a 0.3-pt attendance edge must NOT count as a split
    f = br.facts(base([subj("Wealth Management", "Finance", 60.0, 70.0, 80.3, 80.0),
                       subj("Philosophy", "General management", 80.0, 75.0, 70.0, 75.0)]))
    check("trivial attendance edge does not make a split", f["pattern_kind"] != "split", f["pattern_kind"])

    # strength: best subject >= +3, no split
    f = br.facts(base([subj("Wealth Management", "Finance", 85.0, 75.0, 70.0, 75.0),
                       subj("Philosophy", "General management", 74.0, 75.0, 70.0, 75.0)]))
    check("clear strength selected", f["pattern_kind"] == "strength", f["pattern_kind"])
    check("strength highlights the best subject", f["pattern"]["subject"] == "Wealth Management")

    # gap: nothing else fires
    f = br.facts(base([subj("Wealth Management", "Finance", 74.0, 75.0, 70.0, 75.0),
                       subj("Philosophy", "General management", 55.0, 75.0, 70.0, 75.0)]))
    check("widest gap is the fallback", f["pattern_kind"] == "gap", f["pattern_kind"])
    check("gap rationale is plain language (no metaphors)",
          "pays back" not in f["pattern_why"] and "fix this one first" in f["pattern_why"])


def phase2_interview_picks():
    print("\nPHASE 2 — interview talking points are pre-chosen, never quizzes/tests")
    comps = [{"component": "Quiz 2", "kind": "CE", "you_pct": 95.0, "class_pct": 70.0},
             {"component": "MCQ Tests", "kind": "CE", "you_pct": 90.0, "class_pct": 70.0},
             {"component": "Case Analysis", "kind": "CE", "you_pct": 60.0, "class_pct": 70.0}]
    f = br.facts(base([subj("Wealth Management", "Finance", 70.0, 75.0, 70.0, 75.0, comps)]))
    talk = next(t for t in f["tracks"] if t["track"] == "Finance")["talk_about"]
    check("best quiz is skipped even when it is the top score",
          talk and talk["component"] == "Case Analysis", talk and talk["component"])


def phase3_validator():
    print("\nPHASE 3 — validator: numbers, jargon, sentence length, repetition")
    d = base([subj("Wealth Management", "Finance", 60.0, 70.0, 90.0, 80.0),
              subj("Philosophy", "General management", 80.0, 75.0, 70.0, 75.0)])
    f = br.facts(d)

    def narr(**over):
        n = {"headline": "Hi Test. You scored above your class average in 1 of 2 subjects.",
             "subtitle": "This report compares your marks and attendance with your class.",
             "pattern_title": "x", "attendance_line": "Attendance at or above class in 1 of 2 subjects.",
             "pattern_text": ("In Wealth Management you got 60.0% and the class got 70.0%. "
                              "Your attendance was 90.0% and the class was 80.0%. "
                              "You attended more but the marks did not follow. "
                              "The Assignment shows 60.0% against the class's 70.0%."),
             "tracks": [{"track": "Finance", "title": "Revise notes weekly",
                         "learning": "Your Assignment in Wealth Management shows the material was not revised. Next trimester, read your notes before every class.",
                         "interview": "Talk about your Assignment. If asked about weak areas, say you now revise weekly."},
                        {"track": "General management", "title": "Keep writing essays",
                         "learning": "Your Assignment in Philosophy shows steady written work. Next trimester, keep a short summary habit after each topic.",
                         "interview": "Mention your Assignment in Philosophy. If asked about weak areas, say you practise summaries."}]}
        n.update(over)
        return n

    check("clean narrative passes", br.validate(narr(), f) == [], br.validate(narr(), f))
    bad = narr(headline="Hi Test. You scored 93.7% overall.")
    check("invented number rejected", any("93.7" in p for p in br.validate(bad, f)))
    bad = narr()
    bad["tracks"][0]["learning"] = "Leverage your synthesis framework to optimise recall. Next trimester, read your notes on Wealth Management before class."
    check("jargon rejected", any("jargon" in p for p in br.validate(bad, f)))
    bad = narr()
    bad["tracks"][0]["learning"] = ("Your Assignment in Wealth Management shows that the study material which was taught across the "
                                    "trimester was not revised carefully enough between the classes to stay fresh. Read notes daily.")
    check("over-long sentence rejected", any("too long" in p for p in br.validate(bad, f)))
    bad = narr()
    bad["tracks"][1]["interview"] = bad["tracks"][0]["interview"]
    check("repeated sentence across cards rejected", any("repeated" in p for p in br.validate(bad, f)))
    bad = narr()
    bad["tracks"][0]["interview"] = "Talk about your marks. If asked about weak areas, stay calm."
    check("interview must name the chosen piece of work", any("must mention" in p for p in br.validate(bad, f)))


def phase4_fetch_helpers():
    print("\nPHASE 4 — fetch helpers: subject cleaning + track mapping")
    fs = load("fetch_student")
    check("'Business Forecasting_GR2' cleans", fs.clean_subject("Business Forecasting_GR2") == "Business Forecasting")
    check("double underscore cleans", fs.clean_subject("Financial Modelling and Analysis__GR1") == "Financial Modelling and Analysis")
    check("finance mapped", fs.track_of("Wealth Management") == "Finance")
    check("marketing mapped", fs.track_of("Customer Relationship Management") == "Marketing")
    check("analytics mapped", fs.track_of("Business Forecasting") == "Analytics & Operations")
    check("default track", fs.track_of("Introduction to Philosophy") == "General management")


def phase5_sample_renders():
    print("\nPHASE 5 — the shipped fictional sample builds a full page offline")
    d = json.load(open(os.path.join(HERE, "sample", "student.json")))
    n = json.load(open(os.path.join(HERE, "sample", "narrative.json")))
    f = br.facts(d)
    probs = br.validate(n, f)
    check("sample narrative passes the validator", probs == [], probs)
    html = br.render_html(d, f, n, n.get("_model", "sample"))
    check("html renders with charts", "divergingBar" in html and "statTiles" in html)
    check("no real student identity in the sample",
          "Sample Student" in html and "JN24PG" not in html and "JN24SM" not in html)


if __name__ == "__main__":
    phase1_pattern_kinds()
    phase2_interview_picks()
    phase3_validator()
    phase4_fetch_helpers()
    phase5_sample_renders()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
