"""Build synthetic inventory PDFs for tests, using the same column
x-positions and header/footer text as the real document (see
inventory_monitor/config.py), so the parser under test exercises its real
column-classification and row/continuation logic rather than a shortcut.

Callers supply a list of *physical lines* (dicts keyed by a subset of
config.COLUMN_NAMES, plus an optional "gap" giving the vertical distance in
points from the previous line). A gap at or below parse.ROW_GAP_THRESHOLD
produces a same-cell wrap continuation; a gap above it starts a new row or
group header -- exactly mirroring the real document's structure.
"""

from __future__ import annotations

import datetime

from reportlab.pdfgen import canvas

from inventory_monitor import config

PAGE_WIDTH, PAGE_HEIGHT = 900, 1000
TOP_MARGIN = 60
BOTTOM_MARGIN = 60
NEW_ROW_GAP = 20.0  # comfortably above ROW_GAP_THRESHOLD (14.5)
WRAP_GAP = 12.0  # comfortably at/below ROW_GAP_THRESHOLD (14.5)

_FIELD_X = {name: bounds[0] + 3 for name, bounds in config.COLUMN_X_BOUNDS.items()}


def _draw_header(c: canvas.Canvas, y: float) -> float:
    c.setFont("Helvetica-Bold", 9)
    c.drawString(30, y, config.HEADER_LINE_1)
    y -= NEW_ROW_GAP
    c.drawString(30, y, config.HEADER_LINE_2)
    y -= NEW_ROW_GAP
    c.setFont("Helvetica", 9)
    return y


def _draw_footer(
    c: canvas.Canvas, page_num: int, page_count_placeholder: int = 1, *, glue_of: bool = False
) -> None:
    c.setFont("Helvetica", 8)
    date_str = datetime.date(2026, 1, 1).strftime("%A, %B %d, %Y")
    # glue_of=True reproduces a real capture's kerning quirk where "of" and
    # the page total rendered with no space between them ("...Page 20
    # of50") -- see parse.py's _FOOTER_RE comment.
    sep = "" if glue_of else " "
    c.drawString(30, BOTTOM_MARGIN - 20, f"{date_str} Page {page_num} of{sep}{page_count_placeholder}")


def write_inventory_pdf(
    path: str,
    lines: list[dict],
    *,
    include_banner: bool = True,
    glue_footer: bool = False,
    page_height: float = PAGE_HEIGHT,
) -> None:
    """Render `lines` (a flat, single logical sequence of physical lines --
    pagination is handled automatically) into a PDF at `path`. glue_footer
    reproduces the "of50" no-space kerning quirk in the footer line.
    page_height can be shrunk to force a page break after just a few lines,
    for tests that need content on both sides of a page boundary."""
    c = canvas.Canvas(path, pagesize=(PAGE_WIDTH, page_height))
    page_num = 1
    y = page_height - TOP_MARGIN

    if include_banner:
        c.setFont("Helvetica", 11)
        c.drawString(30, y, "Census Bureau Administrative Data Inventory - Test Edition")
        y -= NEW_ROW_GAP

    y = _draw_header(c, y)

    for line in lines:
        gap = line.get("gap", NEW_ROW_GAP)
        y -= gap
        if y < BOTTOM_MARGIN:
            _draw_footer(c, page_num, glue_of=glue_footer)
            c.showPage()
            page_num += 1
            y = page_height - TOP_MARGIN
            y = _draw_header(c, y)
            y -= gap  # keep gap semantics consistent across the page break -- test data avoids relying on cross-page continuation

        if "_raw_text" in line:
            # A full-width line (e.g. the disclaimer sentence) rather than
            # column-positioned fields.
            c.drawString(30, y, line["_raw_text"])
        else:
            for field, x in _FIELD_X.items():
                value = line.get(field)
                if value:
                    c.drawString(x, y, value)

    _draw_footer(c, page_num, glue_of=glue_footer)
    c.save()


def full_rows_to_lines(rows: list[dict[str, str]]) -> list[dict]:
    """Convenience: turn a list of *complete* row dicts (every field
    populated) into physical lines that mirror the real document's actual
    structure -- entity_type and agency_or_program each get their own
    line (only emitted when the value changes from the previous row, i.e.
    forward-filled otherwise), while data_provider shares a line with
    data_type/years_available/new_years_received, since that is how a
    "new row" line is actually laid out in the source PDF. Drawing all
    three leading columns on one line (which the real document never
    does) would make a single leading text run that the parser -- quite
    correctly -- treats as belonging to just one column.
    """
    out: list[dict] = []
    state = {"entity_type": None, "agency_or_program": None}
    for row in rows:
        for col in ("entity_type", "agency_or_program"):
            if row.get(col, "") != state[col]:
                out.append({col: row.get(col, ""), "gap": NEW_ROW_GAP})
                state[col] = row.get(col, "")
        out.append(
            {
                "data_provider": row.get("data_provider", ""),
                "data_type": row["data_type"],
                "years_available": row.get("years_available", ""),
                "new_years_received": row.get("new_years_received", ""),
                "gap": NEW_ROW_GAP,
            }
        )
    return out
