"""Dependency-safe entry point for the optional desktop interface."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from typing import cast

GUI_INSTALL_MESSAGE = (
    "The KingDirSTAT GUI requires PySide6. "
    "Install it with: python -m pip install 'kingdirstat[gui]'"
)


class GuiDependencyError(RuntimeError):
    """Raised when the optional GUI dependency is unavailable."""


def _load_gui_main() -> Callable[[Sequence[str] | None], int]:
    try:
        module = importlib.import_module("kingdirstat.gui")
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "PySide6" or missing.startswith("PySide6."):
            raise GuiDependencyError(GUI_INSTALL_MESSAGE) from exc
        raise
    return cast(Callable[[Sequence[str] | None], int], module.main)


def run_gui(argv: Sequence[str] | None = None) -> int:
    """Start the GUI, raising a concise error when its extra is not installed."""

    return int(_load_gui_main()(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script wrapper that never exposes a missing-PySide traceback."""

    try:
        return run_gui(argv)
    except GuiDependencyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
