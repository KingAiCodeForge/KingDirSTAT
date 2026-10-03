"""Shared fixtures. Every test runs against a tree it built itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Sizes are distinct so any mix-up shows up as a wrong number, not a coincidence.
A_TXT = b"a" * 100
B_LOG = b"b" * 200
C_TXT = b"c" * 300
D_PYC = b"d" * 50
DUPLICATE = b"z" * 128

TOTAL_SIZE = len(A_TXT) + len(B_LOG) + len(C_TXT) + len(D_PYC) + 2 * len(DUPLICATE)
TOTAL_FILES = 6
TOTAL_DIRECTORIES = 4  # root, sub, sub/__pycache__, empty


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """Build a known tree.

    root/
      a.txt                100 B
      b.log                200 B
      dup1.bin             128 B  (identical to dup2.bin)
      dup2.bin             128 B
      empty/                      (no children)
      sub/
        c.txt              300 B
        __pycache__/
          d.pyc             50 B
    """

    root = tmp_path / "root"
    (root / "sub" / "__pycache__").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_bytes(A_TXT)
    (root / "b.log").write_bytes(B_LOG)
    (root / "dup1.bin").write_bytes(DUPLICATE)
    (root / "dup2.bin").write_bytes(DUPLICATE)
    (root / "sub" / "c.txt").write_bytes(C_TXT)
    (root / "sub" / "__pycache__" / "d.pyc").write_bytes(D_PYC)
    return root
