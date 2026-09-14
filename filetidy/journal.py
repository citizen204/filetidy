"""Move journal: every applied move is recorded so it can be undone."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Iterator, List, Optional

JOURNAL_VERSION = 1


def default_journal_dir() -> Path:
    """Per-user state directory, following each platform's convention."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "filetidy"
        return Path.home() / "AppData" / "Local" / "filetidy"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "filetidy"
    return Path.home() / ".local" / "state" / "filetidy"


class Journal:
    """Append-only JSONL log of applied moves, grouped into sessions."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory else default_journal_dir()
        self.path = self.directory / "journal.jsonl"

    # -- writing ---------------------------------------------------------
    def new_session_id(self) -> str:
        return "%s-%s" % (time.strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:6])

    def record(self, session_id: str, source: Path, destination: Path) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {
            "v": JOURNAL_VERSION,
            "session": session_id,
            "ts": time.time(),
            "from": str(source),
            "to": str(destination),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- reading ---------------------------------------------------------
    def entries(self) -> Iterator[Dict]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A truncated final line (power loss mid-write) should not
                    # make the whole history unreadable.
                    continue

    def sessions(self) -> List[Dict]:
        """Summarise sessions, newest last."""
        grouped: Dict[str, Dict] = {}
        for entry in self.entries():
            sid = entry.get("session")
            if not sid:
                continue
            summary = grouped.setdefault(
                sid, {"session": sid, "count": 0, "ts": entry.get("ts", 0)}
            )
            summary["count"] += 1
            summary["ts"] = max(summary["ts"], entry.get("ts", 0))
        return sorted(grouped.values(), key=lambda item: item["ts"])

    def last_session_id(self) -> Optional[str]:
        sessions = self.sessions()
        return sessions[-1]["session"] if sessions else None

    def moves_for(self, session_id: str) -> List[Dict]:
        return [e for e in self.entries() if e.get("session") == session_id]

    def forget(self, session_id: str) -> None:
        """Drop a session from the journal (after a successful undo)."""
        if not self.path.exists():
            return
        kept = [e for e in self.entries() if e.get("session") != session_id]
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for entry in kept:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(str(tmp), str(self.path))
