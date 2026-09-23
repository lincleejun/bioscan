"""Thin HTTP client for the bioscan service (spec section 4). stdlib only."""
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator

DEFAULT_URL = os.environ.get("BIOSCAN_URL", "http://127.0.0.1:8765")


class ServiceError(Exception):
    pass


def _open(req, url, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        raise ServiceError(f"service returned HTTP {e.code}: {body}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise ServiceError(f"cannot reach bioscan service at {url} ({reason}); start it with `bioscan serve`") from None


def health(url: str = DEFAULT_URL) -> dict:
    with _open(url.rstrip("/") + "/health", url, timeout=5) as r:
        body = r.read()
    try:
        return json.loads(body)
    except ValueError:
        raise ServiceError(f"{url}/health did not return JSON ({body[:80]!r}); is another process on that port?") from None


def run(payload: dict, url: str = DEFAULT_URL) -> Iterator[bytes]:
    """POST /run and yield raw NDJSON lines (without trailing newline) as they arrive."""
    req = urllib.request.Request(
        url.rstrip("/") + "/run",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    # No read timeout: a big batch on a cold service can be silent for a long time.
    with _open(req, url, timeout=None) as r:
        for line in r:  # HTTPResponse iterates line by line, chunked transfer included
            line = line.rstrip(b"\r\n")
            if line:
                yield line
