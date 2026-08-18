"""Querying the Wayback Machine for historical captures of the source PDF.

archive.org's web.archive.org playback cluster is prone to brief outages
and transient connection refusals (observed directly while building this:
requests failed outright for a couple of minutes, then recovered on their
own). Every request in this module retries with backoff before giving up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import requests

from . import config

_MAX_ATTEMPTS = 5
_BACKOFF_SECONDS = 5


@dataclass
class Capture:
    timestamp: str  # raw Wayback timestamp, e.g. "20260527112824"
    digest: str
    date: str  # YYYY-MM-DD, derived from timestamp

    @property
    def archive_url(self) -> str:
        return f"https://web.archive.org/web/{self.timestamp}/{config.SOURCE_URL}"


def _get_with_retry(url: str, timeout: int, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS * attempt)
    raise last_exc  # type: ignore[misc]


def list_captures(timeout: int = 60) -> list[Capture]:
    """Return every distinct-content capture of config.SOURCE_URL, oldest
    first, via the CDX API with collapse=digest (so identical re-crawls of
    unchanged content are already deduplicated)."""
    params = {
        "url": config.SOURCE_URL,
        "output": "json",
        "fl": "timestamp,digest,statuscode",
        "filter": "statuscode:200",
        "collapse": "digest",
    }
    resp = _get_with_retry(config.WAYBACK_CDX_URL, timeout=timeout, params=params)
    data = resp.json()

    if not data:
        return []

    rows = data[1:]  # first row is the header ["timestamp","digest","statuscode"]
    captures = []
    for timestamp, digest, _status in rows:
        date = datetime.strptime(timestamp[:8], "%Y%m%d").strftime("%Y-%m-%d")
        captures.append(Capture(timestamp=timestamp, digest=digest, date=date))
    captures.sort(key=lambda c: c.timestamp)
    return captures


def download_capture(capture: Capture, timeout: int = 60) -> bytes:
    resp = _get_with_retry(capture.archive_url, timeout=timeout)
    content = resp.content
    if not content.startswith(b"%PDF"):
        raise ValueError(
            f"Wayback capture {capture.timestamp} does not look like a PDF "
            f"(first bytes: {content[:16]!r}) -- archive.org may have "
            f"served an interstitial/error page instead of the raw file."
        )
    return content
