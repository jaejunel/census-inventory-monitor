"""Tests for inventory_monitor.parse: header/footer stripping, forward-fill,
and wrapped-cell merging."""

from __future__ import annotations

from inventory_monitor.parse import ParseError, parse_pdf

from .pdf_builder import NEW_ROW_GAP, WRAP_GAP, full_rows_to_lines, write_inventory_pdf


def test_basic_round_trip(tmp_path):
    rows = [
        {
            "entity_type": "Federal",
            "agency_or_program": "Treasury",
            "data_provider": "IRS",
            "data_type": "Business Master File",
            "years_available": "2021; 2022",
            "new_years_received": "",
        },
        {
            "entity_type": "Federal",
            "agency_or_program": "Treasury",
            "data_provider": "IRS",
            "data_type": "Form 1040 Returns",
            "years_available": "2020; 2021",
            "new_years_received": "2026",
        },
    ]
    path = str(tmp_path / "basic.pdf")
    write_inventory_pdf(path, full_rows_to_lines(rows))
    assert parse_pdf(path) == rows


def test_forward_fill_across_rows(tmp_path):
    """entity_type/agency_or_program printed once, blank on later rows in
    the same group, must be inherited by every row until they change."""
    lines = [
        {"entity_type": "Federal", "gap": NEW_ROW_GAP},
        {"agency_or_program": "Treasury", "gap": NEW_ROW_GAP},
        {"data_provider": "IRS", "data_type": "File A", "years_available": "2020", "gap": NEW_ROW_GAP},
        {"data_type": "File B", "years_available": "2021", "gap": NEW_ROW_GAP},  # no data_provider repeated
        {"data_type": "File C", "years_available": "2022", "gap": NEW_ROW_GAP},
    ]
    path = str(tmp_path / "forwardfill.pdf")
    write_inventory_pdf(path, lines)
    rows = parse_pdf(path)
    assert [r["data_type"] for r in rows] == ["File A", "File B", "File C"]
    for r in rows:
        assert r["entity_type"] == "Federal"
        assert r["agency_or_program"] == "Treasury"
        assert r["data_provider"] == "IRS"


def test_wrapped_years_cell_merges_into_one_field(tmp_path):
    lines = [
        {"entity_type": "Federal", "gap": NEW_ROW_GAP},
        {"agency_or_program": "Treasury", "gap": NEW_ROW_GAP},
        {
            "data_provider": "IRS",
            "data_type": "Form 1040 Returns",
            "years_available": "2018; 2019;",
            "gap": NEW_ROW_GAP,
        },
        # wrapped continuation of years_available, small gap, nothing else on the line
        {"years_available": "2020; 2021", "gap": WRAP_GAP},
    ]
    path = str(tmp_path / "wrapyears.pdf")
    write_inventory_pdf(path, lines)
    rows = parse_pdf(path)
    assert len(rows) == 1
    assert rows[0]["years_available"] == "2018; 2019; 2020; 2021"


def test_wrapped_data_provider_merges_and_persists_forward_fill(tmp_path):
    """A data provider name that wraps onto the line right after a new row
    starts must (a) be reassembled into that row's data_provider, and (b)
    be inherited *complete* by later rows in the same group, not just the
    pre-wrap fragment."""
    lines = [
        {"agency_or_program": "Health Agencies", "gap": NEW_ROW_GAP},
        {
            "data_provider": "University of North Carolina Health Care",
            "data_type": "Electronic Health Records",
            "years_available": "2020",
            "gap": NEW_ROW_GAP,
        },
        {"data_provider": "System", "gap": WRAP_GAP},  # continuation of data_provider
        {"data_type": "Visit Detail", "years_available": "2021", "gap": NEW_ROW_GAP},
    ]
    path = str(tmp_path / "wrapprovider.pdf")
    write_inventory_pdf(path, lines)
    rows = parse_pdf(path)
    assert len(rows) == 2
    assert rows[0]["data_provider"] == "University of North Carolina Health Care System"
    assert rows[0]["data_type"] == "Electronic Health Records"
    # The second row, which never repeats the provider name, must inherit
    # the *complete* (post-wrap) name.
    assert rows[1]["data_provider"] == "University of North Carolina Health Care System"
    assert rows[1]["data_type"] == "Visit Detail"


def test_wrapped_data_type_merges(tmp_path):
    lines = [
        {"agency_or_program": "Treasury", "gap": NEW_ROW_GAP},
        {
            "data_provider": "IRS",
            "data_type": "Form 1040 Returns -Electronic",
            "years_available": "2018",
            "gap": NEW_ROW_GAP,
        },
        {"data_type": "Returns", "gap": WRAP_GAP},  # continuation of data_type only
    ]
    path = str(tmp_path / "wraptype.pdf")
    write_inventory_pdf(path, lines)
    rows = parse_pdf(path)
    assert len(rows) == 1
    assert rows[0]["data_type"] == "Form 1040 Returns -Electronic Returns"


def test_header_repeats_across_pages_and_is_stripped(tmp_path):
    """Force a page break mid-table and confirm the repeated header/footer
    never leak into a row's fields, and rows on both pages are captured."""
    rows = []
    for i in range(60):  # enough rows to force at least one page break
        rows.append(
            {
                "entity_type": "Federal",
                "agency_or_program": "Treasury",
                "data_provider": "IRS",
                "data_type": f"File {i:03d}",
                "years_available": "2020",
                "new_years_received": "",
            }
        )
    path = str(tmp_path / "multipage.pdf")
    write_inventory_pdf(path, full_rows_to_lines(rows))
    parsed = parse_pdf(path)
    assert len(parsed) == 60
    assert [r["data_type"] for r in parsed] == [f"File {i:03d}" for i in range(60)]
    for r in parsed:
        for value in r.values():
            assert "Federal, State" not in value
            assert "Page" not in value or "File" in value  # never a bare footer fragment


def test_disclaimer_line_is_stripped(tmp_path):
    lines = [
        {
            "_raw_text": (
                "The following data are available for use by qualified "
                "researchers on approved internal projects for statistical "
                "purposes only. All results are subject to disclosure "
                "avoidance review."
            ),
            "gap": NEW_ROW_GAP,
        },
        {"entity_type": "Federal", "gap": NEW_ROW_GAP},
        {"agency_or_program": "Treasury", "gap": NEW_ROW_GAP},
        {
            "data_provider": "IRS",
            "data_type": "Business Master File",
            "years_available": "2020",
            "gap": NEW_ROW_GAP,
        },
    ]
    path = str(tmp_path / "disclaimer.pdf")
    write_inventory_pdf(path, lines)
    rows = parse_pdf(path)
    assert len(rows) == 1
    assert rows[0]["data_type"] == "Business Master File"


def test_footer_with_no_space_before_page_total_is_still_stripped(tmp_path):
    """Regression test: a real capture (2025-02-20) rendered its footer as
    "...Page 20 of50" -- no space between "of" and the page total, a PDF
    kerning quirk on that one page. The old regex required \\s+ there, so
    the line fell through as content and (landing in the entity_type
    column's x-range) got forward-filled into every row below it, until
    the audit caught 122 corrupted rows in that one file.

    Force a page break so the glued footer sits between two real rows --
    matching exactly how the bug actually manifested -- and confirm the
    row *after* the footer still gets the correct forward-filled
    entity_type, not the leaked footer text.
    """
    lines = [
        {"entity_type": "Other", "gap": NEW_ROW_GAP},
        {"agency_or_program": "Legal Services Corporation", "gap": NEW_ROW_GAP},
        {
            "data_provider": "Legal Services Corporation",
            "data_type": "Evictions Data",
            "years_available": "2020",
            "gap": NEW_ROW_GAP,
        },
        # A small page_height forces the page break to land right here,
        # putting the glued footer between this row and the next.
        {
            "data_provider": "Maine Community College System",
            "data_type": "Degree Data",
            "years_available": "2021",
            "gap": NEW_ROW_GAP,
        },
    ]
    path = str(tmp_path / "gluedfooter.pdf")
    # Small enough that the page breaks after the second row.
    write_inventory_pdf(path, lines, glue_footer=True, page_height=220)
    rows = parse_pdf(path)
    assert len(rows) == 2
    for row in rows:
        assert row["entity_type"] == "Other"
        for value in row.values():
            assert "2026" not in value  # the footer's year must never leak in
            assert "Page" not in value


def test_no_rows_raises_parse_error(tmp_path):
    """A PDF that never contains the expected header text at all (e.g. a
    completely different layout) must fail loudly, not silently return an
    empty inventory."""
    from reportlab.pdfgen.canvas import Canvas

    path = str(tmp_path / "wrongdoc.pdf")
    c = Canvas(path, pagesize=(900, 1000))
    c.drawString(30, 900, "Some Completely Different Report")
    c.drawString(30, 880, "Column A     Column B     Column C")
    c.drawString(30, 860, "foo          bar          baz")
    c.save()

    try:
        parse_pdf(path)
        assert False, "expected ParseError"
    except ParseError:
        pass
