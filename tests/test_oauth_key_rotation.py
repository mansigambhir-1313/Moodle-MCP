"""OAuth-storage encryption keyset / rotation tolerance (plain asserts).

Proves the fix for the "client ID not found" incident: a single key behaves exactly
as before (existing rows stay readable), and a COMMA-SEPARATED keyset decrypts data
written under any listed key while encrypting with the first — so rotating
OAUTH_STORAGE_ENCRYPTION_KEY never orphans already-registered OAuth clients/tokens.

Run:  ../moodle-agent/.venv/bin/python tests/test_oauth_key_rotation.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from cryptography.fernet import Fernet, InvalidToken, MultiFernet  # noqa: E402

from oauth_storage import oauth_fernet  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


OLD = "old-encryption-material-aaaaaaaaaaaa"
NEW = "new-encryption-material-bbbbbbbbbbbb"
BLOB = b'{"client_id":"693bde70","redirect_uris":["https://x/cb"]}'

# --------------------------------------------------------------------------
print("\n[ single key — unchanged behaviour, deterministic ]")
f1 = oauth_fernet(OLD)
check("single material -> a plain Fernet", isinstance(f1, Fernet))
tok = f1.encrypt(BLOB)
check("round-trips its own data", f1.decrypt(tok) == BLOB)
f1b = oauth_fernet(OLD)
check("derivation is deterministic (same material -> same key)",
      oauth_fernet(OLD).decrypt(f1b.encrypt(BLOB)) == BLOB)

print("\n[ empty / missing material ]")
check("no material -> None", oauth_fernet("") is None and oauth_fernet(None) is None)
check("whitespace/commas only -> None", oauth_fernet("  , ,") is None)

# --------------------------------------------------------------------------
print("\n[ keyset rotation — the incident fix ]")
# A client was written under OLD. Ops rotates by putting NEW first, keeping OLD.
token_written_under_old = oauth_fernet(OLD).encrypt(BLOB)
rotated = oauth_fernet(f"{NEW},{OLD}")
check("keyset -> a MultiFernet", isinstance(rotated, MultiFernet))
check("keyset still decrypts data written under the OLD key (no orphaning)",
      rotated.decrypt(token_written_under_old) == BLOB)
# New writes use the FIRST (new) key, readable by a NEW-only build.
token_written_after_rotation = rotated.encrypt(BLOB)
check("keyset encrypts with the FIRST (new) key",
      oauth_fernet(NEW).decrypt(token_written_after_rotation) == BLOB)

print("\n[ key isolation — a wrong/absent key cannot decrypt ]")
raised = False
try:
    oauth_fernet("unrelated-material-zzzzzzzzzzzz").decrypt(token_written_under_old)
except InvalidToken:
    raised = True
check("data is NOT readable by a key outside the set", raised)
# Order independence: OLD present anywhere in the set recovers the row.
check("OLD listed second still recovers", oauth_fernet(f"{NEW},{OLD}").decrypt(token_written_under_old) == BLOB)
check("OLD listed first still recovers", oauth_fernet(f"{OLD},{NEW}").decrypt(token_written_under_old) == BLOB)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
