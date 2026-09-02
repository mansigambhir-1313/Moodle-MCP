# One-page student report (reference implementation)

A single-A4 redesign of the trimester student report, in the Rehearsal editorial
layout: deviation chart (you vs same-class average), attendance bars, a
self-justifying "why this subject" highlight, and four specialisation-track
takeaway cards written for a closed trimester (carry-forward learnings + interview
lines — never "re-attempt"). Language is enforced plain Indian English.

This lives here as a **standalone reference implementation** because it reads the
same Supabase tables this MCP serves. Its natural long-term home is the
`moodle-agent` report service (the renderer behind `create_report`) — port it
there when convenient; nothing below depends on this repo's server code.

## Design decisions (all enforced in code, not by the model)

- **Numbers are never model-written.** `fetch_student.py` computes every figure;
  `validate()` rejects any number in the prose that is not in the data, plus
  jargon, >18-word sentences, repeated sentences across cards, and wrong picks.
  The model gets up to 3 corrective retries, then a fallback model.
- **The highlight explains itself.** One of three deterministic cases, each with
  a printed "Why this subject" rationale: *attended more, scored less* (needs
  ≥2-pt margins both sides), *clearest strength* (best subject ≥ +3 pts), else
  *widest gap*. Titles are deterministic too.
- **Interview talking points are pre-chosen**: best non-quiz/MCQ/test component
  per track. (Lesson learned: telling the model "never mention a quiz" fails for
  students whose best work *is* quizzes — pre-computing the pick fixed a 100%
  fallback rate on the cheap model.)
- **Model-agnostic prompt**: the JSON contract is stated in the prompt itself;
  `response_format` json_schema is offered but optional, and the parser tolerates
  fenced or prose-wrapped JSON. Benchmarked default: `google/gemini-2.5-flash`
  (~$0.003/report, passes validation first attempt); fallback `openai/gpt-5-mini`.
  ~2,500 reports ≈ $7–8.
- **Charts** come from the vendored dependency-free `assets/charts.js`
  (SVG, no CDN): `divergingBar` (auto-scaling axis — a −62 outlier taught us),
  `bullet`, `statTiles`.

## Usage

```bash
pip install -r requirements.txt          # supabase + optional qrcode/pillow

# 1. fetch (same env vars as the server: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
#    SUPABASE_ANON_KEY when the DB key is a custom-role JWT)
python fetch_student.py --campus noida --batch 2024-26 --trimester 5 \
    --student JN24PG013 --out student.json

# 2. build (needs OPENROUTER_API_KEY; writes HTML + narrative.json)
python build_report.py --data student.json --out out/

# 3. render to PDF (headless Chrome)
bash render.sh "out/<Name> (<ID>) - Trimester 5.html"
```

Re-render without a model call (layout iteration): `--narrative out/narrative.json`.

Try it with no credentials at all using the fictional sample:

```bash
python build_report.py --data sample/student.json --out out/ \
    --narrative sample/narrative.json
```

## Tests

`python test_onepager.py` — pattern-kind selection (all three cases), number
validator, jargon/length/repetition checks, subject-name cleaning, track mapping.
No network, no credentials.

## Boundaries

- Read-only: only SELECTs against `extraction_runs`, `courses`, `marks`,
  `attendance_sessions`, `students`. Never writes to `student_reports` or the
  storage bucket (integration with that catalogue belongs in `moodle-agent`).
- Never commit generated reports: they contain real student names and marks.
  `out/` is gitignored; only the fictional `sample/` ships.
