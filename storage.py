"""Small private JSON records; Blob in production, explicit disk storage in dev."""
import copy
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path


class Store:
    def __init__(self):
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self.local = os.environ.get('LOCAL_DATA_DIR') if not os.environ.get('VERCEL') else None

    def get(self, key, *, cached=False):
        # Bound memory use, coalesce concurrent reads, and never share mutable JSON.
        # Fresh reads bypass both this cache and Blob's CDN. Cache only successful
        # reads (including missing records); storage failures must remain visible.
        with self._lock:
            entry = self._cache.get(key)
            if cached and entry and time.monotonic() < entry[0]:
                self._cache.move_to_end(key)
                return copy.deepcopy(entry[1])
            started = time.monotonic()
            value = self._get(key, cached=cached)
            self._cache[key] = (started + 60, copy.deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return value

    def _get(self, key, *, cached=False):
        if self.local:
            try:
                return json.loads((Path(self.local) / key).read_text())
            except FileNotFoundError:
                return None
        from vercel.blob import BlobClient, BlobNotFoundError
        with BlobClient() as client:
            try:
                result = client.get(key, access='private', use_cache=cached, timeout=10)
                return json.loads(result.content) if result else None
            except BlobNotFoundError:
                return None

    def put(self, key, value, overwrite=True):
        with self._lock:
            # Invalidate even on failure: a timed-out write may have reached Blob.
            self._cache.pop(key, None)
            self._put(key, value, overwrite)
            self._cache[key] = (time.monotonic() + 60, copy.deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)

    def _put(self, key, value, overwrite=True):
        body = json.dumps(value).encode()
        if self.local:
            path = Path(self.local) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            if not overwrite:
                with path.open('xb') as f:
                    f.write(body)
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                    f.write(body)
                    temp = f.name
                os.replace(temp, path)
            return
        from vercel.blob import BlobClient
        with BlobClient() as client:
            client.put(key, body, access='private', content_type='application/json',
                       add_random_suffix=False, overwrite=overwrite,
                       cache_control_max_age=60)

    def claim(self, key):
        """Immutable five-minute claim. A killed invocation can't block future slots."""
        with self._lock:
            entry = self._cache.get(key)
            if entry and time.monotonic() < entry[0] and entry[1] is not None:
                return False
            try:
                self.put(key, {'claimed': True}, overwrite=False)
                return True
            except Exception:
                # Distinguish an existing claim from storage/auth failures.
                if self.get(key) is not None:
                    return False
                raise
