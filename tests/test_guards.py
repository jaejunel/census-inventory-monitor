"""Tests for the three sanity guards: no-rows-found, invalid entity_type
(both parse.ParseError), and excess churn (guards.ChurnGuardError)."""

from __future__ import annotations

import pytest

from inventory_monitor.diff import diff_rows
from inventory_monitor.guards import ChurnGuardError, check_churn
from inventory_monitor.parse import ParseError, _looks_like_entity_type, parse_pdf

from .pdf_builder import NEW_ROW_GAP, full_rows_to_lines, write_inventory_pdf


def _row(i):
    return {
        "entity_type": "Federal",
        "agency_or_program": "Treasury",
        "data_provider": "IRS",
        "data_type": f"File {i:03d}",
        "years_available": "2020",
        "new_years_received": "",
    }


def test_blank_pdf_raises_parse_error(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    path = str(tmp_path / "blank.pdf")
    c = Canvas(path, pagesize=(900, 1000))
    c.drawString(30, 900, "This page intentionally left blank-ish.")
    c.save()

    with pytest.raises(ParseError):
        parse_pdf(path)


def test_churn_guard_trips_above_threshold():
    old_rows = [_row(i) for i in range(100)]
    # Replace 30 of 100 rows (30 removed + 30 added = 60/100 = 60% churn,
    # comfortably above the 25% threshold).
    new_rows = [_row(i) for i in range(70, 170)]

    result = diff_rows(old_rows, new_rows)
    with pytest.raises(ChurnGuardError):
        check_churn(result, old_row_count=len(old_rows), new_row_count=len(new_rows))


def test_churn_guard_allows_normal_change():
    old_rows = [_row(i) for i in range(100)]
    # Add 5, remove 5: 10/100 = 10% churn, well under the 25% threshold.
    new_rows = [_row(i) for i in range(5, 105)]

    result = diff_rows(old_rows, new_rows)
    check_churn(result, old_row_count=len(old_rows), new_row_count=len(new_rows))  # must not raise


def test_churn_guard_boundary_is_exclusive():
    from inventory_monitor import config

    old_rows = [_row(i) for i in range(100)]
    n_removed = int(100 * config.MAX_CHURN_FRACTION)  # exactly at the threshold
    # Remove n_removed rows and add none, so churn = n_removed, total =
    # max(old, new) = 100, fraction = exactly MAX_CHURN_FRACTION.
    new_rows = [_row(i) for i in range(n_removed, 100)]

    result = diff_rows(old_rows, new_rows)
    assert len(result.removed) == n_removed
    assert len(result.added) == 0
    # At exactly the threshold fraction, the guard must NOT trip (it only
    # trips when strictly above).
    check_churn(result, old_row_count=len(old_rows), new_row_count=len(new_rows))


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Federal", True),
        ("State", True),
        ("Other", True),
        ("Third Party", True),
        ("state", True),  # real in-source typo (2026-02-03 capture)
        ("Othere", True),  # real in-source typo (2026-05-27/06-05 captures)
        ("federal", True),  # case-insensitive
        ("Thursday, February 13, 2025", False),  # the real corruption case
        ("Thursday, February 13, 2025 Page 20 of 70", False),
        ("", False),
        ("Zorblaxian Committee", False),
    ],
)
def test_looks_like_entity_type(value, expected):
    assert _looks_like_entity_type(value) is expected


def test_entity_type_guard_tolerates_real_in_source_typos(tmp_path):
    """A small typo the Bureau's own PDF is known to contain (lowercase
    "state", or "Othere") must NOT be treated as corruption."""
    rows = [
        {
            "entity_type": "state",  # in-source typo, not a parser bug
            "agency_or_program": "Ohio Department of Higher Education",
            "data_provider": "Ohio Department of Higher Education",
            "data_type": "Degree Data",
            "years_available": "2020",
            "new_years_received": "",
        }
    ]
    path = str(tmp_path / "typo.pdf")
    write_inventory_pdf(path, full_rows_to_lines(rows))
    parsed = parse_pdf(path)  # must not raise
    assert parsed[0]["entity_type"] == "state"


def test_entity_type_guard_catches_leaked_content(tmp_path):
    """A garbage entity_type (e.g. leaked footer/header text that slipped
    past the strippers) forward-filled across several rows must raise
    ParseError, independent of whatever specifically caused the leak."""
    lines = [
        {"entity_type": "Zorblaxian Committee Quarterly Report", "gap": NEW_ROW_GAP},
        {"agency_or_program": "Treasury", "gap": NEW_ROW_GAP},
        {
            "data_provider": "IRS",
            "data_type": "Business Master File",
            "years_available": "2020",
            "gap": NEW_ROW_GAP,
        },
        {
            "data_provider": "IRS",
            "data_type": "Form 1040 Returns",
            "years_available": "2021",
            "gap": NEW_ROW_GAP,
        },
    ]
    path = str(tmp_path / "leaked.pdf")
    write_inventory_pdf(path, lines)
    with pytest.raises(ParseError, match="entity_type"):
        parse_pdf(path)


def test_end_to_end_parse_error_via_layout_change(tmp_path):
    """A PDF using a totally different (but still text-based) layout --
    simulating the pre-2023 template drift observed against the real
    document -- must be refused rather than silently mis-parsed."""
    from reportlab.pdfgen.canvas import Canvas

    path = str(tmp_path / "oldformat.pdf")
    c = Canvas(path, pagesize=(900, 1000))
    c.drawString(30, 900, "Census Bureau Administrative Data Inventory")
    c.drawString(30, 870, "Federal, State, Other Agency or Program Type Data Provider Data Type Years Available")
    c.drawString(30, 840, "Federal CNCS Corporation for National Service Alumni 2005-2010")
    c.save()

    with pytest.raises(ParseError):
        parse_pdf(path)
