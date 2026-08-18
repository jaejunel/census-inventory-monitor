"""End-to-end differ tests: build two synthetic inventory PDFs with a known
set of differences, parse both, and assert the differ reports exactly
those differences -- including that a row which only moved position is
*not* reported as a change."""

from __future__ import annotations

from inventory_monitor.diff import diff_rows
from inventory_monitor.parse import parse_pdf

from .pdf_builder import full_rows_to_lines, write_inventory_pdf


def _row(data_provider, data_type, years, entity_type="Federal", agency="Treasury", new_years=""):
    return {
        "entity_type": entity_type,
        "agency_or_program": agency,
        "data_provider": data_provider,
        "data_type": data_type,
        "years_available": years,
        "new_years_received": new_years,
    }


def test_add_remove_modify_and_reorder_no_false_positive(tmp_path):
    v1_rows = [
        _row("IRS", "Business Master File", "2020; 2021"),
        _row("IRS", "Form 1040 Returns", "2019; 2020"),
        _row("SSA", "Earnings Records", "2015; 2016"),
    ]
    # v2: same set as v1 but REORDERED, plus:
    #   - "Earnings Records" years_available changed (modified)
    #   - "Form 1040 Returns" removed
    #   - a new "W-2 Wage Data" row added
    v2_rows = [
        _row("SSA", "Earnings Records", "2015; 2016; 2017"),  # modified: years changed
        _row("IRS", "Business Master File", "2020; 2021"),  # unchanged, just moved
        _row("IRS", "W-2 Wage Data", "2022"),  # added
        # "Form 1040 Returns" is gone -- removed
    ]

    v1_path = str(tmp_path / "v1.pdf")
    v2_path = str(tmp_path / "v2.pdf")
    write_inventory_pdf(v1_path, full_rows_to_lines(v1_rows))
    write_inventory_pdf(v2_path, full_rows_to_lines(v2_rows))

    old_rows = parse_pdf(v1_path)
    new_rows = parse_pdf(v2_path)
    result = diff_rows(old_rows, new_rows)

    assert len(result.added) == 1
    assert result.added[0]["data_type"] == "W-2 Wage Data"

    assert len(result.removed) == 1
    assert result.removed[0]["data_type"] == "Form 1040 Returns"

    assert len(result.modified) == 1
    mod = result.modified[0]
    assert mod.key == ("SSA", "Earnings Records")
    assert mod.changes == {"years_available": ("2015; 2016", "2015; 2016; 2017")}

    # The reordered-but-otherwise-identical "Business Master File" row
    # must not show up anywhere in the diff.
    all_reported_keys = (
        {(r["data_provider"], r["data_type"]) for r in result.added}
        | {(r["data_provider"], r["data_type"]) for r in result.removed}
        | {m.key for m in result.modified}
    )
    assert ("IRS", "Business Master File") not in all_reported_keys


def test_identical_content_produces_no_diff(tmp_path):
    rows = [
        _row("IRS", "Business Master File", "2020"),
        _row("SSA", "Earnings Records", "2015"),
    ]
    v1_path = str(tmp_path / "v1.pdf")
    v2_path = str(tmp_path / "v2.pdf")
    write_inventory_pdf(v1_path, full_rows_to_lines(rows))
    write_inventory_pdf(v2_path, full_rows_to_lines(list(reversed(rows))))

    result = diff_rows(parse_pdf(v1_path), parse_pdf(v2_path))
    assert result.total_changes == 0


def test_duplicate_keys_are_matched_positionally_not_dropped():
    """The source document is known to contain occasional genuine
    duplicate (data_provider, data_type) pairs (e.g. the same file listed
    under two disclosure-category sections). The differ must not silently
    collapse or crash on these."""
    old_rows = [
        _row("Iowa (IA)", "Drivers License Data", "2020"),
        _row("Iowa (IA)", "Drivers License Data", "2021"),
    ]
    new_rows = [
        _row("Iowa (IA)", "Drivers License Data", "2020"),
        _row("Iowa (IA)", "Drivers License Data", "2022"),  # second copy's years changed
    ]
    result = diff_rows(old_rows, new_rows)
    assert result.added == []
    assert result.removed == []
    assert len(result.modified) == 1
    assert result.modified[0].changes == {"years_available": ("2021", "2022")}


def test_duplicate_key_surplus_reported_as_added_or_removed():
    old_rows = [_row("Iowa (IA)", "Drivers License Data", "2020")]
    new_rows = [
        _row("Iowa (IA)", "Drivers License Data", "2020"),
        _row("Iowa (IA)", "Drivers License Data", "2021"),
    ]
    result = diff_rows(old_rows, new_rows)
    assert len(result.added) == 1
    assert result.added[0]["years_available"] == "2021"
    assert result.removed == []
    assert result.modified == []
