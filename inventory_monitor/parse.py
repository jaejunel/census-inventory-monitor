"""Parse a snapshot PDF into one row per data file.

The PDF has no ruling lines -- it is a grouped/hierarchical report (see
config.py for the full explanation). This module:

  1. Groups words into physical lines by y-position.
  2. Strips the repeated two-line header, the page-footer date/page-number
     line, and the (two distinct variants of the) disclaimer sentence that
     precedes each disclosure-category section.
  3. Buckets each remaining line's words into columns by x-position.
  4. Reconstructs logical rows via a small state machine that (a) forward-
     fills the Type / Agency / Data Provider columns from the last line on
     which they appeared, since the source document only prints them once
     per group, and (b) merges wrapped continuation lines back into the
     field they belong to, using the vertical gap between lines to tell a
     wrapped cell (~13.5pt line spacing) apart from a genuinely new row or
     group header (~20-21pt gap).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import pdfplumber

from . import config

# Gap (in PDF points) above which two consecutive lines are considered to
# start a new row/group rather than being a wrapped continuation of the
# same cell. Empirically, across a sample of pages, line-to-line gaps fall
# into three clusters: ~13.2-13.6pt (a wrapped cell continuing on the next
# physical line), ~15.7-15.9pt (a group-header line -- Type/Agency/Data
# Provider printed on its own line -- immediately followed by its first
# child row), and ~20.5-21.5pt (one row to the next sibling row, or a
# bigger structural gap). Only the first cluster is a same-cell wrap; 14.5
# sits in the clean gap between it and the second cluster.
ROW_GAP_THRESHOLD = 14.5

_DISCLAIMER_PREFIX = "The following data are available for use by qualified researchers"
# Whitespace between tokens is \s* (optional), not \s+ (required): a real
# capture (2025-02-20) rendered its footer as "...Page 20 of50" -- a PDF
# kerning quirk on that one page glued "of" to the page total with no
# space. With \s+ that line didn't match, fell through as content, and
# (starting at x0 in the entity_type column's range) got forward-filled
# into 122 subsequent rows before the bug was caught. See CHANGELOG.md's
# 2026-08-xx correction entry and tests/test_parse.py for the regression
# test.
_FOOTER_RE = re.compile(r"^\w+,\s*\w+\s*\d{1,2},\s*\d{4}\s*Page\s*\d+\s*of\s*\d+$")
_WHITESPACE_RE = re.compile(r"\s+")


class ParseError(Exception):
    """Raised when the PDF could not be parsed into any rows at all."""


@dataclass
class Line:
    page_index: int
    top: float
    cols: dict[str, str]
    raw_text: str


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


_LEADING_COLS = ("entity_type", "agency_or_program", "data_provider")
_LEADING_EDGE = config.COLUMN_X_BOUNDS["data_type"][0]  # 425: where the reliable columns start


def _classify_words_into_columns(words: list[dict]) -> dict[str, str]:
    """Bucket a physical line's words into the six columns.

    The three leading columns (Type / Agency / Data Provider) are narrow,
    indentation-only "slots" -- a value longer than its own slot overflows
    rightward into the x-range nominally owned by the next slot (e.g. a
    51-character agency name starting at the Agency slot's x0=150 runs
    well past the Data Provider slot's x0=210). Bucketing word-by-word
    would wrongly split such a value across two columns. Since a single
    physical line only ever carries ONE leading-column value (the document
    never mixes two hierarchy levels on one line), the whole leading run is
    assigned as a block to whichever column the run's *first* word's x0
    falls in.

    Data Type / Years Available / New Years Received this Month, by
    contrast, have reliable, narrow left edges even when several of them
    share a line (confirmed by histogram across sample pages), so those
    three are still bucketed per-word.
    """
    words = sorted(words, key=lambda w: w["x0"])
    leading = [w for w in words if w["x0"] < _LEADING_EDGE]
    trailing = [w for w in words if w["x0"] >= _LEADING_EDGE]

    buckets: dict[str, list[str]] = {name: [] for name in config.COLUMN_NAMES}

    if leading:
        first_x0 = leading[0]["x0"]
        for name in _LEADING_COLS:
            lo, hi = config.COLUMN_X_BOUNDS[name]
            if lo <= first_x0 < hi:
                buckets[name] = [w["text"] for w in leading]
                break
        else:
            # Shouldn't happen since _LEADING_COLS bounds are contiguous
            # from 0 to _LEADING_EDGE, but fail safe rather than drop text.
            buckets["data_provider"] = [w["text"] for w in leading]

    for w in trailing:
        x0 = w["x0"]
        for name in ("data_type", "years_available", "new_years_received"):
            lo, hi = config.COLUMN_X_BOUNDS[name]
            if lo <= x0 < hi:
                buckets[name].append(w["text"])
                break

    return {name: _normalize(" ".join(vals)) for name, vals in buckets.items()}


def _extract_lines(pdf: pdfplumber.PDF) -> Iterable[Line]:
    """Yield content Lines in document order, with header/footer/disclaimer
    lines already stripped, gated to start only after the header has first
    been seen (which also skips the page-0 title banner)."""
    header_seen = False
    for page_index, page in enumerate(pdf.pages):
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
        if not words:
            continue
        # Cluster words into physical lines by y-position.
        # Tolerance is deliberately generous (~4pt): the "New Years Received
        # this Month" column's text baseline sits ~2.9pt off from the rest
        # of the row on many lines (a rendering quirk of the source PDF,
        # confirmed by inspection), which at a tighter tolerance splits one
        # physical row -- including the header -- into two Line objects
        # that neither match the expected header text nor merge correctly.
        # 4pt is still far below the ~13.5pt same-cell-wrap gap, so it
        # cannot accidentally merge two genuinely different lines.
        by_top: list[tuple[float, list[dict]]] = []
        for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if by_top and abs(w["top"] - by_top[-1][0]) <= 4.0:
                by_top[-1][1].append(w)
            else:
                by_top.append((w["top"], [w]))

        for top, line_words in by_top:
            raw_text = _normalize(" ".join(w["text"] for w in sorted(line_words, key=lambda w: w["x0"])))
            if raw_text in (config.HEADER_LINE_1, config.HEADER_LINE_2):
                header_seen = True
                continue
            if not header_seen:
                continue  # page-0 title banner, before the table starts
            if raw_text.startswith(_DISCLAIMER_PREFIX):
                continue
            if _FOOTER_RE.match(raw_text):
                continue
            if not raw_text:
                continue
            cols = _classify_words_into_columns(line_words)
            yield Line(page_index=page_index, top=top, cols=cols, raw_text=raw_text)


_GROUP_COLS = ("entity_type", "agency_or_program", "data_provider")


def _rows_from_lines(lines: list[Line]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    state = {c: "" for c in _GROUP_COLS}
    current_row: dict[str, str] | None = None
    mode: str | None = None  # "ROW" | "GROUP:<col>" | None
    prev: Line | None = None

    for line in lines:
        cols = line.cols
        same_page = prev is not None and prev.page_index == line.page_index
        gap = (line.top - prev.top) if same_page else None
        has_data_type = bool(cols["data_type"])
        has_group = any(cols[c] for c in _GROUP_COLS)

        if gap is not None:
            is_new = gap > ROW_GAP_THRESHOLD
        else:
            # Page boundary (or first line): can't measure a gap. Treat as
            # a continuation only if the line carries nothing but
            # years/new-years content, which can only be a wrap of an
            # already-open row.
            is_new = has_data_type or has_group

        if not is_new:
            if mode == "ROW" and current_row is not None:
                # data_provider is included here too: a data provider name
                # that wraps onto the line right after a new row started
                # (e.g. "...Health Care" / "System") is a continuation of
                # that row's own data_provider field, not a fresh group
                # header -- the gap is small and no data_type/years content
                # is competing for this line.
                for c in ("data_provider", "data_type", "years_available", "new_years_received"):
                    if cols[c]:
                        current_row[c] = (current_row[c] + " " + cols[c]).strip()
                        if c == "data_provider":
                            # Keep forward-fill state in sync so later
                            # rows in the same group inherit the complete
                            # (unwrapped) name, not the pre-wrap fragment.
                            state["data_provider"] = current_row[c]
            elif mode is not None and mode.startswith("GROUP:"):
                for c in _GROUP_COLS:
                    if cols[c]:
                        state[c] = (state[c] + " " + cols[c]).strip()
            # else: nothing open to continue; drop the line silently.
        else:
            if has_data_type:
                for c in _GROUP_COLS:
                    if cols[c]:
                        state[c] = cols[c]
                current_row = {
                    **state,
                    "data_type": cols["data_type"],
                    "years_available": cols["years_available"],
                    "new_years_received": cols["new_years_received"],
                }
                rows.append(current_row)
                mode = "ROW"
            elif has_group:
                touched = [c for c in _GROUP_COLS if cols[c]]
                for c in touched:
                    state[c] = cols[c]
                mode = "GROUP:" + touched[-1]
            # else: blank/unrecognized line; ignore, keep mode unchanged.

        prev = line

    return rows


def parse_pdf(pdf_path: str) -> list[dict[str, str]]:
    """Parse a snapshot PDF into a list of row dicts (config.COLUMN_NAMES keys).

    Raises ParseError if no rows could be extracted at all -- that is far
    more likely to mean the document's layout changed under us than that
    the inventory is genuinely empty.
    """
    with pdfplumber.open(pdf_path) as pdf:
        lines = list(_extract_lines(pdf))

    if not lines:
        raise ParseError(
            f"Found no table content at all in {pdf_path} -- the expected "
            f"header text was never matched. The document's layout has "
            f"likely changed; the column calibration in config.py needs a "
            f"fresh look before this can be trusted."
        )

    rows = _rows_from_lines(lines)

    if not rows:
        raise ParseError(
            f"Found table header/content lines in {pdf_path} but extracted "
            f"zero data rows from them. The layout has likely changed; "
            f"re-calibrate config.py before trusting any diff."
        )

    return rows
