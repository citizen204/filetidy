"""filetidy command line interface."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import CONFIG_FILENAME, load as load_config, user_config_path, write_starter
from .journal import Journal
from .names import diagnose, safe_name
from .organizer import Organizer, Plan, find_unportable, undo as undo_moves, unique_destination
from .rules import Ruleset
from . import service as service_mod

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Modern Windows Terminal sets this; older conhost does not.
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON"))
    return True


COLOUR = _supports_colour()


def paint(text: str, code: str) -> str:
    return "\033[%sm%s\033[0m" % (code, text) if COLOUR else text


def bold(text: str) -> str:
    return paint(text, "1")


def dim(text: str) -> str:
    return paint(text, "2")


def green(text: str) -> str:
    return paint(text, "32")


def yellow(text: str) -> str:
    return paint(text, "33")


def red(text: str) -> str:
    return paint(text, "31")


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return "%3.1f %s" % (num, unit) if unit != "B" else "%d B" % num
        num /= 1024.0
    return "%.1f PB" % num


def _prepare_stdout() -> None:
    """Make sure non-ASCII filenames print on a Windows console."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Shared construction
# ---------------------------------------------------------------------------

def build_organizer(args: argparse.Namespace, target: Optional[Path]) -> Organizer:
    config = load_config(
        explicit=Path(args.config) if getattr(args, "config", None) else None,
        target_dir=target,
    )

    def pick(attr: str, key: str):
        value = getattr(args, attr, None)
        return config[key] if value is None else value

    ruleset = Ruleset(
        categories=config["categories"],
        fallback=config["fallback_category"],
        detect_screenshots=config["detect_screenshots"],
        screenshot_category=config["screenshot_category"],
    )
    excludes = list(config.get("excludes", [])) + list(getattr(args, "exclude", None) or [])
    return Organizer(
        ruleset=ruleset,
        archive_name=pick("archive", "archive_name"),
        recursive=bool(pick("recursive", "recursive")),
        include_hidden=bool(pick("include_hidden", "include_hidden")),
        date_subfolders=bool(pick("date_folders", "date_subfolders")),
        date_format=config["date_format"],
        min_age_seconds=int(pick("min_age", "min_age_seconds")),
        excludes=excludes,
        sanitize_names=bool(pick("sanitize_names", "sanitize_names")),
    )


def print_plan(plan: Plan, root: Path, verbose: bool) -> None:
    if not plan.moves and not plan.skips:
        print(dim("Nothing to do -- no loose files found in %s" % root))
        return

    buckets = {}
    for move in plan.moves:
        buckets.setdefault(move.category, []).append(move)

    for category in sorted(buckets):
        moves = buckets[category]
        size = 0
        for move in moves:
            try:
                size += move.source.stat().st_size
            except OSError:
                pass
        print("  %s  %s  %s" % (
            bold("%-14s" % category),
            "%3d file%s" % (len(moves), "" if len(moves) == 1 else "s"),
            dim("(%s)" % human_size(size)),
        ))
        if verbose:
            for move in moves:
                print("      %s %s %s" % (
                    move.source.name,
                    dim("->"),
                    dim(str(move.destination.relative_to(root))),
                ))

    if plan.skips:
        print()
        print(dim("  Skipped %d item%s:" % (len(plan.skips), "" if len(plan.skips) == 1 else "s")))
        shown = plan.skips if verbose else plan.skips[:5]
        for skip in shown:
            print(dim("      %s  (%s)" % (skip.source.name, skip.reason)))
        if len(plan.skips) > len(shown):
            print(dim("      ... and %d more (use -v to list)" % (len(plan.skips) - len(shown))))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    roots = [Path(p).expanduser() for p in (args.paths or ["."])]
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        for root in missing:
            print(red("Not a directory: %s" % root), file=sys.stderr)
        return 2

    # In quiet mode nothing is printed unless something actually happened, so
    # a service running every few minutes does not fill a log with "nothing
    # to do".
    quiet = getattr(args, "quiet", False)
    exit_code = 0

    for root in roots:
        organizer = build_organizer(args, root)
        try:
            plan = organizer.plan(root)
        except OSError as exc:
            print("%s cannot read %s: %s" % (red("!!"), root, exc.strerror or exc),
                  file=sys.stderr)
            if sys.platform == "darwin" and getattr(exc, "errno", None) in (1, 13):
                print(dim("   macOS privacy protection blocks this. Grant access in"),
                      file=sys.stderr)
                print(dim("   System Settings > Privacy & Security > Full Disk Access."),
                      file=sys.stderr)
            exit_code = 2
            continue

        if quiet and not plan.moves:
            continue

        if not quiet:
            mode = bold(green("APPLY")) if args.apply else bold(yellow("DRY RUN"))
            print("%s  %s" % (mode, root))
            print()
            print_plan(plan, root.resolve(), args.verbose)

        if not plan.moves:
            if not quiet:
                print()
            continue

        if not args.apply:
            print()
            print("  %d file%s would move (%s). Re-run with %s to do it." % (
                len(plan.moves),
                "" if len(plan.moves) == 1 else "s",
                human_size(plan.total_bytes),
                bold("--apply"),
            ))
            continue

        journal = None if args.no_journal else Journal()
        result = organizer.apply(plan, journal=journal)

        if quiet:
            print("%s  %s  %d file%s filed" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), root,
                len(result.moved), "" if len(result.moved) == 1 else "s",
            ))
        else:
            print()
            print("  %s %d file%s moved." % (
                green("OK"), len(result.moved), "" if len(result.moved) == 1 else "s",
            ))
        if result.failed:
            exit_code = 1
            print("  %s %d failed:" % (red("!!"), len(result.failed)))
            for failure in result.failed:
                print("      %s  (%s)" % (failure.source.name, failure.reason))
        if journal and result.session_id and not quiet:
            print(dim("  Undo with:  filetidy undo"))
        sys.stdout.flush()

    return exit_code


def cmd_undo(args: argparse.Namespace) -> int:
    journal = Journal()
    sessions = journal.sessions()

    if args.list:
        if not sessions:
            print(dim("No recorded sessions."))
            return 0
        for summary in sessions:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(summary["ts"]))
            print("  %s  %s  %d file%s" % (
                bold(summary["session"]), stamp, summary["count"],
                "" if summary["count"] == 1 else "s",
            ))
        return 0

    session_id = args.session or journal.last_session_id()
    if not session_id:
        print(dim("Nothing to undo."))
        return 0

    moves = journal.moves_for(session_id)
    if not moves:
        print(red("No such session: %s" % session_id), file=sys.stderr)
        return 2

    print("Undoing %s (%d file%s)" % (bold(session_id), len(moves), "" if len(moves) == 1 else "s"))
    result = undo_moves(moves)
    print("  %s %d restored." % (green("OK"), len(result.moved)))
    if result.failed:
        print("  %s %d could not be restored:" % (yellow("??"), len(result.failed)))
        for failure in result.failed:
            print("      %s  (%s)" % (failure.source.name, failure.reason))
    if not result.failed:
        journal.forget(session_id)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(red("Not a directory: %s" % root), file=sys.stderr)
        return 2

    organizer = build_organizer(args, root)
    journal = None if args.no_journal else Journal()
    print("Watching %s every %ds. Ctrl-C to stop." % (bold(str(root)), args.interval))
    print(dim("Files younger than %ds are left alone so in-progress downloads settle first."
              % organizer.min_age_seconds))
    try:
        while True:
            plan = organizer.plan(root)
            if plan.moves:
                result = organizer.apply(plan, journal=journal)
                stamp = time.strftime("%H:%M:%S")
                for move in result.moved:
                    print("  [%s] %s %s %s" % (
                        stamp, move.source.name, dim("->"), move.category,
                    ))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        print(dim("Stopped."))
        return 0


def cmd_rules(args: argparse.Namespace) -> int:
    organizer = build_organizer(args, None)
    lookup = organizer.ruleset.known_extensions()
    grouped = {}
    for ext, category in lookup.items():
        grouped.setdefault(category, []).append(ext)
    for category in sorted(grouped):
        extensions = sorted(grouped[category])
        print("%s" % bold(category))
        print("  " + dim(" ".join("." + e for e in extensions)))
    print()
    print("%s  %s" % (bold(organizer.ruleset.screenshot_category),
                      dim("(matched by filename, e.g. Screenshot..., 截屏..., CleanShot...)")))
    print("%s  %s" % (bold(organizer.ruleset.fallback),
                      dim("(anything not listed above)")))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(red("Not a directory: %s" % root), file=sys.stderr)
        return 2

    offenders = find_unportable(root, recursive=not args.no_recursive)
    if not offenders:
        print(green("All names are portable across macOS, Windows and Linux."))
        return 0

    print("%d name%s would cause trouble on another platform:" % (
        len(offenders), "" if len(offenders) == 1 else "s"))
    print()
    for path in offenders:
        proposed = safe_name(path.name)
        print("  %s" % bold(path.name))
        for problem in diagnose(path.name):
            print("      %s %s" % (yellow("!"), problem))
        print("      %s %s" % (dim("->"), green(proposed)))
        print(dim("      in %s" % path.parent))
        print()

    if not args.fix:
        print("Re-run with %s to rename them." % bold("--fix"))
        return 0

    journal = None if args.no_journal else Journal()
    session_id = journal.new_session_id() if journal else None
    renamed = 0
    # Deepest paths first, so renaming a parent folder cannot invalidate the
    # child paths still queued behind it.
    for path in sorted(offenders, key=lambda p: len(p.parts), reverse=True):
        if not path.exists():
            continue
        target = unique_destination(path.parent / safe_name(path.name), set())
        try:
            path.rename(target)
        except OSError as exc:
            print("  %s %s (%s)" % (red("!!"), path.name, exc.strerror or exc))
            continue
        renamed += 1
        if journal and session_id:
            journal.record(session_id, path, target)
    print("  %s %d renamed." % (green("OK"), renamed))
    if journal and session_id and renamed:
        print(dim("  Undo with:  filetidy undo"))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = user_config_path() if args.user else Path.cwd() / CONFIG_FILENAME
    if path.exists() and not args.force:
        print(yellow("Already exists: %s" % path))
        print("Use --force to overwrite.")
        return 1
    write_starter(path)
    print("%s %s" % (green("Wrote"), path))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def cmd_service(args: argparse.Namespace) -> int:
    action = args.action
    name = args.name

    if action == "status":
        outcome = service_mod.status(name)
        mark = green("running") if outcome.ok else yellow(outcome.message)
        print("  %-10s %s" % (bold(name), mark))
        if outcome.path:
            print(dim("  config: %s" % outcome.path))
        print(dim("  log:    %s" % service_mod.log_path(name)))
        return 0 if outcome.ok else 1

    if action == "uninstall":
        outcome = service_mod.uninstall(name)
        print("  %s %s" % (green("OK") if outcome.ok else red("!!"), outcome.message))
        return 0 if outcome.ok else 1

    # install
    paths = service_mod.normalise_paths(args.paths or [])
    if not paths:
        print(red("Give at least one folder to watch."), file=sys.stderr)
        return 2
    missing = [p for p in paths if not Path(p).is_dir()]
    if missing:
        for path in missing:
            print(red("Not a directory: %s" % path), file=sys.stderr)
        return 2

    spec = service_mod.ServiceSpec(
        name=name,
        paths=paths,
        interval=args.interval,
        min_age=args.min_age,
    )
    outcome = service_mod.install(spec)
    if not outcome.ok:
        print("  %s %s" % (red("!!"), outcome.message), file=sys.stderr)
        return 1

    print("%s on %s" % (bold(green("Installed")), service_mod.platform_name()))
    print()
    for path in paths:
        print("  watching  %s" % path)
    print("  every     %d seconds" % spec.interval)
    print("  delay     %d seconds before a new file is filed" % spec.min_age)
    print("  command   %s" % " ".join(service_mod.command_for(spec)))
    if outcome.path:
        print("  config    %s" % outcome.path)
    print("  log       %s" % service_mod.log_path(name))
    print()
    print(dim("  Stop it with:  filetidy service uninstall"))
    return 0


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to a config file")
    parser.add_argument("--archive", help="name of the destination folder (default: _Archive)")
    parser.add_argument("-r", "--recursive", action="store_true", default=None,
                        help="also organise files in sub-folders")
    parser.add_argument("--include-hidden", action="store_true", default=None,
                        help="include dotfiles")
    parser.add_argument("--date-folders", action="store_true", default=None,
                        help="add a YYYY-MM sub-folder inside each category")
    parser.add_argument("--sanitize-names", action="store_true", default=None,
                        help="rewrite names that break on Windows while filing them")
    parser.add_argument("--min-age", type=int, default=None, metavar="SECONDS",
                        help="leave files modified within this window alone (default: 60)")
    parser.add_argument("--exclude", action="append", metavar="GLOB",
                        help="skip names matching this glob (repeatable)")
    parser.add_argument("--no-journal", action="store_true",
                        help="do not record moves (disables undo)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="filetidy",
        description="Sort loose files into folders by type. Works the same on macOS, Windows and Linux.",
    )
    parser.add_argument("--version", action="version", version="filetidy " + __version__)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="organise a folder (dry run unless --apply)")
    run.add_argument("paths", nargs="*", default=["."], metavar="PATH",
                     help="folder(s) to organise (default: .)")
    run.add_argument("--apply", action="store_true", help="actually move the files")
    run.add_argument("-v", "--verbose", action="store_true", help="list every file")
    run.add_argument("-q", "--quiet", action="store_true",
                     help="print only when files were actually moved")
    add_common_options(run)
    run.set_defaults(func=cmd_run)

    undo_parser = subparsers.add_parser("undo", help="put files back where they were")
    undo_parser.add_argument("--session", help="session id to undo (default: the last one)")
    undo_parser.add_argument("--list", action="store_true", help="list recorded sessions")
    undo_parser.set_defaults(func=cmd_undo)

    watch = subparsers.add_parser("watch", help="keep a folder tidy continuously")
    watch.add_argument("path", nargs="?", default=".", help="folder to watch (default: .)")
    watch.add_argument("--interval", type=int, default=30, help="seconds between checks (default: 30)")
    add_common_options(watch)
    watch.set_defaults(func=cmd_watch)

    rules = subparsers.add_parser("rules", help="show which extension goes where")
    rules.add_argument("--config", help="path to a config file")
    rules.set_defaults(func=cmd_rules)

    doctor = subparsers.add_parser(
        "doctor", help="find names that break on another platform")
    doctor.add_argument("path", nargs="?", default=".", help="folder to check (default: .)")
    doctor.add_argument("--fix", action="store_true", help="rename them")
    doctor.add_argument("--no-recursive", action="store_true", help="check only the top level")
    doctor.add_argument("--no-journal", action="store_true", help="do not record renames")
    doctor.set_defaults(func=cmd_doctor)

    service = subparsers.add_parser(
        "service", help="run filetidy automatically in the background")
    service.add_argument("action", choices=["install", "uninstall", "status"])
    service.add_argument("paths", nargs="*", metavar="PATH",
                         help="folder(s) to keep tidy (install only)")
    service.add_argument("--name", default=service_mod.DEFAULT_NAME,
                         help="service name, so several can coexist (default: autotidy)")
    service.add_argument("--interval", type=int, default=service_mod.DEFAULT_INTERVAL,
                         metavar="SECONDS", help="how often to check (default: 300)")
    service.add_argument("--min-age", type=int, default=service_mod.DEFAULT_MIN_AGE,
                         metavar="SECONDS",
                         help="leave a new file alone for this long (default: 600)")
    service.set_defaults(func=cmd_service)

    init = subparsers.add_parser("init", help="write a starter config file")
    init.add_argument("--user", action="store_true", help="write to the per-user config location")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(func=cmd_init)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _prepare_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
