"""Persistent OAuth state, so DEPLOYS NO LONGER LOG EVERYONE OUT.

FastMCP's OAuthProxy keeps all its OAuth state — dynamic client registrations,
JTI→upstream-token mappings, refresh-token metadata — in a pluggable
`client_storage` (AsyncKeyValue). Its default is a DiskStore under the app
home, which is EPHEMERAL on Render: every deploy wiped it, invalidating every
issued token and forcing all signed-in faculty back through Google.

This module provides a Supabase-backed store (table: mcp_oauth_kv) and wraps
it in the same FernetEncryptionWrapper FastMCP uses for its own default —
with the encryption key derived from OAUTH_JWT_SIGNING_KEY exactly the way
FastMCP derives it — so every value in the database is ciphertext. The
signing key lives only in the Render env: a DB leak yields no usable tokens.

The MCP's DB role (reporting_readonly) has CRUD on this ONE operational table
and remains SELECT-only on all student data (enforced by grants + RLS).
"""
import logging
import random

import httpx
from key_value.aio.stores.base import BaseStore
from key_value.shared.utils.managed_entry import ManagedEntry
from key_value.shared.utils.serialization import BasicSerializationAdapter
from typing_extensions import override

log = logging.getLogger("moodle-mcp.oauth-storage")

_TABLE = "mcp_oauth_kv"


class SupabaseKVStore(BaseStore):
    """AsyncKeyValue over one Supabase (PostgREST) table. Async httpx client —
    never blocks the event loop. Values are stored as opaque text (the caller
    wraps this store in encryption); expiry rides in a queryable column so the
    base class's TTL semantics survive restarts too."""

    def __init__(self, *, url: str, apikey: str, bearer: str,
                 default_collection: str | None = None):
        self._rest = f"{url.rstrip('/')}/rest/v1/{_TABLE}"
        self._headers = {"apikey": apikey, "Authorization": f"Bearer {bearer}",
                         "Content-Type": "application/json"}
        self._http = httpx.AsyncClient(timeout=10.0)
        self._adapter = BasicSerializationAdapter(date_format="isoformat",
                                                  value_format="dict")
        super().__init__(default_collection=default_collection,
                         serialization_adapter=self._adapter, stable_api=True)

    @override
    async def _get_managed_entry(self, *, key: str, collection: str) -> ManagedEntry | None:
        r = await self._http.get(
            self._rest, headers=self._headers,
            params={"collection": f"eq.{collection}", "key": f"eq.{key}",
                    "select": "value", "limit": "1"})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        try:
            return self._adapter.load_json(json_str=rows[0]["value"])
        except Exception:  # noqa: BLE001 — a corrupt row reads as a miss, never a crash
            log.warning("undecodable oauth-kv row (collection=%s) — treating as miss",
                        collection)
            return None

    @override
    async def _put_managed_entry(self, *, key: str, collection: str,
                                 managed_entry: ManagedEntry) -> None:
        payload = {"collection": collection, "key": key,
                   "value": self._adapter.dump_json(entry=managed_entry, key=key,
                                                    collection=collection),
                   "expires_at": managed_entry.expires_at_isoformat,
                   "updated_at": "now()"}
        r = await self._http.post(
            self._rest, headers={**self._headers,
                                 "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "collection,key"}, json=[payload])
        r.raise_for_status()
        if random.random() < 0.02:  # opportunistic purge of expired rows (~1 in 50 writes)
            try:
                await self._http.delete(
                    self._rest, headers=self._headers,
                    params={"expires_at": "lt.now()"})
            except httpx.HTTPError:  # purge is hygiene, never a failure path
                pass

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        r = await self._http.delete(
            self._rest, headers={**self._headers, "Prefer": "return=representation"},
            params={"collection": f"eq.{collection}", "key": f"eq.{key}"})
        r.raise_for_status()
        return bool(r.json())


def build_oauth_storage(settings):
    """FernetEncryptionWrapper(SupabaseKVStore) with the encryption key derived
    from OAUTH_JWT_SIGNING_KEY exactly as FastMCP derives it for its own default
    store (jwt-signing-key derivation, then the storage-encryption salt) — the
    same env value therefore decrypts the same rows across every deploy.
    Returns None when prerequisites are missing (FastMCP falls back to its
    ephemeral default, i.e. today's behaviour)."""
    if not (settings.supabase_url and settings.supabase_service_role_key
            and settings.oauth_jwt_signing_key):
        return None
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.jwt_issuer import derive_jwt_key
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    signing_key_bytes = derive_jwt_key(
        low_entropy_material=settings.oauth_jwt_signing_key,
        salt="fastmcp-jwt-signing-key")
    storage_key = derive_jwt_key(
        high_entropy_material=signing_key_bytes.decode(),
        salt="fastmcp-storage-encryption-key")
    store = SupabaseKVStore(
        url=settings.supabase_url,
        apikey=settings.supabase_anon_key or settings.supabase_service_role_key,
        bearer=settings.supabase_service_role_key)
    return FernetEncryptionWrapper(key_value=store, fernet=Fernet(key=storage_key))
