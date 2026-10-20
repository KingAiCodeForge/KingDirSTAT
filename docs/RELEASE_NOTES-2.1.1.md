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

Local qualification: Windows/Python 3.13 with PySide6 6.9.2 **181 tests passed**;
Ubuntu 24.04/WSL Python 3.12.3 **152 passed**, with the optional GUI module skipped.
Ruff format/lint and mypy passed. Built wheel and source archives were reviewed;
clean CLI-only wheel installs passed outside the checkout on both platforms with
spaces/Chinese/accented paths, a forced legacy pipe code page and input SHA-256
preservation.

Hosted CI could not start: all nine jobs in
[run 39392783718](https://github.com/KingAiCodeForge/KingDirSTAT/actions/runs/39392783718)
had no executed steps because GitHub reports the account is locked due to a billing
issue. CI is **not passed**. No billing/security settings or required checks were
changed or bypassed. These GitHub artifacts are built and qualified locally.

See `docs/REVIEW-2.1.1.md` for the per-module ledger and limits. This remains beta:
Python 3.10/3.11, the hosted matrix, macOS, unusual/remote filesystems and exhaustive
interactive GUI behavior remain unqualified. No personal scan data or private
prototype source is included. No package-registry publication is performed.

Download the wheel or source distribution and verify it against `SHA256SUMS`.
The original public source remains at `preserve/main-20261020`; integration history
remains at `integrate/portable-safety-20261020`. Source-tag and artifact receipts
identify the exact build; no original refs were deleted or rewritten.
