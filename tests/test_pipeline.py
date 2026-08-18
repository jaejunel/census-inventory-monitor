"""Integration tests for inventory_monitor.pipeline: the wiring between
parse, diff, guards, and changelog that `monitor.py` actually calls."""

from __future__ import annotations

import os

from inventory_monitor import changelog, pipeline

from .pdf_builder import full_rows_to_lines, write_inventory_pdf


def _row(i, year="2020"):
    return {
        "entity_type": "Federal",
        "agency_or_program": "Treasury",
        "data_provider": "IRS",
        "data_type": f"File {i:03d}",
        "years_available": year,
        "new_years_received": "",
    }


def _pdf_bytes(tmp_path, name, rows):
    path = str(tmp_path / name)
    write_inventory_pdf(path, full_rows_to_lines(rows))
    with open(path, "rb") as f:
        return f.read()


def test_first_snapshot_is_initial(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = _pdf_bytes(tmp_path, "src.pdf", [_row(i) for i in range(5)])

    outcome = pipeline.process_snapshot(data, "2026-01-01")

    assert outcome.status == "initial"
    assert outcome.row_count == 5
    assert os.path.exists(pipeline.snapshot_path("2026-01-01"))
    assert os.path.exists(pipeline.extracted_path("2026-01-01"))
    changelog_text = open("CHANGELOG.md").read()
    assert "## 2026-01-01" in changelog_text
    assert "Initial snapshot" in changelog_text


def test_normal_diff_appends_changelog_entry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 20 rows, drop one and add one -> 2/20 = 10% churn, safely under the
    # 25% guard threshold.
    data1 = _pdf_bytes(tmp_path, "src1.pdf", [_row(i) for i in range(20)])
    data2 = _pdf_bytes(tmp_path, "src2.pdf", [_row(i) for i in range(1, 21)])  # drop 0, add 20

    pipeline.process_snapshot(data1, "2026-01-01")
    outcome = pipeline.process_snapshot(data2, "2026-02-01")

    assert outcome.status == "diffed"
    changelog_text = open("CHANGELOG.md").read()
    # Newest entry ("## 2026-02-01") must appear ABOVE the older one.
    assert changelog_text.index("## 2026-02-01") < changelog_text.index("## 2026-01-01")
    assert "File 000" in changelog_text  # removed
    assert "File 020" in changelog_text  # added


def test_guard_trip_writes_suspect_csv_and_skips_changelog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data1 = _pdf_bytes(tmp_path, "src1.pdf", [_row(i) for i in range(100)])
    # Replace 60 of 100 rows -- well above the churn threshold.
    data2 = _pdf_bytes(tmp_path, "src2.pdf", [_row(i) for i in range(60, 160)])

    pipeline.process_snapshot(data1, "2026-01-01")
    before = open("CHANGELOG.md").read()
    outcome = pipeline.process_snapshot(data2, "2026-02-01")
    after = open("CHANGELOG.md").read()

    assert outcome.status == "guard_tripped"
    assert before == after  # no changelog entry was written
    assert os.path.exists(pipeline.suspect_extracted_path("2026-02-01"))
    assert not os.path.exists(pipeline.extracted_path("2026-02-01"))
    # The snapshot PDF itself is still kept as evidence.
    assert os.path.exists(pipeline.snapshot_path("2026-02-01"))
    # And the next diff should still chain from the last *good* extraction
    # (2026-01-01), not the suspect one.
    latest_date, _ = pipeline.latest_extracted()
    assert latest_date == "2026-01-01"


def test_hash_short_circuit_detects_unchanged_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = _pdf_bytes(tmp_path, "src.pdf", [_row(i) for i in range(5)])
    pipeline.process_snapshot(data, "2026-01-01")

    from inventory_monitor.fetch import sha256_bytes

    unchanged, latest = pipeline.check_hash_against_latest(sha256_bytes(data))
    assert unchanged is True
    assert latest[0] == "2026-01-01"

    different_data = _pdf_bytes(tmp_path, "src2.pdf", [_row(i) for i in range(6)])
    unchanged, latest = pipeline.check_hash_against_latest(sha256_bytes(different_data))
    assert unchanged is False


def test_latest_entry_extracts_only_the_newest_section(tmp_path, monkeypatch):
    """The GitHub Actions workflow uses changelog.latest_entry() to build
    an issue body -- it must return only the newest "## " section, not the
    whole file or a stale one."""
    monkeypatch.chdir(tmp_path)
    data1 = _pdf_bytes(tmp_path, "src1.pdf", [_row(i) for i in range(20)])
    data2 = _pdf_bytes(tmp_path, "src2.pdf", [_row(i) for i in range(1, 21)])

    pipeline.process_snapshot(data1, "2026-01-01")
    pipeline.process_snapshot(data2, "2026-02-01")

    entry = changelog.latest_entry()
    assert entry.startswith("## 2026-02-01")
    assert "## 2026-01-01" not in entry
    assert "File 020" in entry  # the addition from the newer diff
