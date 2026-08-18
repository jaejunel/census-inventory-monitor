#!/usr/bin/env python3
"""CLI for the Census Bureau administrative data file inventory monitor.

    python monitor.py run          Fetch the live PDF; if it changed, snapshot,
                                    parse, diff against history, and update
                                    CHANGELOG.md. This is what the monthly
                                    GitHub Actions workflow calls.

    python monitor.py backfill     Pull every distinct historical capture of
                                    the source PDF from the Wayback Machine
                                    and run the same pipeline over them in
                                    chronological order, building out
                                    CHANGELOG.md's history. Safe to re-run:
                                    captures already saved to snapshots/ are
                                    skipped.

Exit code is nonzero whenever something needs a human to look at it: a
parse failure, a guard trip, or (for `run`) an unresolved one from a
previous run that a since-unchanged PDF would otherwise hide.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from inventory_monitor import changelog, config, wayback
from inventory_monitor.fetch import fetch_live_pdf
from inventory_monitor.pipeline import (
    check_hash_against_latest,
    extracted_path,
    latest_extracted,
    latest_snapshot,
    process_snapshot,
    snapshot_path,
    suspect_extracted_path,
)


def cmd_run(args: argparse.Namespace) -> int:
    print(f"Fetching {config.SOURCE_URL} ...")
    fetched = fetch_live_pdf()
    print(f"  sha256 = {fetched.sha256}")

    unchanged, latest = check_hash_against_latest(fetched.sha256)

    if unchanged:
        changelog.append_check_log(
            f"- {args.date}: unchanged (sha256 {fetched.sha256[:12]}..., "
            f"matches {latest[1]})"
        )
        print("No change since the last recorded snapshot.")
        # Even though nothing changed, don't let a silently-unresolved
        # failure from a previous run hide behind "nothing to do" forever.
        return _check_unresolved_backlog()

    print(f"Content changed (or no prior snapshot) -- processing as {args.date}.")
    outcome = process_snapshot(fetched.content, args.date)
    return _report_outcome(outcome)


def _check_unresolved_backlog() -> int:
    snap = latest_snapshot()
    ext = latest_extracted()
    snap_date = snap[0] if snap else None
    ext_date = ext[0] if ext else None
    if snap_date is not None and snap_date != ext_date:
        print(
            f"WARNING: the latest snapshot ({snap_date}) was never turned "
            f"into a successful diff (latest successful extraction is "
            f"{ext_date!r}). A previous run's parse failure or churn-guard "
            f"trip is still unresolved -- see CHECK_LOG.md and "
            f"extracted/*.suspect.csv.",
            file=sys.stderr,
        )
        return 1
    return 0


def _report_outcome(outcome) -> int:
    if outcome.status == "parse_error":
        changelog.append_check_log(f"- {outcome.date}: PARSE ERROR -- {outcome.detail}")
        print(f"PARSE ERROR: {outcome.detail}", file=sys.stderr)
        return 1
    if outcome.status == "guard_tripped":
        changelog.append_check_log(f"- {outcome.date}: CHURN GUARD TRIPPED -- {outcome.detail}")
        print(f"CHURN GUARD TRIPPED: {outcome.detail}", file=sys.stderr)
        return 1
    if outcome.status == "initial":
        changelog.append_check_log(f"- {outcome.date}: initial snapshot ({outcome.detail})")
        print(f"Initial snapshot recorded: {outcome.detail}")
        return 0
    if outcome.status == "diffed":
        changelog.append_check_log(f"- {outcome.date}: diffed ({outcome.detail})")
        print(f"Changelog updated: {outcome.detail}")
        return 0
    raise AssertionError(f"unhandled outcome status {outcome.status!r}")


def cmd_backfill(args: argparse.Namespace) -> int:
    print("Querying the Wayback Machine CDX API for historical captures ...")
    captures = wayback.list_captures()
    print(f"Found {len(captures)} distinct-content captures.")

    seen_dates: set[str] = set()
    skipped: list[str] = []
    processed = 0
    failed = 0

    for capture in captures:
        if capture.date in seen_dates:
            print(f"  {capture.date}: SKIPPING second capture with same date "
                  f"({capture.timestamp}) -- keeping the first one seen.")
            continue
        seen_dates.add(capture.date)

        path = snapshot_path(capture.date)
        already_diffed = os.path.exists(extracted_path(capture.date)) or os.path.exists(
            suspect_extracted_path(capture.date)
        )
        if already_diffed:
            # Fully processed by a previous (possibly interrupted) backfill
            # run. Re-running process_snapshot would re-diff against
            # whatever is now "latest" and prepend a duplicate changelog
            # entry, so skip it entirely rather than just skipping the
            # download.
            print(f"  {capture.date}: already processed in a previous run, skipping.")
            continue

        if os.path.exists(path):
            print(f"  {capture.date}: snapshot already on disk, skipping download.")
            with open(path, "rb") as f:
                content = f.read()
        else:
            print(f"  {capture.date}: downloading ({capture.archive_url}) ...")
            try:
                content = wayback.download_capture(capture)
            except Exception as e:
                print(f"    FAILED to download: {e}", file=sys.stderr)
                skipped.append(f"{capture.date}: download failed -- {e}")
                failed += 1
                continue

        outcome = process_snapshot(content, capture.date)
        processed += 1
        print(f"    {outcome.status}: {outcome.detail}")
        if outcome.status == "parse_error":
            skipped.append(f"{capture.date}: parse error -- {outcome.detail}")
        elif outcome.status == "guard_tripped":
            skipped.append(f"{capture.date}: churn guard tripped -- {outcome.detail}")

    print()
    print(f"Backfill complete: {processed} captures processed, {failed} failed to download.")
    if skipped:
        print(f"{len(skipped)} captures did not produce a changelog entry:")
        for s in skipped:
            print(f"  - {s}")
        print(
            "These snapshots are still saved under snapshots/ for future "
            "re-parsing if the calibration in inventory_monitor/config.py "
            "is ever extended to cover their (likely older/different) "
            "layout."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Fetch the live PDF and process it if changed.")
    p_run.add_argument(
        "--date",
        default=None,
        help="Override the date used for the snapshot filename (default: today, UTC). Mainly for testing.",
    )
    p_run.set_defaults(func=cmd_run)

    p_backfill = sub.add_parser("backfill", help="Backfill history from the Wayback Machine.")
    p_backfill.set_defaults(func=cmd_backfill)

    args = parser.parse_args()

    if args.command == "run" and args.date is None:
        args.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
