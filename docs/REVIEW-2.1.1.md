# KingDirSTAT 2.1.1 preservation and review

## Source and preservation

The only public branch at inspection was `main`, commit
`1efd970c1e297dfbb7eed45bfbee39c9a22510c3` (the initial 2.1.0 release source).
No public PR, other branch, or release was present. This release does not claim to
merge an earlier PR. `preserve/main-20261020` retains that exact source; the
`integrate/portable-safety-20261020` branch and separate worktree retain the review.
An original all-refs Git bundle and ref manifest are retained separately by the
maintainer. No original branch was deleted or rewritten.

The public history contains no legacy top-level prototype launchers or generated
scan data. None was imported from private/local projects. Those prototypes remain
unsupported and outside this distribution; this manifest does not assert their
contents were reviewed. No scan of the maintainer's personal files was performed.

## Script and module ledger

Every row below was inspected for purpose, paths, dependencies, errors and mutation.
"Tested" means the named bounded checks ran, not exhaustive correctness of every
branch or every filesystem. Package paths and user arguments replace no hidden
machine-specific configuration: none was found in these public modules.

| Public source | Change | Bounded verification |
| --- | --- | --- |
| `__init__.py` | Version 2.1.1 | Installed import and `--version` |
| `__main__.py` | Inspected, unchanged | Installed `python -m kingdirstat` help and commands |
| `cli.py` | Protected output defaults, explicit overwrite, early hash validation | CLI suite; installed commands, missing path/dependency, source/output aliases |
| `analysis.py` | Reject variable-length/unsupported hashes before file reads | Analysis suite and hash regression; fixture duplicate/search/tree/clean commands |
| `scanner.py` | Mark directory incomplete on child stat failure | Scanner suite; synthetic error/cancellation/depth limits and known-byte trees |
| `models.py` | Inspected, unchanged | Scanner/round-trip tests and snapshot validation regressions |
| `exporters.py` | Atomic no-clobber option, snapshot entry validation, complete CSV text guard | Existing atomic-failure tests plus concurrent destination creation, malformed snapshots and formula-prefix cases |
| `drives.py` | Inspected, unchanged | `drives`/`space` tests, synthetic fixture volume context; not all mounts/filesystems |
| `gui_launcher.py` | Inspected, unchanged | Launcher tests and clean CLI-only installation without PySide6 |
| `gui.py` | Safe close when cancellation times out | Offscreen GUI model/selection/find/drive tests and synthetic slow-worker close |
| `treemap.py` | Inspected, unchanged | Offscreen GUI suite; rendering is not a full interactive visual/accessibility acceptance test |
| `tests/conftest.py`, existing `test_*.py` | Existing suite retained; GUI regression added | Full suite; test fixture data only |
| `tests/test_release_safety.py` | New regressions | Data preservation, races, aliases, CSV prefixes, malformed snapshots, unreadable metadata |
| `tests/installed_smoke.py` | New CLI-only wheel qualification | Run with a clean noneditable wheel install outside the checkout, Unicode/space paths, input SHA-256 comparison |
| `.github/workflows/ci.yml` | Added clean installed-wheel smoke | Ubuntu/Windows Python 3.10–3.13 suite and Ubuntu lint/type/build jobs |
| `.github/workflows/release.yml` | Accurate checked-in notes, checksums, CLI-only wheel smoke | Tag/version check, tests, quality checks and build before GitHub-only release |

No public runtime script is excluded or still unreviewed. Older private prototypes
and other repositories are excluded historical material, not qualified scripts.

## Reproduction and limits

In a development virtual environment with `.[gui,dev]` installed:

```text
python -m pytest
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy
python -m build
```

For independent installation qualification, make a second virtual environment
without GUI extras, install only the built wheel (`pip install --no-deps <wheel>`),
change to a directory outside this checkout, and run that environment's Python on
the absolute path to `tests/installed_smoke.py`. Do not set `PYTHONPATH`.

Local qualification used Windows/Python 3.13 with PySide6 6.9.2 for the full offscreen
suite and a clean CLI-only wheel environment. CI separately records its exact
platform/Python matrix outcomes. Release receipts record source SHAs and artifact
SHA-256 values. A passing smoke test does not replace the module ledger above.

The project remains beta. Interactive desktop behavior on every platform, macOS,
unusual/remote filesystems, live network shares, every permission/symlink race,
enormous/deep JSON snapshots and whole-drive performance were not exhaustively
qualified. Unix no-clobber output publication requires same-directory hard links;
unsupported filesystems fail without replacing existing output. GUI save dialogs
and the Python API retain their explicit output-overwrite behavior. `clean` emits
review scripts only; qualification did not run removal commands or delete user data.

## Exposure and distribution review

The tracked public source, tests, license, documentation and workflow files were
reviewed. `.git/info/exclude` contains only its default comments; no local/global
ignore changes were made. `.gitignore` continues excluding environments, caches,
credentials, local config and scan/profiling data. No ignored material was force
added or made public. Wheel/sdist contents are allowlisted package source, tests
(sdist), public documentation and license; local bundles, test runs and scan outputs
stay outside the checkout and are not distribution inputs.
