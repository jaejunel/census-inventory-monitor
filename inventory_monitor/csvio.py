"""Reading and writing the extracted/*.csv files."""

from __future__ import annotations

import csv

from . import config


def write_csv(rows: list[dict[str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=config.COLUMN_NAMES)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]
