"""Run with a fresh CLI-only wheel installation, from outside the checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import kingdirstat


def main() -> None:
    assert kingdirstat.__version__ == "2.1.1"
    assert "site-packages" in str(Path(kingdirstat.__file__).resolve())
    assert importlib.util.find_spec("PySide6") is None, "use a clean CLI-only environment"

    def run(*args: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "kingdirstat", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == code, (args, result.returncode, result.stderr)
        assert "Traceback" not in result.stderr, result.stderr
        return result

    with tempfile.TemporaryDirectory(prefix="KingDirSTAT portable é ") as temporary:
        base = Path(temporary)
        root = base / "données with spaces"
        cache = root / "__pycache__"
        cache.mkdir(parents=True)
        (root / "café.txt").write_bytes(b"duplicate data")
        (root / "copy.txt").write_bytes(b"duplicate data")
        (cache / "cache.pyc").write_bytes(b"fixture only")
        (root / "empty").mkdir()

        def originals() -> dict[str, str]:
            return {
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*")
                if path.is_file()
            }

        before = originals()
        run("--version")
        run("--help")
        for command in (
            "scan",
            "tree",
            "space",
            "largest",
            "types",
            "duplicates",
            "empty",
            "stale",
            "clean",
        ):
            json.loads(run(command, str(root), "--json", "--no-progress").stdout)
        json.loads(run("search", str(root), "café", "--json", "--no-progress").stdout)
        run("completion", "bash")
        run("completion", "powershell")
        run("completion", "zsh")
        snapshot = base / "saved snapshot.json"
        run("export", str(root), str(snapshot), "--path-mode", "redacted", "--no-progress")
        assert str(base) not in snapshot.read_text(encoding="utf-8")
        assert (
            json.loads(run("compare", str(snapshot), str(snapshot), "--json").stdout)["bytes_delta"]
            == 0
        )
        run("export", str(root), str(base / "report.csv"), "--format", "csv", "--no-progress")
        for suffix in ("sh", "ps1"):
            run(
                "clean", str(root), "--emit-script", str(base / f"review.{suffix}"), "--no-progress"
            )
        run("scan", str(base / "missing"), "--no-progress", code=1)
        source = root / "café.txt"
        for command, options in (
            ("scan", ["--save", str(source)]),
            ("export", [str(source)]),
            ("duplicates", ["--csv", str(source)]),
            ("clean", ["--emit-script", str(source)]),
        ):
            run(command, str(root), *options, "--no-progress", code=1)
        run("duplicates", str(root), "--algorithm", "shake_128", code=1)
        missing_gui = run("gui", str(root), code=1)
        assert "requires PySide6" in missing_gui.stderr
        gui_entry = Path(sys.executable).parent / (
            "kingdirstat-gui.exe" if sys.platform == "win32" else "kingdirstat-gui"
        )
        result = subprocess.run([str(gui_entry)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 1 and "requires PySide6" in result.stderr
        assert "Traceback" not in result.stderr
        assert originals() == before, "an analysis command changed fixture input"
    print(
        "Installed CLI-only wheel qualification passed: commands, Unicode/spaces, errors, aliases, no input mutation."
    )


if __name__ == "__main__":
    main()
