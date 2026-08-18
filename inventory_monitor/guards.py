"""Sanity guards against parser/layout breakage masquerading as a real diff.

Two failure modes are guarded against:

1. parse.parse_pdf() already raises ParseError if table extraction finds no
   tables/rows at all -- that guard lives there since it has nothing to do
   with a diff (there may be no previous snapshot yet).

2. This module's check_churn() guards the diff itself: if additions plus
   removals exceed config.MAX_CHURN_FRACTION of the total row count, that
   is far more likely to be a layout change that broke column boundaries
   than genuine turnover in a ~monthly-updated inventory.
"""

from __future__ import annotations

from . import config
from .diff import DiffResult


class ChurnGuardError(Exception):
    """Raised when a diff looks more like a parser break than real change."""


def check_churn(result: DiffResult, old_row_count: int, new_row_count: int) -> None:
    total = max(old_row_count, new_row_count, 1)
    churn = len(result.added) + len(result.removed)
    fraction = churn / total
    if fraction > config.MAX_CHURN_FRACTION:
        raise ChurnGuardError(
            f"{churn} of {total} rows ({fraction:.0%}) were added or removed "
            f"between versions -- above the {config.MAX_CHURN_FRACTION:.0%} "
            f"threshold. This is far more likely to mean the PDF's layout "
            f"changed and broke extraction than that the inventory genuinely "
            f"turned over that much in one update. Refusing to write a "
            f"changelog entry. The extracted CSV has still been saved "
            f"(with a .suspect.csv suffix) for inspection; re-calibrate "
            f"inventory_monitor/config.py before trusting a diff again."
        )
