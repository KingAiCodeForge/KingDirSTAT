"""KingDirSTAT public package."""

from .models import Entry, ScanError, ScanOptions, ScanProgress, ScanResult
from .scanner import DirectoryScanner, ScanCancelled

__all__ = [
    "DirectoryScanner",
    "Entry",
    "ScanCancelled",
    "ScanError",
    "ScanOptions",
    "ScanProgress",
    "ScanResult",
]

__version__ = "2.1.1"
