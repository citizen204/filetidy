"""Planning and applying the file moves."""
from __future__ import annotations

import fnmatch
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, List, NamedTuple, Optional, Sequence

from .journal import Journal
from .names import is_portable, safe_name
from .rules import Ruleset, is_incomplete, split_extension


class Move(NamedTuple):
    source: Path
    destination: Path
    category: str


class Skip(NamedTuple):
    source: Path
    reason: str


class Plan(NamedTuple):
    moves: List[Move]
    skips: List[Skip]

    @property
    def total_bytes(self) -> int:
        total = 0
        for move in self.moves:
            try:
                total += move.source.stat().st_size
            except OSError:
                pass
        return total


class Result(NamedTuple):
    moved: List[Move]
    failed: List[Skip]
    session_id: Optional[str]


DEFAULT_EXCLUDES = (
    ".DS_Store", "Thumbs.db", "desktop.ini", ".localized", "Icon\r",
)


class Organizer:
    """Turns a directory of loose files into categorised folders."""

    def __init__(
        self,
        ruleset: Optional[Ruleset] = None,
        archive_name: str = "_Archive",
        recursive: bool = False,
        include_hidden: bool = False,
        date_subfolders: bool = False,
        date_format: str = "%Y-%m",
        min_age_seconds: int = 60,
        excludes: Sequence[str] = (),
        sanitize_names: bool = False,
        follow_symlinks: bool = False,
    ) -> None:
        self.ruleset = ruleset or Ruleset()
        self.archive_name = archive_name
        self.recursive = recursive
        self.include_hidden = include_hidden
        self.date_subfolders = date_subfolders
        self.date_format = date_format
        self.min_age_seconds = max(0, min_age_seconds)
        self.excludes = tuple(DEFAULT_EXCLUDES) + tuple(excludes)
        self.sanitize_names = sanitize_names
        self.follow_symlinks = follow_symlinks

    # -- planning --------------------------------------------------------
    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.excludes)

    def _iter_candidates(self, root: Path, archive_root: Path) -> Iterable[Path]:
        if self.recursive:
            for dirpath, dirnames, filenames in os.walk(str(root)):
                current = Path(dirpath)
                # Never descend into the archive we are filling, and skip
                # hidden / package directories unless explicitly asked.
                dirnames[:] = [
                    d for d in dirnames
                    if (current / d) != archive_root
                    and (self.include_hidden or not d.startswith("."))
                    and not d.endswith((".app", ".bundle", ".framework"))
                ]
                for filename in filenames:
                    yield current / filename
        else:
            try:
                entries = sorted(root.iterdir())
            except OSError:
                return
            for entry in entries:
                if entry.is_file() or entry.is_symlink():
                    yield entry

    def plan(self, root: Path) -> Plan:
        root = Path(root).expanduser().resolve()
        archive_root = root / self.archive_name
        moves: List[Move] = []
        skips: List[Skip] = []
        now = time.time()
        # Destinations claimed by this plan, so two files that would land on
        # the same name do not silently collide before anything is applied.
        claimed = set()

        for path in self._iter_candidates(root, archive_root):
            name = path.name

            if archive_root in path.parents or path == archive_root:
                continue
            if path.is_dir():
                continue
            if path.is_symlink() and not self.follow_symlinks:
                skips.append(Skip(path, "symlink"))
                continue
            if not self.include_hidden and name.startswith("."):
                continue
            if self._is_excluded(name):
                continue
            if is_incomplete(name):
                skips.append(Skip(path, "download in progress"))
                continue

            try:
                stat = path.stat()
            except OSError as exc:
                skips.append(Skip(path, "unreadable (%s)" % exc.strerror))
                continue

            if self.min_age_seconds and (now - stat.st_mtime) < self.min_age_seconds:
                skips.append(Skip(path, "modified less than %ds ago" % self.min_age_seconds))
                continue

            category = self.ruleset.category_for(name)
            target_dir = archive_root / category
            if self.date_subfolders:
                stamp = time.strftime(self.date_format, time.localtime(stat.st_mtime))
                target_dir = target_dir / stamp

            final_name = safe_name(name) if self.sanitize_names else name
            destination = unique_destination(target_dir / final_name, claimed)
            claimed.add(_key(destination))
            moves.append(Move(path, destination, category))

        return Plan(moves, skips)

    # -- applying --------------------------------------------------------
    def apply(
        self,
        plan: Plan,
        journal: Optional[Journal] = None,
        on_move: Optional[Callable[[Move], None]] = None,
    ) -> Result:
        moved: List[Move] = []
        failed: List[Skip] = []
        session_id = journal.new_session_id() if journal else None

        for move in plan.moves:
            try:
                move.destination.parent.mkdir(parents=True, exist_ok=True)
                # Re-check at apply time: another process may have created the
                # destination since the plan was built.
                destination = unique_destination(move.destination, set())
                shutil.move(str(move.source), str(destination))
            except OSError as exc:
                failed.append(Skip(move.source, exc.strerror or str(exc)))
                continue

            applied = Move(move.source, destination, move.category)
            moved.append(applied)
            if journal and session_id:
                journal.record(session_id, applied.source, applied.destination)
            if on_move:
                on_move(applied)

        return Result(moved, failed, session_id)


def _key(path: Path) -> str:
    """Case-insensitive key, because macOS and Windows filesystems usually are."""
    return os.path.normcase(str(path))


def unique_destination(destination: Path, claimed: set) -> Path:
    """Return a path that collides with neither disk nor ``claimed``."""
    if not destination.exists() and _key(destination) not in claimed:
        return destination
    stem, ext = split_extension(destination.name)
    parent = destination.parent
    counter = 2
    while True:
        candidate = parent / ("%s (%d)%s" % (stem, counter, ext))
        if not candidate.exists() and _key(candidate) not in claimed:
            return candidate
        counter += 1


def undo(moves: Sequence[dict]) -> Result:
    """Reverse a recorded set of moves, newest first."""
    restored: List[Move] = []
    failed: List[Skip] = []
    vacated: List[Path] = []
    for entry in reversed(list(moves)):
        source = Path(entry["to"])       # where the file is now
        destination = Path(entry["from"])  # where it came from
        if not source.exists():
            failed.append(Skip(source, "no longer at recorded location"))
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            final = unique_destination(destination, set())
            shutil.move(str(source), str(final))
            restored.append(Move(source, final, ""))
            vacated.append(source.parent)
        except OSError as exc:
            failed.append(Skip(source, exc.strerror or str(exc)))

    # Leaving a tree of empty category folders behind would make an undo feel
    # only half-done, so prune what this undo emptied (deepest first).
    for directory in sorted(set(vacated), key=lambda p: len(p.parts), reverse=True):
        prune_empty(directory)
    return Result(restored, failed, None)


def prune_empty(directory: Path, stop_after: int = 4) -> None:
    """Remove ``directory`` and empty parents, at most ``stop_after`` levels."""
    current = directory
    for _ in range(max(1, stop_after)):
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def find_unportable(root: Path, recursive: bool = True) -> List[Path]:
    """List files and folders whose names are not portable across platforms."""
    root = Path(root).expanduser().resolve()
    offenders: List[Path] = []
    if recursive:
        walker = os.walk(str(root))
    else:
        walker = [(str(root), [], [p.name for p in root.iterdir()])]
    for dirpath, dirnames, filenames in walker:
        for name in list(dirnames) + list(filenames):
            if name.startswith("."):
                continue
            if not is_portable(name):
                offenders.append(Path(dirpath) / name)
    return offenders
