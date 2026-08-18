"""
Calibrated against the live PDF fetched 2026-08-17 (document title: "Census
Bureau Administrative Data Inventory - July 2026"; footer generation date:
"Monday, August 3, 2026"; 70 pages).

THE SHAPE OF THE DOCUMENT
--------------------------
This is not a ruled table -- there are no grid lines in the PDF (confirmed:
zero `page.lines` / `page.rects` on body pages). It is a grouped report, the
kind a database reporting tool (e.g. MS Access) produces: each row's leading
fields are only printed once per group and are visually implied by
indentation. Reading down a page looks like:

    Federal                                                          <- Type
        Treasury                                                     <- Agency or Program
            Internal Revenue Service (IRS)   Business Master File   ty2021; ty2022; ...
                                              Business Master Files (Annual)  ty2021; ...
                                              Earned Income Tax Credit (EITC) 2018-2021; ...

So a logical "row" (one data file) is reconstructed by forward-filling the
Type / Agency / Data Provider columns from the last line on which they
appeared -- they are blank on every subsequent row within the same group.

Column x0 (left-edge) boundaries were determined empirically: two columns
(Data Type, Years Available) have a very strong, consistent left edge across
hundreds of samples (x0 ~= 425 and ~= 585 respectively); the others were
confirmed by rendering pages to images and reading them directly. See
inventory_monitor/parse.py for how these are used.
"""

from __future__ import annotations

SOURCE_URL = "https://www2.census.gov/about/linkage/data-file-inventory.pdf"

# CDX API base for backfilling historical captures from the Wayback Machine.
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_AVAILABLE_URL = "https://archive.org/wayback/available"

SNAPSHOTS_DIR = "snapshots"
EXTRACTED_DIR = "extracted"
CHANGELOG_PATH = "CHANGELOG.md"
CHECK_LOG_PATH = "CHECK_LOG.md"

# --- Column schema -----------------------------------------------------
#
# Column order matters: it is both the extraction order (left to right) and
# the CSV column order.
#
#   internal name         | header text on the PDF
#   -----------------------|--------------------------------
#   entity_type            | Federal, State, Other, or Third Party
#   agency_or_program      | Agency or Program Type
#   data_provider          | Data Provider
#   data_type              | Data Type
#   years_available        | Years Available
#   new_years_received     | New Years Received this Month
#
# "data_type" is the actual data-file name (e.g. "Business Master File").
# "data_provider" is the specific originating office (e.g. "Internal Revenue
# Service (IRS)"); "agency_or_program" is the parent department (e.g.
# "Treasury"). The user's diff key is "agency and data-file-name" -- the
# most specific, stable pairing available is (data_provider, data_type), so
# that is what KEY_FIELDS uses below. agency_or_program is carried along as
# a plain field (its value shows up as a "modified" field if an agency is
# renamed, per the README caveat on row identity).
COLUMN_NAMES = [
    "entity_type",
    "agency_or_program",
    "data_provider",
    "data_type",
    "years_available",
    "new_years_received",
]

# Left-edge x0 boundaries (in PDF points) for each column, low bound
# inclusive. The upper bound of the last column is open-ended (page width).
# Determined by rendering sample pages to images and cross-checking against
# a histogram of word x0 positions across 14 sample pages.
COLUMN_X_BOUNDS = {
    "entity_type": (0, 150),
    "agency_or_program": (150, 210),
    "data_provider": (210, 425),
    "data_type": (425, 585),
    "years_available": (585, 760),
    "new_years_received": (760, 10_000),
}

# The two-line header repeats verbatim at the top of every page. These are
# the literal text lines pdfplumber's extract_text() produces for it (used
# to detect and strip repeated header lines during parsing).
HEADER_LINE_1 = "Federal, State, Other, Agency or Program"
HEADER_LINE_2 = (
    "or Third Party Type Data Provider Data Type Years Available "
    "New Years Received this Month"
)

# The key that identifies "the same data file" across two versions of the
# inventory, so that reordering the table does not register as a change.
# See the column-name discussion above.
KEY_FIELDS = ["data_provider", "data_type"]

# Guard: if a table-extraction pass finds zero rows, something broke.
# Guard: if (additions + removals) / total_rows exceeds this fraction,
# treat the diff as a probable parser/layout break rather than a genuine
# inventory turnover, and refuse to write a changelog entry.
MAX_CHURN_FRACTION = 0.25
