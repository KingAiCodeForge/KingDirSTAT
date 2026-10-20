KingDirSTAT 2.1.1 is a beta safety and portability update to the public 2.1.0 source
(`1efd970c1e297dfbb7eed45bfbee39c9a22510c3`). No earlier PR existed to merge.

- CLI snapshots, CSV reports and cleanup scripts now preserve existing files by
  default. Use `--overwrite` to intentionally replace an output.
- Race-safe output publication also protects destinations created during export.
- Duplicate hashing rejects unsupported/variable-length digests before scanning.
- Redirected CLI output stays UTF-8 even with a Windows legacy code page.
- Snapshot entry validation and CSV text escaping cover additional malformed input.
- Unreadable metadata no longer makes a directory look verified empty; slow GUI
  cancellation no longer destroys the running scan thread on close.

Qualification includes fixture-only regression tests, offscreen GUI tests,
lint/type checks, a source/wheel build, and a clean CLI-only wheel installation run
outside the checkout with spaces/Unicode paths and input SHA-256 preservation.
The workflow run associated with the tag records the exact checks and results.
See `docs/REVIEW-2.1.1.md` for the per-module ledger and limits. This remains beta;
macOS, every unusual/remote filesystem and exhaustive interactive GUI behavior
have not been qualified. No personal scan data or private prototype source is
included. No package-registry publication is performed.

Download the wheel or source distribution and verify it against `SHA256SUMS`.
The original public source remains at `preserve/main-20261020`; integration history
remains at `integrate/portable-safety-20261020`. Source-tag and artifact receipts
identify the exact build; no original refs were deleted or rewritten.
