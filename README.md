# KingDirSTAT

[![CI](https://github.com/KingAiCodeForge/KingDirSTAT/actions/workflows/ci.yml/badge.svg)](https://github.com/KingAiCodeForge/KingDirSTAT/actions/workflows/ci.yml)

A disk usage analyser with a treemap desktop interface and an automation-friendly CLI.

**KingDirSTAT does not modify, move, or delete the content it scans.** It writes a file only when
you explicitly request an output such as a snapshot, CSV report, or dry-run cleanup script. The
`clean` command reports what could be reclaimed; it never performs the cleanup itself.

CLI snapshot, CSV and cleanup-script outputs refuse existing destinations by default,
including aliases of scanned files. Use `--overwrite` only when replacing that output
is intentional. The GUI asks through its Save dialog; the Python export API retains
its overwrite behavior and offers `overwrite=False` for protected writes. On Unix,
protected atomic writes require same-directory hard-link support and fail safely if
the destination filesystem does not provide it.

## Relationship to WinDirStat

KingDirSTAT is an independent project inspired by the inspection and reporting workflow of
[WinDirStat](https://windirstat.net/). It is not affiliated with, endorsed by, or maintained by
the WinDirStat project. Feature comparisons describe behavior, not source compatibility or
official status. Contributors must not copy WinDirStat source code or assets into this
MIT-licensed repository without first resolving the licensing implications.

## Install

Clone the repository, create a virtual environment, and install the checkout:

```bash
git clone https://github.com/KingAiCodeForge/KingDirSTAT.git
cd KingDirSTAT
python -m venv .venv
```

Activate `.venv` in the usual way for your shell, then install the checkout:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
```

Run `kingdirstat --help` to verify the installation. The `gui` extra installs PySide6; for a
CLI-only installation use `python -m pip install -e .`. You can also invoke the installed package
as `python -m kingdirstat`. Ad hoc top-level launchers from older private prototypes are not
required or supported.

Versioned wheels and source archives are attached to
[GitHub releases](https://github.com/KingAiCodeForge/KingDirSTAT/releases). Install a downloaded
wheel with `python -m pip install path/to/kingdirstat-2.1.1-py3-none-any.whl` from any directory.
See [the preservation and script review](docs/REVIEW-2.1.1.md) for the exact public source,
entrypoint coverage and qualification limits. The project remains beta.

## Desktop interface

```bash
kingdirstat gui "."
kingdirstat gui --snapshot before.kingdirstat.json
```

The window follows WinDirStat's layout:

- **Directory pane** (top left): Name, Size, % of parent, Items, Files, Subdirs, Last change,
  Attributes. Click any header to sort using the underlying numeric/date value; Size descending is
  the default. Expanded branches and the selected entry survive a sort. Rows are built lazily, so
  expanding a large result does not duplicate the scan tree.
- **Extension list** (top right): every file type with a colour swatch matching the treemap,
  file count, size, and share of the total.
- **Largest Files tab** (top right): the 500 largest files without building another unbounded
  in-memory copy of the scan.
- **Treemap** (bottom): squarified layout with cushion shading, coloured by file extension.
  Double-click a folder to zoom in, and use View to zoom out or reset.

Use **Ctrl+F** to find a name or path and **F3** to cycle through matches. Find, the directory tree,
Largest Files, extension selection, and the treemap keep their selection synchronized. The
right-click menu offers **Show in File Manager** and **Copy Path** only; there is no delete action
anywhere in the interface.

Use **File > Open Drive** for a read-only scan of an accessible volume, or **File > Open Folder**
for a narrower target. Snapshots can be saved and reopened later (File > Save Snapshot / Open
Snapshot), and any scan can be exported to CSV. GUI snapshots contain the paths displayed by the
application, which may be absolute. Treat them as private unless you have inspected and redacted
them before sharing.

## Commands

Most live-analysis commands take a path; where the path is optional, it defaults to the current
directory. `search`, snapshot export/compare, completion and GUI options show their own required
arguments in `--help`. Add `--json` where offered for machine-readable output.
Redirected CLI output uses UTF-8, including on Windows with a legacy console code page.

### `scan` - summarise a directory

```bash
kingdirstat scan "." --top 20
kingdirstat scan "." --json --save baseline.kingdirstat.json
```

### `tree` - the directory pane as text

Shows size, share, item counts, last change, and attributes, indented by depth.

```bash
kingdirstat tree "." --depth 2
kingdirstat tree "/mnt/archive" --depth 1 --dirs-only --total
kingdirstat tree . --children 5          # only the 5 largest children per level
```

`--depth` controls how much is displayed; `--max-depth` limits how deep the scan itself goes.
`--total` shows each row as a share of the whole scan instead of its parent.

### `space` - compare a scan against the volume

Reports capacity, free space, what the scan measured, and how much of the volume is accounted for
elsewhere. A warning below 10% free is a conservative operating-headroom signal, not a diagnosis or
a promise that every SSD slows at that exact threshold.

```bash
kingdirstat space "/"
kingdirstat space "D:\Data" --json
```

### `clean` - find regenerable caches and build output

Reports only. `safe` rules are always rebuilt automatically (`__pycache__`, `.mypy_cache`,
`node_modules`, and similar); `review` rules may hold something you still want (`build`, `dist`,
`target`, `.venv`). A matched directory is never descended into, so nested matches are not double
counted.

```bash
kingdirstat clean "."
kingdirstat clean "." --confidence all --min-size 100MiB
kingdirstat clean "." --emit-script review.ps1
```

`--emit-script` writes a `.ps1` or `.sh` you can read before running. Every line in it is a dry run
by default: PowerShell scripts carry `-WhatIf`, shell scripts default to `DRY_RUN=1`. You decide
what to delete, and you run it yourself.

### `largest`, `types`, `duplicates`, `empty`, `stale`

```bash
kingdirstat largest "/mnt/archive" --limit 40 --min-size 1GiB
kingdirstat types "." --limit 20
kingdirstat duplicates "./Downloads" --min-size 10MiB
kingdirstat duplicates "./Downloads" --min-size 10MiB --csv duplicate-groups.csv
kingdirstat empty "."
kingdirstat stale "/mnt/archive" --days 730
```

`duplicates` matches on content: files are grouped by size, then hashed, so identical bytes are
required before anything is reported as a duplicate. Hardlinks pointing at the same data are only
counted once in the reclaimable total. `--csv` atomically writes one formula-safe row per path with
the group index, hash algorithm, digest, file size and reclaimable group size; it never removes a
duplicate. Symlinks and reparse-point files (including cloud placeholders) are not opened for
hashing, so this analysis does not hydrate remote files.

### `search` - find names and paths

Literal matching is case-insensitive by default. Results are largest-first and can be bounded by
size; regular expressions and directory matches are opt-in.

```bash
kingdirstat search "." src --limit 50
kingdirstat search "/mnt/games" "\\.(pak|vpk)$" --regex --min-size 1GiB
kingdirstat search "." workspace --include-directories --json
```

Use `--case-sensitive`, `--max-size`, and any global scan option as needed. An invalid regular
expression fails before the filesystem scan starts, with a concise error instead of a traceback.
Literal relative-path queries accept either `sub\file.bin` or `sub/file.bin`.

### `export` and `compare` - track growth over time

```bash
kingdirstat export "." before.kingdirstat.json
# ... time passes ...
kingdirstat export "." after.kingdirstat.json
kingdirstat compare before.kingdirstat.json after.kingdirstat.json
```

`export` also writes CSV (`--format csv`), and `-` sends output to stdout for piping.

### `drives` and `completion`

```bash
kingdirstat drives
kingdirstat completion powershell >> $PROFILE
```

## Global scan options

| Option | Effect |
|---|---|
| `--workers N` | Directory workers (default: min(8, CPU count)); outstanding directory jobs are capped at 4x this value |
| `--exclude GLOB` | Skip a name or root-relative glob; repeatable |
| `--max-depth N` | Stop descending past N levels |
| `--follow-symlinks` | Follow links and junctions (off by default) |
| `--one-file-system` | Do not cross onto another volume |
| `--allocated` | Measure allocated size as well as logical size |
| `--dedupe-hardlinks` | Count hardlinked content once |
| `--show-errors` | List every unreadable path, not just the count |
| `--no-progress` | Suppress the progress line |

**Links are not followed by default.** A junction such as `D:\Caches\python\pip` is reported as a
link and its target is not counted, so a redirected cache is never billed to the wrong drive.
If a scan is cancelled, discovered-but-unscanned directories are explicitly marked incomplete and
are never presented as verified empty.

## Path privacy

`export` and `scan --save` accept `--path-mode`:

- `absolute` (default): full paths
- `relative`: paths relative to the scan root
- `redacted`: root replaced with `<ROOT>`, so a snapshot can be shared without leaking your layout

Use the `.kingdirstat.json` suffix for snapshots so generated evidence is easy to recognize and is
covered by this repository's ignore rules. Before sharing a snapshot, prefer a fresh redacted
export and still inspect it for sensitive file and directory names:

```bash
kingdirstat export "." share.kingdirstat.json --path-mode redacted
```

The suffix does not make a snapshot anonymous. Relative and redacted modes protect the scan-root
prefix, but file names and paths below that root can still reveal private information. GUI-created
snapshots may contain absolute paths and should be treated as private by default.

CSV output escapes any cell starting with `=`, `+`, `-`, or `@`, so a crafted filename cannot become
a spreadsheet formula.

## Safety and non-goals

- KingDirSTAT does not automatically delete caches or act on a directory name alone.
- It does not remove editor or application state, browser profiles, dumps, backups, or redirected
  junction targets.
- Links, junctions, and other reparse points are not followed unless the user explicitly opts in.
- A low-free-space warning is an operating-headroom signal; it does not diagnose a disk,
  controller, cable, driver, or other hardware fault.

Any future mutation feature must first add path-boundary and reparse-point validation, a complete
preview, explicit confirmation, durable audit output, and post-action verification. Until then,
KingDirSTAT remains an analyser rather than a file remover.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Fatal error |
| 2 | Usage error |
| 3 | Completed, but some paths were unreadable |
| 130 | Cancelled (partial result still returned) |

Code 3 matters for scripting: the scan finished and the numbers are usable, but permissions or
locked files meant some entries could not be read. Use `--show-errors` to see which.

## Roadmap

- Improve whole-volume scale with an explicit entry/memory budget, clearer ETA, optional native
  NTFS/MFT enumeration, and background GUI aggregation.
- Add size/date/extension filters, sorting for secondary tables, configurable columns, and a GUI
  duplicate-groups workflow with cancellable hashing and a reusable hash cache.
- Add pause/resume, branch refresh, file-system watching, and explicit stale-snapshot indicators.
- Improve saved UI settings, theme/high-DPI behavior, keyboard navigation, and explanations of
  logical, allocated, sparse, compressed, hardlinked, free, and unknown space.

## Project documentation

- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md) — development setup, checks, and pull-request rules
- [Security policy](SECURITY.md) — private reporting and supported versions
