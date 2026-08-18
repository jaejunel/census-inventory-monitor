# census-inventory-monitor

A monitor for the U.S. Census Bureau's **Administrative Data Inventory** —
a list of the administrative data files (from federal agencies, states,
and third parties) the Bureau has acquired for statistical use.

The Bureau publishes this as a single PDF at a static URL and overwrites it
in place roughly monthly:

> https://www2.census.gov/about/linkage/data-file-inventory.pdf

Every new version destroys the previous one — there is no version history
on the Bureau's side. This project keeps every version, and tracks exactly
which data files were added, removed, or changed between them.

## What it does

1. **Fetch.** Download the live PDF and hash it (SHA-256). If the hash
   matches the last recorded snapshot, nothing changed — record the check
   and stop.
2. **Snapshot.** If the content changed, save it to
   `snapshots/data-file-inventory-YYYY-MM-DD.pdf`, dated by the day it was
   fetched (not by whatever month the document's own title claims — the
   two frequently disagree).
3. **Parse.** Extract the table into `extracted/inventory-YYYY-MM-DD.csv`,
   one row per data file, using [pdfplumber](https://github.com/jsvine/pdfplumber).
4. **Diff.** Compare the new CSV against the previous one, matched by a key
   (not row position — see [Row identity](#row-identity-and-its-limits)
   below), and report additions, removals, and modified fields separately.
5. **Changelog.** Prepend a Markdown entry to `CHANGELOG.md` — newest
   first.

Every step is designed to **fail loudly rather than guess**. See
[Guards](#guards-against-a-bad-diff) below.

## The document's actual layout

The PDF has no ruling lines — pdfplumber's table-line detection finds
nothing. It's a grouped/hierarchical report (the kind a database export
tool like MS Access produces): a row's leading fields (which kind of
entity, which agency, which specific data provider) are printed only once
per group and implied by indentation on the page, e.g.:

```
Federal                                                        <- entity_type
    Treasury                                                   <- agency_or_program
        Internal Revenue Service (IRS)  Business Master File   ty2021; ty2022; ...
                                         Business Master Files (Annual)  ty2021; ...
```

`inventory_monitor/parse.py` reconstructs one logical row per data file by
forward-filling the leading columns from the last line they appeared on,
and merging cells that wrap onto a second physical line (both value
wraps like a long agency name, and pure line-wraps of a long word list)
back into the field they belong to. The column x-positions and header/
footer text patterns it relies on were determined by rendering sample
pages to images and cross-checking against a histogram of word positions
— see the comments in `inventory_monitor/config.py` and `parse.py` for the
specifics, including which vertical gaps between lines mean "this is a
continuation of the same cell" versus "this is a new row."

**Columns** (see `config.py` for the exact PDF header text each maps to):

| internal name         | meaning                                    |
|------------------------|---------------------------------------------|
| `entity_type`           | Federal / State / Other / Third Party      |
| `agency_or_program`      | parent department, e.g. "Treasury"        |
| `data_provider`          | specific office, e.g. "Internal Revenue Service (IRS)" |
| `data_type`              | the data file's own name — the closest thing to a file identifier |
| `years_available`        | year(s)/vintages the Bureau holds          |
| `new_years_received`     | years received in the most recent update   |

The **diff key** is `(data_provider, data_type)` — the closest the document
comes to a stable file identifier. See the next section for what that
does and doesn't guarantee.

## Row identity and its limits

Rows are matched between versions by `(data_provider, data_type)`, not by
position in the table — so re-sorting or re-grouping the table between
versions never shows up as a spurious change.

But this key is only as stable as the Bureau's own naming, and two
limitations follow directly from that:

- **A rename looks like a removal plus an addition, not a modification.**
  If an agency is renamed, or a data file relabeled, the key changes, so
  the diff reports one row disappearing and an unrelated-looking one
  appearing — not "renamed." Anyone reading `CHANGELOG.md` should keep
  this in mind before reading a same-day add+remove pair as two unrelated
  events.

- **The key is not guaranteed unique even within one version.** The
  document itself contains occasional genuine duplicates — the same
  `(data_provider, data_type)` pair appearing twice (e.g. under two
  different disclosure-category sections, or as an apparent copy-paste
  duplicate in the Bureau's own export; both were found in the document
  fetched while building this). The differ handles this by matching
  same-key rows positionally (the Nth old row with a given key against the
  Nth new row with that key) rather than dropping or crashing on them —
  see the tests in `tests/test_diff.py` — but it's worth knowing the
  guarantee is "a reasonable diff," not "provably correct" in this edge
  case.

**A row disappearing from the inventory is a change in what the Bureau
discloses about itself. It is not evidence the Bureau stopped holding the
data.** The inventory is a disclosure document, not a live index of the
Bureau's holdings — a file could be dropped from the published list for
reasons that have nothing to do with whether the data still exists
(reclassification, a disclosure-policy change, an unannounced correction
to a previous overstatement, or simple document maintenance). Silence in a
disclosure document is not evidence of absence. Treat `CHANGELOG.md` as a
record of what the Bureau *said*, not a record of what it *has*.

## Guards against a bad diff

A parser this dependent on a specific column layout will eventually meet a
version of the document formatted differently — and a diff produced by a
broken parser looks exactly like a diff, just a wrong one. Two guards
exist so that a layout break gets caught instead of silently emitted as a
changelog entry:

1. **No rows extracted.** If the parser never even matches the expected
   header text, or matches it but extracts zero rows from the content that
   follows, it raises `ParseError` immediately rather than producing an
   empty inventory.
2. **Excessive churn.** If additions plus removals exceed 25% of the total
   row count (`config.MAX_CHURN_FRACTION`), that is treated as far more
   likely to be a parser/layout break than genuine turnover in a
   monthly-updated inventory, and no changelog entry is written. The
   extracted CSV is still saved (as `extracted/inventory-YYYY-MM-DD.suspect.csv`)
   for inspection, and the snapshot PDF is always kept regardless — but
   the pipeline refuses to chain the next diff through an unreviewed
   snapshot, and `python monitor.py run` exits non-zero so CI fails
   loudly. See `CHECK_LOG.md` and any `*.suspect.csv` files after a
   failure.

Both guards are exercised in `tests/test_guards.py`.

## Known gap: pre-2023 captures

Wayback Machine captures of the source PDF go back to 2018, but the
document's layout changed at some point between the 2022-10-07 and
2023-08-31 captures — the older template has five columns (no "New Years
Received this Month"), different header wording, and a slightly different
disclaimer sentence. The seven captures from 2018–2022 fail guard #1
above (correctly — they're a genuinely different layout, not a fluke) and
are excluded from the parsed diff chronology, though their PDFs are still
saved in `snapshots/` in case someone wants to extend the calibration to
cover them later. `CHANGELOG.md` history starts at 2023-08-31, the oldest
capture the current calibration handles.

## Usage

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Normal monthly check (what the GitHub Actions workflow runs):
python monitor.py run

# One-time (or repeatable) historical backfill from the Wayback Machine:
python monitor.py backfill

# Tests:
pytest
```

`monitor.py run` exits non-zero if a parse failure or churn-guard trip
needs attention — including on a run where the PDF *didn't* change, if a
previous run's failure was never resolved (so a quiet month doesn't bury
an unresolved problem). `monitor.py backfill` is safe to re-run: captures
already saved are skipped, and it reports (without failing the whole run)
any individual captures it couldn't produce a changelog entry for.

## Automation (GitHub Actions)

`.github/workflows/monitor.yml` runs `python monitor.py run` on a monthly
cron and on manual `workflow_dispatch`. When the PDF changed, it commits
the new snapshot, CSV, and changelog back to the repository and opens a
GitHub issue summarizing what changed, using the repo's built-in
`GITHUB_TOKEN` (no secrets to configure). Dependencies are pinned in
`requirements.txt` for the same reason the guards exist: a silent library
upgrade shouldn't be able to change what gets extracted without anyone
noticing.

**GitHub's scheduled workflows are best-effort, not guaranteed-on-time** —
cron runs get delayed under platform load, and GitHub disables scheduled
workflows on repositories with no activity for 60 days (it will still run
on manual dispatch, but not on schedule, until someone pushes a commit or
manually re-enables it). If a month goes by with no new commit from the
bot:

1. Check the **Actions** tab for the repository — did the workflow run at
   all, and if so, did it fail?
2. If there's no run listed, check **Settings → Actions → General** —
   scheduled workflows may have been auto-disabled for inactivity; re-enable
   there.
3. As a manual fallback, trigger the workflow yourself from the Actions
   tab (`workflow_dispatch`), or just run `python monitor.py run` locally.

## Tests

`pytest` with synthetic PDFs generated via
[reportlab](https://www.reportlab.com/) (`tests/pdf_builder.py`), built to
mirror the real document's column positions and line-spacing conventions
closely enough to exercise the actual parsing logic, not a shortcut around
it:

- `tests/test_parse.py` — header/footer stripping across page breaks,
  forward-fill of the leading columns, and wrapped-cell merging (both a
  long data-provider name and a long data-type name split across two
  physical lines).
- `tests/test_diff.py` — builds two synthetic inventory PDFs with a known
  set of differences and asserts the differ reports exactly one addition,
  one removal, one modified field, and *no* false positive for a row that
  only moved position between the two versions. Also covers the
  duplicate-key edge case described above.
- `tests/test_guards.py` — both guards, including the exact-threshold
  boundary case.
- `tests/test_pipeline.py` — the `run`/`backfill` wiring: hash
  short-circuiting, the initial-snapshot case, and that a churn-guard trip
  writes a `.suspect.csv` and leaves `CHANGELOG.md` untouched.

## Project layout

```
monitor.py                  CLI: `run` and `backfill`
inventory_monitor/
  config.py                  column schema, calibration notes, thresholds
  fetch.py                   download + hash the live PDF
  parse.py                   PDF -> list of row dicts
  diff.py                    key-based diff between two row-sets
  guards.py                  the churn guard
  changelog.py                Markdown formatting + prepend/append
  csvio.py                   extracted/*.csv read/write
  wayback.py                  Wayback Machine CDX API client
  pipeline.py                 orchestration shared by run/backfill
snapshots/                  every distinct PDF version, dated by fetch date
extracted/                  one CSV per successfully parsed snapshot
CHANGELOG.md                 human-readable diff history, newest first
CHECK_LOG.md                  every `run` invocation, changed or not
tests/
```
