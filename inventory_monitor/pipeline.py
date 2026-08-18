"""Orchestration shared by the `run` and `backfill` CLI commands.

Both commands do the same thing to each new PDF they encounter: save it,
parse it, diff it against the most recently *successfully parsed* snapshot
(if any), guard the diff, and either write a changelog entry or explain why
they refused to.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass

from . import changelog, config, csvio
from .diff import diff_rows
from .fetch import sha256_bytes
from .guards import ChurnGuardError, check_churn
from .parse import ParseError, parse_pdf

_SNAPSHOT_RE = re.compile(r"^data-file-inventory-(\d{4}-\d{2}-\d{2})\.pdf$")
_EXTRACTED_RE = re.compile(r"^inventory-(\d{4}-\d{2}-\d{2})\.csv$")


def snapshot_path(date: str) -> str:
    return os.path.join(config.SNAPSHOTS_DIR, f"data-file-inventory-{date}.pdf")


def extracted_path(date: str) -> str:
    return os.path.join(config.EXTRACTED_DIR, f"inventory-{date}.csv")


def suspect_extracted_path(date: str) -> str:
    return os.path.join(config.EXTRACTED_DIR, f"inventory-{date}.suspect.csv")


def _dated_files(directory: str, pattern: re.Pattern) -> list[tuple[str, str]]:
    """Return (date, path) pairs found in directory, sorted oldest first."""
    if not os.path.isdir(directory):
        return []
    out = []
    for path in glob.glob(os.path.join(directory, "*")):
        m = pattern.match(os.path.basename(path))
        if m:
            out.append((m.group(1), path))
    out.sort(key=lambda pair: pair[0])
    return out


def latest_snapshot() -> tuple[str, str] | None:
    """Latest (date, path) in snapshots/, regardless of whether it was
    ever successfully parsed -- used for the hash short-circuit."""
    files = _dated_files(config.SNAPSHOTS_DIR, _SNAPSHOT_RE)
    return files[-1] if files else None


def latest_extracted() -> tuple[str, str] | None:
    """Latest (date, path) in extracted/ among *successfully* parsed CSVs
    (i.e. excluding *.suspect.csv, which _EXTRACTED_RE doesn't match)."""
    files = _dated_files(config.EXTRACTED_DIR, _EXTRACTED_RE)
    return files[-1] if files else None


@dataclass
class Outcome:
    date: str
    status: str  # "unchanged" | "parse_error" | "guard_tripped" | "initial" | "diffed"
    detail: str
    row_count: int | None = None


def process_snapshot(pdf_bytes: bytes, date: str) -> Outcome:
    """Save pdf_bytes as the snapshot for `date`, parse it, and diff it
    against the latest previously-successfully-parsed extracted CSV (if
    any), writing a changelog entry if all guards pass.

    Assumes the caller has already decided this content is worth saving
    (i.e. its hash differs from the current latest snapshot, or there is
    no latest snapshot yet).
    """
    os.makedirs(config.SNAPSHOTS_DIR, exist_ok=True)
    os.makedirs(config.EXTRACTED_DIR, exist_ok=True)

    snap_path = snapshot_path(date)
    with open(snap_path, "wb") as f:
        f.write(pdf_bytes)

    try:
        rows = parse_pdf(snap_path)
    except ParseError as e:
        return Outcome(date=date, status="parse_error", detail=str(e))

    prev = latest_extracted()

    if prev is None:
        csvio.write_csv(rows, extracted_path(date))
        entry = changelog.format_initial_entry(date=date, snapshot=snap_path, row_count=len(rows))
        changelog.prepend_changelog(entry)
        return Outcome(date=date, status="initial", detail=f"{len(rows)} rows", row_count=len(rows))

    prev_date, prev_csv_path = prev
    prev_rows = csvio.read_csv(prev_csv_path)
    result = diff_rows(prev_rows, rows)

    try:
        check_churn(result, old_row_count=len(prev_rows), new_row_count=len(rows))
    except ChurnGuardError as e:
        # Keep the CSV for inspection, but under a name the "latest
        # extracted" glob won't pick up, so future diffs don't chain
        # through a snapshot we don't trust the diff for.
        csvio.write_csv(rows, suspect_extracted_path(date))
        return Outcome(date=date, status="guard_tripped", detail=str(e), row_count=len(rows))

    csvio.write_csv(rows, extracted_path(date))
    entry = changelog.format_entry(
        date=date,
        old_snapshot=snapshot_path(prev_date),
        new_snapshot=snap_path,
        old_row_count=len(prev_rows),
        new_row_count=len(rows),
        result=result,
    )
    changelog.prepend_changelog(entry)
    detail = f"{len(result.added)} added, {len(result.removed)} removed, {len(result.modified)} modified"
    return Outcome(date=date, status="diffed", detail=detail, row_count=len(rows))


def check_hash_against_latest(new_hash: str) -> tuple[bool, tuple[str, str] | None]:
    """Return (unchanged, latest_snapshot_pair)."""
    latest = latest_snapshot()
    if latest is None:
        return False, None
    _, path = latest
    with open(path, "rb") as f:
        existing_hash = sha256_bytes(f.read())
    return existing_hash == new_hash, latest
