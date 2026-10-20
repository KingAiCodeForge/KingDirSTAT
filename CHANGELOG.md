# Changelog

All notable changes to KingDirSTAT are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.1] - 2026-10-20

### Fixed

- CLI outputs now refuse existing files, including aliases of scanned input, unless
  `--overwrite` is supplied. Atomic publishing also refuses files created during export.
- Unsupported and variable-length duplicate hash algorithms fail before scanning.
- Malformed snapshot entry structures and values produce concise errors.
- All free-text CSV entry columns escape formula prefixes, including line feeds.
- Directories with unreadable child metadata are no longer reported as verified empty.
- Closing the GUI during a slow cancellation keeps the scan thread alive safely.

### Qualification

- Added data-preservation regressions and an installed-wheel smoke script covering
  all analysis commands, spaces/Unicode paths, missing input and optional dependencies.
- Retained beta status; see `docs/REVIEW-2.1.1.md` for reviewed modules and limitations.

## [2.1.0] - 2026-10-04

### Added

- A PySide6 desktop interface with a sortable lazy directory tree, extension summary, Largest
  Files view, synchronized treemap, drive/folder selection, snapshots, CSV export, and Ctrl+F/F3
  search.
- CLI commands for tree, volume-space, largest-file, type, duplicate, empty-directory, stale-file,
  cleanup-candidate, search, snapshot, comparison, drive, and shell-completion workflows.
- Literal and regular-expression search with case, entry-type, size, and result-count controls.
- Content-hash duplicate detection with hardlink-aware reclaimable totals and atomic,
  spreadsheet-formula-safe CSV evidence.
- Relative and redacted path modes for shareable CLI snapshots.
- Logical-size reporting, optional allocated-size reporting, hardlink deduplication, exclusion
  rules, one-filesystem scans, and bounded worker queues.
- Cross-platform reveal-in-file-manager support for Windows, macOS, and Linux.

### Changed

- Largest-file and limited-search selection now use bounded heaps to reduce peak memory.
- Final aggregation no longer constructs a second full list of scanned files.
- Whole `.gradle` directories are classified as review-required rather than automatically safe
  cleanup candidates.
- Cancelled scans explicitly mark discovered but unscanned directories as incomplete.
- The optional `kingdirstat-gui` entry point now reports how to install the `gui` extra instead of
  exposing a missing-PySide6 traceback.

### Security

- Symlinks and reparse points are not followed by default.
- Reparse-point files, including cloud placeholders, are not opened for duplicate hashing.
- CSV cells that could be interpreted as spreadsheet formulas are escaped.
- Generated cleanup scripts are dry-run-first, sanitize line separators in comments, and are never
  executed by KingDirSTAT.

[Unreleased]: https://github.com/KingAiCodeForge/KingDirSTAT/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/KingAiCodeForge/KingDirSTAT/releases/tag/v2.1.0
