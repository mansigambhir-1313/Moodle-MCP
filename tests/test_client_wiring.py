"""_client() wiring: with an anon key, the apikey is the anon key and the DB key
rides as the bearer (so a custom-role JWT works through the gateway); without one,
the DB key is used for both (unchanged service_role behaviour).

Run:  ../moodle-agent/.venv/bin/python tests/test_client_wiring.py   (from moodle-mcp/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import supabase_client as sc

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}{('  → ' + detail) if detail else ''}")


class _FakePostgrest:
    def __init__(self):
        self.bearer = None

    def auth(self, token):
        self.bearer = token


class _FakeClient:
    def __init__(self, url, key):
        self.url = url
        self.apikey = key
        self.postgrest = _FakePostgrest()


def _build(anon, db_key):
    created = {}

    def fake_create(url, key):
        c = _FakeClient(url, key)
        created["c"] = c
        return c

    sc.create_client = fake_create           # patch the name _client() uses
    sc._client_singleton = None              # reset the singleton
    sc.settings.supabase_anon_key = anon
    sc.settings.supabase_service_role_key = db_key
    return sc._client(), created["c"]


def main():
    print("anon key set (reporting_readonly deployment)")
    client, created = _build("ANON_KEY", "READONLY_JWT")
    check("apikey is the anon key", created.apikey == "ANON_KEY")
    check("DB key attached as bearer (postgrest.auth)", client.postgrest.bearer == "READONLY_JWT")

    print("no anon key (legacy service_role deployment)")
    client2, created2 = _build("", "SERVICE_ROLE_KEY")
    check("apikey is the DB key", created2.apikey == "SERVICE_ROLE_KEY")
    check("no bearer override", client2.postgrest.bearer is None)

    sc._client_singleton = None  # leave clean
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
