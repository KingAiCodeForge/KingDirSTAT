"""The optional GUI entry point must fail cleanly on a base installation."""

from __future__ import annotations

import importlib

import pytest

from kingdirstat import gui_launcher
from kingdirstat.cli import EXIT_FATAL
from kingdirstat.cli import main as cli_main


def _missing_pyside(_name: str) -> object:
    error = ModuleNotFoundError("No module named 'PySide6'")
    error.name = "PySide6"
    raise error


def test_direct_gui_entry_explains_how_to_install_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gui_launcher.importlib, "import_module", _missing_pyside)

    assert gui_launcher.main([]) == EXIT_FATAL
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires PySide6" in captured.err
    assert "kingdirstat[gui]" in captured.err
    assert "Traceback" not in captured.err


def test_cli_gui_command_uses_the_same_dependency_guidance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gui_launcher.importlib, "import_module", _missing_pyside)

    assert cli_main(["gui"]) == EXIT_FATAL
    captured = capsys.readouterr()
    assert "requires PySide6" in captured.err
    assert "kingdirstat[gui]" in captured.err
    assert "Traceback" not in captured.err


def test_launcher_does_not_hide_an_unrelated_missing_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_internal_dependency(_name: str) -> object:
        error = ModuleNotFoundError("No module named 'unexpected_dependency'")
        error.name = "unexpected_dependency"
        raise error

    monkeypatch.setattr(gui_launcher.importlib, "import_module", missing_internal_dependency)

    with pytest.raises(ModuleNotFoundError, match="unexpected_dependency"):
        gui_launcher.run_gui([])


def test_launcher_forwards_arguments_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[list[str] | None] = []

    class FakeGuiModule:
        @staticmethod
        def main(argv: list[str] | None = None) -> int:
            received.append(argv)
            return 7

    monkeypatch.setattr(importlib, "import_module", lambda _name: FakeGuiModule)

    assert gui_launcher.run_gui(["C:/data"]) == 7
    assert received == [["C:/data"]]
