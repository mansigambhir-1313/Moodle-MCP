"""Bounded in-process TTL cache — sync OrderedDict LRU with per-entry expiry.
OOM invariant: no unbounded module-level dict, ever."""
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, maxsize: int, ttl: float):
        self.maxsize = maxsize
        self.ttl = ttl
        self._d: "OrderedDict[object, tuple[float, object]]" = OrderedDict()

    def get(self, key, default=None):
        item = self._d.get(key)
        if item is None:
            return default
        ts, val = item
        if time.monotonic() - ts > self.ttl:
            self._d.pop(key, None)
            return default
        self._d.move_to_end(key)
        return val

    def set(self, key, val):
        self._d[key] = (time.monotonic(), val)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)
