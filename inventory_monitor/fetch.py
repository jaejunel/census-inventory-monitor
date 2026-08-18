"""Fetching the live PDF and computing its hash."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import requests

from . import config


@dataclass
class FetchResult:
    content: bytes
    sha256: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_live_pdf(timeout: int = 60) -> FetchResult:
    """Download the current PDF from config.SOURCE_URL."""
    resp = requests.get(config.SOURCE_URL, timeout=timeout)
    resp.raise_for_status()
    content = resp.content
    if not content.startswith(b"%PDF"):
        raise ValueError(
            f"Response from {config.SOURCE_URL} does not look like a PDF "
            f"(first bytes: {content[:16]!r})"
        )
    return FetchResult(content=content, sha256=sha256_bytes(content))
