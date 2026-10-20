"""Automation-friendly command line interface for KingDirSTAT."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import (
    cleanup_candidates,
    compare_results,
    empty_directories,
    file_type_stats,
    find_duplicates,
    largest_entries,
    scan_summary,
    search_entries,
    stale_files,
    tree_rows,
    validate_hash_algorithm,
    validate_search_options,
)
from .drives import DriveSelector, drive_for_path
from .exporters import (
    atomic_write_text,
    dumps_snapshot,
    read_snapshot,
    write_csv,
    write_csv_rows,
    write_csv_stream,
    write_snapshot,
)
from .models import ScanOptions, ScanProgress, ScanResult
from .scanner import DirectoryScanner

_SCRIPT_COMMENT_LINE_SEPARATORS = re.compile(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3
EXIT_CANCELLED = 130
COMMANDS = {
    "scan",
    "report",
    "tree",
    "space",
    "clean",
    "largest",
    "search",
    "types",
    "extensions",
    "duplicates",
    "dupes",
    "empty",
    "stale",
    "export",
    "compare",
    "diff",
    "drives",
    "gui",
    "browse",
    "completion",
}
SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b)?\s*$", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingdirstat",
        description="Read-only disk usage analysis for humans and automation.",
    )
    parser.add_argument("--version", action="version", version=f"KingDirSTAT {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    scan_parent = argparse.ArgumentParser(add_help=False)
    _add_scan_options(scan_parent)

    scan = subparsers.add_parser(
        "scan", aliases=["report"], parents=[scan_parent], help="scan and summarise a directory"
    )
    scan.add_argument("path", nargs="?", default=".")
    scan.add_argument("--top", type=_positive_int, default=10, help="largest files to include")
    scan.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    scan.add_argument("--save", metavar="FILE", help="also save a versioned snapshot")
    _add_output_options(scan)
    scan.add_argument(
        "--path-mode", choices=["absolute", "relative", "redacted"], default="absolute"
    )
    scan.set_defaults(handler=_command_scan)

    largest = subparsers.add_parser("largest", parents=[scan_parent], help="list the largest files")
    largest.add_argument("path", nargs="?", default=".")
    largest.add_argument("--limit", "-n", type=_positive_int, default=25)
    largest.add_argument("--min-size", type=parse_size, default=0)
    largest.add_argument("--include-directories", action="store_true")
    largest.add_argument("--json", action="store_true")
    largest.set_defaults(handler=_command_largest)

    search = subparsers.add_parser(
        "search",
        parents=[scan_parent],
        help="search file names and root-relative paths",
    )
    search.add_argument("path")
    search.add_argument("query")
    search.add_argument(
        "--regex", action="store_true", help="interpret QUERY as a regular expression"
    )
    search.add_argument("--case-sensitive", action="store_true")
    search.add_argument("--include-directories", action="store_true")
    search.add_argument("--min-size", type=parse_size, default=0)
    search.add_argument("--max-size", type=parse_size)
    search.add_argument("--limit", "-n", type=_positive_int, default=100)
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=_command_search)

    types = subparsers.add_parser(
        "types", aliases=["extensions"], parents=[scan_parent], help="aggregate usage by extension"
    )
    types.add_argument("path", nargs="?", default=".")
    types.add_argument("--limit", "-n", type=_positive_int, default=50)
    types.add_argument("--json", action="store_true")
    types.set_defaults(handler=_command_types)

    duplicates = subparsers.add_parser(
        "duplicates",
        aliases=["dupes"],
        parents=[scan_parent],
        help="find true content duplicates using hashes",
    )
    duplicates.add_argument("path", nargs="?", default=".")
    duplicates.add_argument("--min-size", type=parse_size, default=1)
    duplicates.add_argument("--algorithm", default="sha256")
    duplicates.add_argument("--csv", metavar="FILE", help="also save one row per duplicate path")
    _add_output_options(duplicates)
    duplicates.add_argument("--json", action="store_true")
    duplicates.set_defaults(handler=_command_duplicates)

    empty = subparsers.add_parser("empty", parents=[scan_parent], help="list empty directories")
    empty.add_argument("path", nargs="?", default=".")
    empty.add_argument("--json", action="store_true")
    empty.set_defaults(handler=_command_empty)

    stale = subparsers.add_parser("stale", parents=[scan_parent], help="list old files")
    stale.add_argument("path", nargs="?", default=".")
    stale.add_argument("--days", type=_positive_int, default=365)
    stale.add_argument("--limit", "-n", type=_positive_int, default=100)
    stale.add_argument("--json", action="store_true")
    stale.set_defaults(handler=_command_stale)

    export = subparsers.add_parser("export", parents=[scan_parent], help="export a snapshot or CSV")
    export.add_argument("path")
    export.add_argument("output", help="destination file or - for stdout")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    _add_output_options(export)
    export.add_argument(
        "--path-mode", choices=["absolute", "relative", "redacted"], default="absolute"
    )
    export.set_defaults(handler=_command_export)

    compare = subparsers.add_parser(
        "compare", aliases=["diff"], help="compare two saved JSON snapshots"
    )
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--limit", "-n", type=_positive_int, default=50)
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(handler=_command_compare)

    drives = subparsers.add_parser("drives", help="list available drives and mount points")
    drives.add_argument("--json", action="store_true")
    drives.set_defaults(handler=_command_drives)

    gui = subparsers.add_parser("gui", aliases=["browse"], help="open the desktop interface")
    gui.add_argument("path", nargs="?")
    gui.add_argument("--snapshot", help="open a saved snapshot without rescanning")
    gui.set_defaults(handler=_command_gui)

    tree = subparsers.add_parser(
        "tree", parents=[scan_parent], help="print the directory tree with size shares"
    )
    tree.add_argument("path", nargs="?", default=".")
    tree.add_argument(
        "--depth",
        type=_non_negative_int,
        default=2,
        help="levels to display (default 2); --max-depth limits the scan itself",
    )
    tree.add_argument("--min-size", type=parse_size, default=0)
    tree.add_argument(
        "--children",
        type=_positive_int,
        help="show only the N largest children of each directory",
    )
    tree.add_argument("--dirs-only", action="store_true", help="hide files")
    tree.add_argument(
        "--total",
        action="store_true",
        help="show each row as a share of the scan total instead of its parent",
    )
    tree.add_argument("--json", action="store_true")
    tree.set_defaults(handler=_command_tree)

    space = subparsers.add_parser(
        "space", parents=[scan_parent], help="compare a scan against its volume's free space"
    )
    space.add_argument("path", nargs="?", default=".")
    space.add_argument("--json", action="store_true")
    space.set_defaults(handler=_command_space)

    clean = subparsers.add_parser(
        "clean",
        parents=[scan_parent],
        help="report regenerable caches and build output (never deletes)",
    )
    clean.add_argument("path", nargs="?", default=".")
    clean.add_argument(
        "--confidence",
        choices=["safe", "review", "all"],
        default="safe",
        help="safe = always regenerable (default); review = may hold deliverables",
    )
    clean.add_argument("--min-size", type=parse_size, default=0)
    clean.add_argument("--limit", "-n", type=_positive_int)
    clean.add_argument(
        "--emit-script",
        metavar="FILE",
        help="write a dry-run .ps1 or .sh you can review and run yourself",
    )
    clean.add_argument("--json", action="store_true")
    _add_output_options(clean)
    clean.set_defaults(handler=_command_clean)

    completion = subparsers.add_parser("completion", help="print a shell completion script")
    completion.add_argument("shell", choices=["powershell", "bash", "zsh"])
    completion.set_defaults(handler=_command_completion)
    return parser


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--overwrite", action="store_true", help="explicitly replace an existing output file"
    )


def _check_output(destination: str | None, *, overwrite: bool) -> None:
    if destination and destination != "-" and not overwrite and os.path.lexists(destination):
        raise FileExistsError(
            f"Output already exists: {destination}. Use --overwrite to replace it."
        )


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=_positive_int, default=min(8, os.cpu_count() or 4))
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude a name or root-relative glob; repeatable",
    )
    parser.add_argument("--max-depth", type=_non_negative_int)
    parser.add_argument("--follow-symlinks", action="store_true")
    parser.add_argument("--one-file-system", action="store_true")
    parser.add_argument("--allocated", action="store_true", help="measure allocated/physical size")
    parser.add_argument("--dedupe-hardlinks", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--show-errors", action="store_true")


def main(argv: Sequence[str] | None = None) -> int:
    # Windows pipes otherwise inherit a legacy code page, which can corrupt JSON
    # or reject valid filesystem names. Redirected CLI output has a UTF-8 contract.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure) and not stream.isatty():
            reconfigure(encoding="utf-8", errors="backslashreplace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    arguments = _normalise_legacy_arguments(arguments)
    parser = build_parser()
    namespace = parser.parse_args(arguments)
    if not hasattr(namespace, "handler"):
        namespace = parser.parse_args(["gui"])
    try:
        return int(namespace.handler(namespace))
    except KeyboardInterrupt:
        print("\nScan cancelled.", file=sys.stderr)
        return EXIT_CANCELLED
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL


def _command_scan(args: argparse.Namespace) -> int:
    _check_output(args.save, overwrite=args.overwrite)
    result = _scan(args.path, args)
    if args.save:
        write_snapshot(result, args.save, path_mode=args.path_mode, overwrite=args.overwrite)
    if args.json:
        payload = scan_summary(result)
        payload["largest_files"] = [
            _entry_record(entry) for entry in largest_entries(result.root, limit=args.top)
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_summary(result)
        largest = largest_entries(result.root, limit=args.top)
        if largest:
            print("\nLargest files")
            _print_table(
                ["Size", "Path"],
                [[format_size(entry.size), entry.path] for entry in largest],
            )
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_largest(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    entries = largest_entries(
        result.root,
        limit=args.limit,
        minimum_size=args.min_size,
        include_directories=args.include_directories,
    )
    if args.json:
        print(json.dumps([_entry_record(entry) for entry in entries], indent=2, ensure_ascii=False))
    else:
        _print_table(
            ["Size", "Type", "Path"],
            [
                [format_size(entry.size), "dir" if entry.is_dir else "file", entry.path]
                for entry in entries
            ],
        )
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_search(args: argparse.Namespace) -> int:
    validate_search_options(
        args.query,
        regex=args.regex,
        case_sensitive=args.case_sensitive,
        minimum_size=args.min_size,
        maximum_size=args.max_size,
    )
    result = _scan(args.path, args)
    entries = search_entries(
        result.root,
        args.query,
        regex=args.regex,
        case_sensitive=args.case_sensitive,
        include_directories=args.include_directories,
        minimum_size=args.min_size,
        maximum_size=args.max_size,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps([_entry_record(entry) for entry in entries], indent=2, ensure_ascii=False))
    else:
        _print_table(
            ["Size", "Type", "Path"],
            [
                [format_size(entry.size), "dir" if entry.is_dir else "file", entry.path]
                for entry in entries
            ],
        )
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_types(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    items = file_type_stats(result.root)[: args.limit]
    if args.json:
        print(json.dumps([asdict(item) for item in items], indent=2, ensure_ascii=False))
    else:
        total = result.total_size
        _print_table(
            ["Extension", "Files", "Logical", "Share", "Allocated"],
            [
                [
                    item.extension,
                    f"{item.files:,}",
                    format_size(item.size),
                    f"{(item.size / total * 100) if total else 0.0:5.1f}%",
                    format_size(item.allocated_size),
                ]
                for item in items
            ],
        )
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_duplicates(args: argparse.Namespace) -> int:
    _check_output(args.csv, overwrite=args.overwrite)
    validate_hash_algorithm(args.algorithm)
    result = _scan(args.path, args)
    report = find_duplicates(
        result.root,
        minimum_size=args.min_size,
        algorithm=args.algorithm,
        workers=args.workers,
    )
    if args.csv:
        written = write_csv_rows(
            args.csv,
            [
                "group_index",
                "algorithm",
                "digest",
                "file_size",
                "reclaimable_group_size",
                "path",
            ],
            (
                [
                    group_index,
                    args.algorithm,
                    group.digest,
                    group.size,
                    group.reclaimable_size,
                    entry.path,
                ]
                for group_index, group in enumerate(report.groups, 1)
                for entry in sorted(
                    group.files,
                    key=lambda item: (item.path.casefold(), item.path),
                )
            ),
            overwrite=args.overwrite,
        )
        print(f"Saved duplicate report CSV: {written.resolve()}", file=sys.stderr)
    if args.json:
        payload = {
            "algorithm": args.algorithm,
            "reclaimable_size": report.reclaimable_size,
            "groups": [
                {
                    "digest": group.digest,
                    "file_size": group.size,
                    "reclaimable_size": group.reclaimable_size,
                    "paths": [entry.path for entry in group.files],
                }
                for group in report.groups
            ],
            "errors": [asdict(error) for error in report.errors],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(
            f"{len(report.groups):,} duplicate groups; "
            f"up to {format_size(report.reclaimable_size)} reclaimable"
        )
        for index, group in enumerate(report.groups, 1):
            print(
                f"\n[{index}] {format_size(group.size)} each, "
                f"{format_size(group.reclaimable_size)} reclaimable"
            )
            for entry in group.files:
                print(f"  {entry.path}")
        if report.errors:
            print(f"\n{len(report.errors)} files could not be hashed.", file=sys.stderr)
        _print_scan_errors(result, args.show_errors)
    return EXIT_PARTIAL if result.errors or report.errors else _result_exit_code(result)


def _command_empty(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    entries = empty_directories(result.root)
    if args.json:
        print(json.dumps([entry.path for entry in entries], indent=2, ensure_ascii=False))
    else:
        print(f"{len(entries):,} empty directories")
        for entry in entries:
            print(entry.path)
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_stale(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    entries = stale_files(result.root, older_than_days=args.days)[: args.limit]
    if args.json:
        print(json.dumps([_entry_record(entry) for entry in entries], indent=2, ensure_ascii=False))
    else:
        _print_table(
            ["Modified", "Size", "Path"],
            [
                [
                    _format_timestamp(entry.modified_time),
                    format_size(entry.size),
                    entry.path,
                ]
                for entry in entries
            ],
        )
        _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_export(args: argparse.Namespace) -> int:
    _check_output(args.output, overwrite=args.overwrite)
    result = _scan(args.path, args)
    if args.output == "-":
        if args.format == "json":
            sys.stdout.write(dumps_snapshot(result, path_mode=args.path_mode))
        else:
            write_csv_stream(result, sys.stdout, path_mode=args.path_mode)
    elif args.format == "json":
        write_snapshot(result, args.output, path_mode=args.path_mode, overwrite=args.overwrite)
        print(f"Saved snapshot: {Path(args.output).resolve()}", file=sys.stderr)
    else:
        write_csv(result, args.output, path_mode=args.path_mode, overwrite=args.overwrite)
        print(f"Saved CSV: {Path(args.output).resolve()}", file=sys.stderr)
    return _result_exit_code(result)


def _command_compare(args: argparse.Namespace) -> int:
    before = read_snapshot(args.before)
    after = read_snapshot(args.after)
    difference = compare_results(before, after)
    changes = sorted(
        difference.added + difference.removed + difference.changed,
        key=lambda change: (-abs(change.delta), change.relative_path.casefold()),
    )[: args.limit]
    if args.json:
        print(
            json.dumps(
                {
                    "before_root": difference.before_root,
                    "after_root": difference.after_root,
                    "bytes_delta": difference.bytes_delta,
                    "added": [asdict(change) for change in difference.added],
                    "removed": [asdict(change) for change in difference.removed],
                    "changed": [asdict(change) for change in difference.changed],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"Net change: {format_signed_size(difference.bytes_delta)}")
        print(
            f"Added {len(difference.added):,} | Removed {len(difference.removed):,} | "
            f"Changed {len(difference.changed):,}"
        )
        _print_table(
            ["Delta", "Before", "After", "Path"],
            [
                [
                    format_signed_size(change.delta),
                    "-" if change.before_size is None else format_size(change.before_size),
                    "-" if change.after_size is None else format_size(change.after_size),
                    change.relative_path,
                ]
                for change in changes
            ],
        )
    return EXIT_OK


def _command_drives(args: argparse.Namespace) -> int:
    drives = DriveSelector().get_available_drives()
    if args.json:
        print(json.dumps([drive.to_dict() for drive in drives], indent=2, ensure_ascii=False))
    else:
        _print_table(
            ["Path", "Label", "Type", "Used", "Free", "Total", "Status"],
            [
                [
                    drive.path,
                    drive.label,
                    drive.drive_type,
                    format_size(drive.used_space),
                    format_size(drive.free_space),
                    format_size(drive.total_space),
                    "ready" if drive.is_accessible else "unavailable",
                ]
                for drive in drives
            ],
        )
    return EXIT_OK


def _command_gui(args: argparse.Namespace) -> int:
    from .gui_launcher import run_gui

    gui_args: list[str] = []
    if args.path:
        gui_args.append(args.path)
    if args.snapshot:
        gui_args.extend(["--snapshot", args.snapshot])
    return run_gui(gui_args)


def _command_completion(args: argparse.Namespace) -> int:
    commands = " ".join(sorted(COMMANDS))
    if args.shell == "powershell":
        print(
            "Register-ArgumentCompleter -Native -CommandName kingdirstat -ScriptBlock {\n"
            "  param($wordToComplete, $commandAst, $cursorPosition)\n"
            f"  '{commands}'.Split(' ') | Where-Object {{ $_ -like \"$wordToComplete*\" }} | "
            "ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }\n"
            "}"
        )
    elif args.shell == "bash":
        print(
            "_kingdirstat() { COMPREPLY=( $(compgen -W '"
            f"{commands}"
            '\' -- "${COMP_WORDS[COMP_CWORD]}") ); }; complete -F _kingdirstat kingdirstat'
        )
    else:
        print(f"#compdef kingdirstat\n_arguments '1:command:({commands})' '*:path:_files'")
    return EXIT_OK


def _command_tree(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    rows = tree_rows(
        result.root,
        max_depth=args.depth,
        minimum_size=args.min_size,
        directories_only=args.dirs_only,
        children_limit=args.children,
    )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "path": row.entry.path,
                        "name": row.entry.name,
                        "type": "directory" if row.entry.is_dir else "file",
                        "depth": row.depth,
                        "logical_size": row.entry.size,
                        "allocated_size": row.entry.allocated_size,
                        "share_of_parent": round(row.share_of_parent, 4),
                        "share_of_total": round(row.share_of_total, 4),
                        "files": row.entry.file_count,
                        "directories": row.entry.directory_count,
                        "modified_time": row.entry.modified_time,
                        "attributes": row.entry.attribute_string,
                    }
                    for row in rows
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return _result_exit_code(result)

    share_label = "% total" if args.total else "% parent"
    headers = ["Size", share_label, "Items", "Files", "Dirs", "Modified", "Attr", "Name"]
    body: list[list[str]] = []
    for row in rows:
        entry = row.entry
        share = row.share_of_total if args.total else row.share_of_parent
        name = f"{'  ' * row.depth}{entry.name}"
        if entry.is_dir:
            name += "/"
        if not entry.scan_complete and entry.skip_reason:
            name += f"  [{entry.skip_reason}]"
        body.append(
            [
                format_size(entry.size),
                f"{share:5.1f}%",
                f"{entry.file_count + entry.directory_count:,}" if entry.is_dir else "",
                f"{entry.file_count:,}" if entry.is_dir else "",
                f"{entry.directory_count:,}" if entry.is_dir else "",
                _format_timestamp(entry.modified_time),
                entry.attribute_string,
                name,
            ]
        )
    _print_tree_table(headers, body)
    _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_space(args: argparse.Namespace) -> int:
    result = _scan(args.path, args)
    drive = drive_for_path(args.path)
    unmeasured = max(0, drive.used_space - result.total_size)
    payload = {
        "path": result.root.path,
        "volume": drive.path,
        "label": drive.label,
        "file_system": drive.file_system,
        "drive_type": drive.drive_type,
        "total_space": drive.total_space,
        "used_space": drive.used_space,
        "free_space": drive.free_space,
        "scanned_size": result.total_size,
        "scanned_allocated_size": result.total_allocated_size,
        "unmeasured_space": unmeasured,
        "free_percent": round(drive.free_space / drive.total_space * 100, 2)
        if drive.total_space
        else 0.0,
        "scanned_percent_of_volume": round(result.total_size / drive.total_space * 100, 2)
        if drive.total_space
        else 0.0,
        "errors": len(result.errors),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return _result_exit_code(result)

    print(f"Path:        {result.root.path}")
    print(f"Volume:      {drive.path}  ({drive.label}, {drive.file_system}, {drive.drive_type})")
    print(f"Capacity:    {format_size(drive.total_space)}")
    print(f"Used:        {format_size(drive.used_space)}")
    print(f"Free:        {format_size(drive.free_space)}  ({payload['free_percent']:.1f}%)")
    print(
        f"Scanned:     {format_size(result.total_size)}  "
        f"({payload['scanned_percent_of_volume']:.1f}% of the volume)"
    )
    if result.options.measure_allocated:
        print(f"Allocated:   {format_size(result.total_allocated_size)}")
    print(f"Unmeasured:  {format_size(unmeasured)}  (elsewhere on the volume, or unreadable)")
    if drive.free_space and drive.total_space:
        ratio = drive.free_space / drive.total_space
        if ratio < 0.10:
            print(
                "\nWarning: under 10% free. SSDs lose write performance and wear levelling "
                "headroom below this point.",
                file=sys.stderr,
            )
    _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _command_clean(args: argparse.Namespace) -> int:
    _check_output(args.emit_script, overwrite=args.overwrite)
    result = _scan(args.path, args)
    confidence = None if args.confidence == "all" else args.confidence
    candidates = cleanup_candidates(
        result.root,
        confidence=confidence,
        minimum_size=args.min_size,
    )
    if args.limit:
        candidates = candidates[: args.limit]
    total = sum(candidate.size for candidate in candidates)

    if args.emit_script:
        written = _write_cleanup_script(
            args.emit_script, candidates, result.root.path, overwrite=args.overwrite
        )
        print(f"Wrote review script: {written}", file=sys.stderr)
        print("It runs as a dry run until you edit it. Nothing has been deleted.", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "root": result.root.path,
                    "reclaimable_size": total,
                    "candidates": [
                        {
                            "path": candidate.entry.path,
                            "type": "directory" if candidate.entry.is_dir else "file",
                            "logical_size": candidate.size,
                            "allocated_size": candidate.entry.allocated_size,
                            "rule": candidate.rule.name,
                            "confidence": candidate.rule.confidence,
                            "note": candidate.rule.note,
                        }
                        for candidate in candidates
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return _result_exit_code(result)

    print(f"{len(candidates):,} regenerable items, {format_size(total)} reclaimable")
    print("KingDirSTAT does not delete anything. Review before acting.\n")
    _print_table(
        ["Size", "Confidence", "Rule", "Path"],
        [
            [
                format_size(candidate.size),
                candidate.rule.confidence,
                candidate.rule.name,
                candidate.entry.path,
            ]
            for candidate in candidates
        ],
    )
    _print_scan_errors(result, args.show_errors)
    return _result_exit_code(result)


def _script_comment_text(value: object) -> str:
    """Collapse characters that could terminate a generated script comment."""

    return _SCRIPT_COMMENT_LINE_SEPARATORS.sub(" ", str(value))


def _write_cleanup_script(
    destination: str,
    candidates: Sequence[Any],
    root: str,
    *,
    overwrite: bool = True,
) -> Path:
    output = Path(destination)
    powershell = output.suffix.lower() == ".ps1"
    lines: list[str] = []
    if powershell:
        lines += [
            "# KingDirSTAT cleanup review script",
            f"# Root: {_script_comment_text(root)}",
            "# Generated as a DRY RUN. Every command carries -WhatIf, so nothing is deleted.",
            "# Read the list, delete lines you want to keep, then remove -WhatIf to act.",
            "",
            "$ErrorActionPreference = 'Continue'",
            "",
        ]
        for candidate in candidates:
            escaped = candidate.entry.path.replace("'", "''")
            lines.append(
                f"# {format_size(candidate.size)}  "
                f"{_script_comment_text(candidate.rule.name)}  "
                f"({_script_comment_text(candidate.rule.note)})"
            )
            lines.append(f"Remove-Item -LiteralPath '{escaped}' -Recurse -Force -WhatIf")
            lines.append("")
    else:
        lines += [
            "#!/bin/sh",
            "# KingDirSTAT cleanup review script",
            f"# Root: {_script_comment_text(root)}",
            "# DRY_RUN=1 prints what would happen. Set DRY_RUN=0 to actually delete.",
            "",
            "DRY_RUN=1",
            "run() {",
            '  if [ "$DRY_RUN" = "1" ]; then',
            '    echo "would remove: $1"',
            "  else",
            '    rm -rf -- "$1"',
            "  fi",
            "}",
            "",
        ]
        for candidate in candidates:
            escaped = candidate.entry.path.replace("'", "'\\''")
            lines.append(
                f"# {format_size(candidate.size)}  "
                f"{_script_comment_text(candidate.rule.name)}  "
                f"({_script_comment_text(candidate.rule.note)})"
            )
            lines.append(f"run '{escaped}'")
            lines.append("")
    atomic_write_text(output, "\n".join(lines) + "\n", overwrite=overwrite)
    return output.resolve()


def _print_tree_table(headers: list[str], rows: list[list[str]]) -> None:
    """Like _print_table, but never truncates the final indented name column."""

    if not rows:
        print("No results.")
        return
    fixed = len(headers) - 1
    widths = [len(header) for header in headers]
    for row in rows:
        for index in range(fixed):
            widths[index] = max(widths[index], len(str(row[index])))
    header_line = "  ".join(headers[index].ljust(widths[index]) for index in range(fixed))
    print(f"{header_line}  {headers[-1]}")
    print("  ".join("-" * widths[index] for index in range(fixed)) + "  " + "-" * widths[-1])
    for row in rows:
        cells = "  ".join(str(row[index]).ljust(widths[index]) for index in range(fixed))
        print(f"{cells}  {row[-1]}")


def _scan(path: str, args: argparse.Namespace) -> ScanResult:
    options = ScanOptions(
        workers=args.workers,
        exclude=tuple(args.exclude),
        max_depth=args.max_depth,
        follow_symlinks=args.follow_symlinks,
        same_file_system=args.one_file_system,
        measure_allocated=args.allocated,
        dedupe_hardlinks=args.dedupe_hardlinks,
    )
    scanner = DirectoryScanner(options)
    show_progress = not args.no_progress and sys.stderr.isatty()
    last_width = 0

    def progress(update: ScanProgress) -> None:
        nonlocal last_width
        if not show_progress:
            return
        message = (
            f"Scanning {update.files_scanned:,} files | "
            f"{format_size(update.total_size)} | {update.scan_rate:,.0f} files/s"
        )
        last_width = max(last_width, len(message))
        print(f"\r{message:<{last_width}}", end="", file=sys.stderr, flush=True)

    try:
        return scanner.scan(path, progress=progress)
    finally:
        if show_progress:
            print(file=sys.stderr)


def _print_summary(result: ScanResult) -> None:
    print(f"Root:        {result.root.path}")
    print(f"Logical:     {format_size(result.total_size)}")
    if result.options.measure_allocated:
        print(f"Allocated:   {format_size(result.total_allocated_size)}")
    print(f"Files:       {result.files:,}")
    print(f"Directories: {result.directories:,}")
    print(f"Duration:    {result.duration:.2f} s")
    print(f"Errors:      {len(result.errors):,}")
    if result.cancelled:
        print("Status:      cancelled (partial result)")


def _print_scan_errors(result: ScanResult, show_details: bool) -> None:
    if not result.errors:
        return
    print(
        f"\nWarning: {len(result.errors):,} filesystem entries could not be read.", file=sys.stderr
    )
    if show_details:
        for error in result.errors:
            print(f"  {error.operation}: {error.path}: {error.message}", file=sys.stderr)


def _entry_record(entry: Any) -> dict[str, Any]:
    return {
        "path": entry.path,
        "name": entry.name,
        "type": "directory" if entry.is_dir else "file",
        "logical_size": entry.size,
        "allocated_size": entry.allocated_size,
        "modified_time": entry.modified_time,
    }


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        print("No results.")
        return
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = min(72, max(widths[index], len(str(cell))))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            text = str(value)
            if len(text) > widths[index]:
                text = "…" + text[-(widths[index] - 1) :]
            cells.append(text.ljust(widths[index]))
        print("  ".join(cells))


def parse_size(value: str) -> int:
    match = SIZE_PATTERN.match(value)
    if not match:
        raise argparse.ArgumentTypeError(f"invalid size: {value!r} (example: 250MiB)")
    number = float(match.group(1))
    suffix = (match.group(2) or "B").lower()
    powers = {
        "b": 0,
        "kb": 1,
        "kib": 1,
        "mb": 2,
        "mib": 2,
        "gb": 3,
        "gib": 3,
        "tb": 4,
        "tib": 4,
        "pb": 5,
        "pib": 5,
        "eb": 6,
        "eib": 6,
    }
    if suffix not in powers:
        raise argparse.ArgumentTypeError(f"invalid size suffix: {suffix}")
    return int(number * (1024 ** powers[suffix]))


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        if abs(size) < 1024 or unit == "EiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} EiB"


def format_signed_size(value: int) -> str:
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}{format_size(abs(value))}"


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return "unknown"
    from datetime import datetime

    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d")


def _result_exit_code(result: ScanResult) -> int:
    if result.cancelled:
        return EXIT_CANCELLED
    if result.errors:
        return EXIT_PARTIAL
    return EXIT_OK


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _normalise_legacy_arguments(arguments: list[str]) -> list[str]:
    if not arguments:
        return ["gui"]
    if arguments[0] == "--gui":
        return ["gui", *arguments[1:]]
    if arguments[0] == "--cli":
        return ["scan", *arguments[1:]]
    first = arguments[0]
    if first not in COMMANDS and not first.startswith("-"):
        return ["scan", *arguments]
    return arguments
