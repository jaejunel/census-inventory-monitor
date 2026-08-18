"""Formatting diff results as Markdown and prepending them to CHANGELOG.md."""

from __future__ import annotations

import os

from . import config
from .diff import DiffResult


def _row_label(row: dict[str, str]) -> str:
    return f"{row['data_provider']} — {row['data_type']}"


def _key_label(key: tuple[str, ...]) -> str:
    return " — ".join(key)


def format_entry(
    *,
    date: str,
    old_snapshot: str,
    new_snapshot: str,
    old_row_count: int,
    new_row_count: int,
    result: DiffResult,
) -> str:
    lines = [
        f"## {date}",
        "",
        f"Compared [`{os.path.basename(old_snapshot)}`](snapshots/{os.path.basename(old_snapshot)}) "
        f"({old_row_count} rows) to "
        f"[`{os.path.basename(new_snapshot)}`](snapshots/{os.path.basename(new_snapshot)}) "
        f"({new_row_count} rows).",
        "",
        f"**{len(result.added)} added, {len(result.removed)} removed, "
        f"{len(result.modified)} modified.**",
        "",
    ]

    if result.added:
        lines.append(f"### Added ({len(result.added)})")
        lines.append("")
        for row in sorted(result.added, key=_row_label):
            lines.append(f"- {_row_label(row)}")
        lines.append("")

    if result.removed:
        lines.append(f"### Removed ({len(result.removed)})")
        lines.append("")
        for row in sorted(result.removed, key=_row_label):
            lines.append(f"- {_row_label(row)}")
        lines.append("")

    if result.modified:
        lines.append(f"### Modified ({len(result.modified)})")
        lines.append("")
        for m in sorted(result.modified, key=lambda m: _key_label(m.key)):
            lines.append(f"- {_key_label(m.key)}")
            for f, (old, new) in m.changes.items():
                lines.append(f"  - `{f}`: {old!r} → {new!r}")
        lines.append("")

    if not (result.added or result.removed or result.modified):
        lines.append("No changes detected (content hash differed, but rows matched).")
        lines.append("")

    return "\n".join(lines)


def format_initial_entry(*, date: str, snapshot: str, row_count: int) -> str:
    return (
        f"## {date}\n\n"
        f"Initial snapshot: [`{os.path.basename(snapshot)}`](snapshots/{os.path.basename(snapshot)}) "
        f"({row_count} rows). Nothing to diff against yet.\n"
    )


def prepend_changelog(entry: str, path: str = config.CHANGELOG_PATH) -> None:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    else:
        existing = (
            "# Changelog\n\n"
            "History of changes to the Census Bureau's administrative data "
            "file inventory PDF, newest first. See README.md for how row "
            "identity works and what a removed row does and does not mean.\n\n"
        )

    # Insert the new entry right after the top-level header block (before
    # the first "## " section, if any; otherwise at the end of the file).
    header_end = existing.find("\n## ")
    if header_end == -1:
        new_content = existing.rstrip("\n") + "\n\n" + entry.strip("\n") + "\n"
    else:
        header_end += 1  # keep the newline before "## "
        new_content = existing[:header_end] + entry.strip("\n") + "\n\n" + existing[header_end:]

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)


def latest_entry(path: str = config.CHANGELOG_PATH) -> str:
    """Return just the newest "## ..." section of CHANGELOG.md (used by the
    GitHub Actions workflow to build an issue body). Empty string if the
    file doesn't exist or has no entries yet."""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    start = content.find("\n## ")
    if start == -1:
        return ""
    start += 1
    end = content.find("\n## ", start)
    return content[start:end].strip("\n") if end != -1 else content[start:].strip("\n")


def append_check_log(line: str, path: str = config.CHECK_LOG_PATH) -> None:
    header_needed = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if header_needed:
            f.write(
                "# Check log\n\n"
                "Every automated run of the monitor, whether or not the PDF "
                "changed. Newest entries are appended at the bottom.\n\n"
            )
        f.write(line.rstrip("\n") + "\n")
