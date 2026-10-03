# Contributing to KingDirSTAT

Thank you for helping improve KingDirSTAT. Bug reports, focused feature proposals, documentation
improvements, and tested pull requests are welcome.

## Before opening an issue

- Search existing issues first.
- Include the KingDirSTAT version, Python version, operating system, command, expected behavior,
  and actual behavior.
- Reproduce filesystem bugs with a small synthetic directory tree when possible.
- Remove usernames, absolute paths, filenames, snapshots, logs, and other private data before
  posting. Prefer `--path-mode redacted` for CLI snapshot evidence.
- Report security problems privately according to [SECURITY.md](SECURITY.md).

## Development setup

Python 3.10 through 3.13 is supported.

```bash
git clone https://github.com/KingAiCodeForge/KingDirSTAT.git
cd KingDirSTAT
python -m venv .venv
```

Activate `.venv` for your shell, then install the checkout and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[gui,dev]"
```

Run the application through the installed entry point (`kingdirstat`) or as a module
(`python -m kingdirstat`), not through ad hoc top-level launchers from older private prototypes.

## Quality checks

Run the same checks as CI before submitting a pull request:

```bash
python -m pytest
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy
python -m build
```

GUI tests use Qt's offscreen platform in CI. To reproduce that configuration locally, set
`QT_QPA_PLATFORM=offscreen` before running pytest. In PowerShell:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
```

## Pull-request guidelines

- Keep each pull request focused and explain user-visible behavior and safety implications.
- Add or update tests for behavioral changes. Use temporary directories rather than real home,
  project, download, or mounted-drive paths.
- Preserve the default boundary: KingDirSTAT may inspect scanned content and write explicitly
  requested reports, but it must not delete, move, or rewrite scanned content.
- Do not follow symlinks, junctions, reparse points, or cloud placeholders without an explicit and
  well-tested opt-in.
- Never commit generated snapshots, benchmark output, personal filesystem inventories, proprietary
  data, secrets, or credentials.
- Do not copy WinDirStat source code or assets into this repository. KingDirSTAT is independent and
  MIT-licensed; discuss any third-party code or asset and its licence before inclusion.
- Update `README.md` and `CHANGELOG.md` when a change affects users.

By contributing, you agree that your contribution is licensed under this repository's MIT License.
