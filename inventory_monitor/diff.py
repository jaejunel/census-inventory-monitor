"""Diff two extracted row-sets by key, not by row position.

The key is config.KEY_FIELDS (data_provider, data_type) so that reordering
the table between versions never registers as a change.

The source document is not guaranteed to have unique keys -- e.g. the same
(data_provider, data_type) pair can legitimately appear twice under two
different disclosure-category sections, or as an outright duplicate row in
the Bureau's own export. Rather than silently dropping or crashing on
these, rows sharing a key are matched up in the order they appear in each
CSV: the Nth old row with a given key is compared against the Nth new row
with that key; any surplus on either side is reported as added/removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass
class ModifiedRow:
    key: tuple[str, ...]
    changes: dict[str, tuple[str, str]]  # field -> (old, new)


@dataclass
class DiffResult:
    added: list[dict[str, str]] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    modified: list[ModifiedRow] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)


def _key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[f] for f in config.KEY_FIELDS)


def _group_by_key(rows: list[dict[str, str]]) -> dict[tuple[str, ...], list[dict[str, str]]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(_key(row), []).append(row)
    return groups


_COMPARE_FIELDS = [c for c in config.COLUMN_NAMES if c not in config.KEY_FIELDS]


def diff_rows(old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> DiffResult:
    old_groups = _group_by_key(old_rows)
    new_groups = _group_by_key(new_rows)

    result = DiffResult()
    all_keys = sorted(set(old_groups) | set(new_groups))

    for key in all_keys:
        olds = old_groups.get(key, [])
        news = new_groups.get(key, [])

        for old_row, new_row in zip(olds, news):
            changes = {}
            for f in _COMPARE_FIELDS:
                if old_row[f] != new_row[f]:
                    changes[f] = (old_row[f], new_row[f])
            if changes:
                result.modified.append(ModifiedRow(key=key, changes=changes))

        if len(olds) > len(news):
            result.removed.extend(olds[len(news):])
        elif len(news) > len(olds):
            result.added.extend(news[len(olds):])

    return result
