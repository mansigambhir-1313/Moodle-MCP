-- Least-privilege DB credential for the Moodle Reports MCP.
--
-- Today the MCP connects with the Supabase service_role key, which bypasses RLS
-- and can read/write/delete everything. The tools only SELECT, but the KEY has
-- full power — one injected or future write could land. This migration creates a
-- SELECT-only role so the credential itself can't write, regardless of the code.
--
-- After applying: mint a JWT with `{"role":"reporting_readonly"}` signed with the
-- project's JWT secret and set it as SUPABASE_SERVICE_ROLE_KEY (supabase-py uses
-- that value as both apikey and bearer; PostgREST then runs every query as
-- reporting_readonly). See the README "Least-privilege DB role" section.
--
-- Idempotent. Apply via the Supabase SQL editor or apply_migration.

-- 1. The role (NOLOGIN — assumed via JWT through PostgREST's `authenticator`).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reporting_readonly') THEN
        CREATE ROLE reporting_readonly NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO reporting_readonly;

-- 2. SELECT only, on exactly the tables the MCP reads.
GRANT SELECT ON
    public.students,
    public.courses,
    public.enrolments,
    public.marks,
    public.attendance_sessions,
    public.student_reports,
    public.report_accuracy,
    public.student_report_jobs,
    public.extraction_runs
TO reporting_readonly;

-- 3. Belt-and-suspenders: ensure NO write privileges anywhere in public.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM reporting_readonly;

-- 4. Let PostgREST switch into this role when a JWT presents role=reporting_readonly.
GRANT reporting_readonly TO authenticator;

-- 5. If RLS is enabled on any read table, reporting_readonly is NOT a bypass role
--    (only service_role is), so give it an explicit read-all SELECT policy. The
--    MCP still enforces campus scoping in application code. Harmless if RLS is off.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'students','courses','enrolments','marks','attendance_sessions',
        'student_reports','report_accuracy','student_report_jobs','extraction_runs'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS reporting_readonly_select ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY reporting_readonly_select ON public.%I '
            'FOR SELECT TO reporting_readonly USING (true)', t);
    END LOOP;
END $$;

-- 6. Verify the role holds no write grants (fail loudly if it somehow does).
DO $$
DECLARE
    writes int;
BEGIN
    SELECT count(*) INTO writes
    FROM information_schema.role_table_grants
    WHERE grantee = 'reporting_readonly'
      AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE');
    IF writes > 0 THEN
        RAISE EXCEPTION 'reporting_readonly has % write grant(s) — not least-privilege', writes;
    END IF;
    RAISE NOTICE 'reporting_readonly: SELECT-only role ready; mint a JWT with role=reporting_readonly.';
END $$;
