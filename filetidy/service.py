"""Register filetidy to run automatically, using whatever scheduler the OS has.

macOS gets a launchd user agent, Windows a Scheduled Task, Linux a systemd
user timer. All three run the same one-shot command on an interval rather than
keeping a process resident, so a crash cannot leave the folder unattended --
the next tick simply runs again.

The functions that *build* a scheduler's configuration are pure and take no
side effects, so they can be tested on any platform.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

DEFAULT_NAME = "autotidy"
LABEL_PREFIX = "com.filetidy."
DEFAULT_INTERVAL = 300      # seconds between runs
DEFAULT_MIN_AGE = 600       # leave a file alone for its first 10 minutes


class ServiceSpec(NamedTuple):
    """Everything the scheduler needs to know, platform-independent."""
    name: str
    paths: List[str]
    interval: int
    min_age: int

    @property
    def label(self) -> str:
        return LABEL_PREFIX + self.name


class Outcome(NamedTuple):
    ok: bool
    message: str
    path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def launcher() -> List[str]:
    """How to invoke filetidy from a scheduler.

    The installation doing the registering is the one that gets registered.
    Resolving through PATH instead would pick whichever filetidy happens to
    come first, so installing from a dedicated virtualenv -- the reason to
    have one at all on macOS, where the interpreter is what gets granted disk
    access -- would silently schedule a different interpreter than the one the
    user then grants.
    """
    invoked = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if invoked and invoked.name in ("filetidy", "filetidy.exe") and invoked.exists():
        return [str(invoked.resolve())]

    script = shutil.which("filetidy")
    if script:
        return [str(Path(script).resolve())]

    # Running as `python -m filetidy`: keep using this interpreter.
    return [str(Path(sys.executable).resolve()), "-m", "filetidy"]


def command_for(spec: ServiceSpec) -> List[str]:
    argv = launcher() + ["run"]
    argv.extend(spec.paths)
    argv.extend(["--apply", "--quiet", "--min-age", str(spec.min_age)])
    return argv


def log_path(name: str) -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "filetidy" / ("%s.log" % name)
    return Path.home() / "Library" / "Logs" / "filetidy" / ("%s.log" % name) \
        if sys.platform == "darwin" \
        else Path.home() / ".local" / "state" / "filetidy" / ("%s.log" % name)


def normalise_paths(paths: Sequence[str]) -> List[str]:
    """Absolute, expanded, de-duplicated -- schedulers do not expand ``~``."""
    seen = []
    for raw in paths:
        resolved = str(Path(raw).expanduser().resolve())
        if resolved not in seen:
            seen.append(resolved)
    return seen


# ---------------------------------------------------------------------------
# macOS -- launchd
# ---------------------------------------------------------------------------

def launchd_plist_path(name: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / (LABEL_PREFIX + name + ".plist")


def build_launchd_plist(spec: ServiceSpec) -> dict:
    log = log_path(spec.name)
    return {
        "Label": spec.label,
        "ProgramArguments": command_for(spec),
        "RunAtLoad": True,
        "StartInterval": spec.interval,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        # Keep it out of the way of whatever the user is actually doing.
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 5,
    }


def _launchctl(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl"] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def install_launchd(spec: ServiceSpec) -> Outcome:
    plist_file = launchd_plist_path(spec.name)
    plist_file.parent.mkdir(parents=True, exist_ok=True)
    log_path(spec.name).parent.mkdir(parents=True, exist_ok=True)

    # Remove any previous copy first, or launchd refuses the new one as a
    # duplicate label and silently keeps running the stale command.
    uninstall_launchd(spec.name)

    with plist_file.open("wb") as handle:
        plistlib.dump(build_launchd_plist(spec), handle)

    domain = "gui/%d" % os.getuid()
    result = _launchctl(["bootstrap", domain, str(plist_file)])
    if result.returncode != 0:
        # bootstrap is unavailable before macOS 10.11.
        result = _launchctl(["load", "-w", str(plist_file)])
    if result.returncode != 0:
        return Outcome(False, result.stdout.strip() or "launchctl failed", plist_file)
    return Outcome(True, "launchd agent loaded", plist_file)


def uninstall_launchd(name: str) -> Outcome:
    plist_file = launchd_plist_path(name)
    domain = "gui/%d" % os.getuid()
    _launchctl(["bootout", "%s/%s%s" % (domain, LABEL_PREFIX, name)])
    _launchctl(["unload", "-w", str(plist_file)])
    existed = plist_file.exists()
    if existed:
        plist_file.unlink()
    return Outcome(True, "removed" if existed else "was not installed", plist_file)


def status_launchd(name: str) -> Outcome:
    plist_file = launchd_plist_path(name)
    if not plist_file.exists():
        return Outcome(False, "not installed", plist_file)
    result = _launchctl(["list", LABEL_PREFIX + name])
    if result.returncode != 0:
        return Outcome(False, "installed but not loaded", plist_file)
    return Outcome(True, "loaded and scheduled", plist_file)


# ---------------------------------------------------------------------------
# Windows -- Scheduled Tasks
# ---------------------------------------------------------------------------

def windows_task_name(name: str) -> str:
    return "filetidy_" + name


def build_schtasks_command(spec: ServiceSpec) -> List[str]:
    """The schtasks invocation that registers the task.

    /sc minute keeps running after a reboot and starts again at logon, which
    is what "run automatically" means in practice -- an onlogon-only trigger
    would stop covering a machine that stays on for days.
    """
    quoted = subprocess.list2cmdline(command_for(spec))
    minutes = max(1, spec.interval // 60)
    return [
        "schtasks", "/create",
        "/tn", windows_task_name(spec.name),
        "/tr", quoted,
        "/sc", "minute",
        "/mo", str(minutes),
        "/f",
    ]


def install_windows(spec: ServiceSpec) -> Outcome:
    log_path(spec.name).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        build_schtasks_command(spec),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        return Outcome(False, result.stdout.strip() or "schtasks failed")
    return Outcome(True, "scheduled task created")


def uninstall_windows(name: str) -> Outcome:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", windows_task_name(name), "/f"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        return Outcome(True, "was not installed")
    return Outcome(True, "removed")


def status_windows(name: str) -> Outcome:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", windows_task_name(name)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        return Outcome(False, "not installed")
    return Outcome(True, "scheduled")


# ---------------------------------------------------------------------------
# Linux -- systemd user units
# ---------------------------------------------------------------------------

def systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def build_systemd_units(spec: ServiceSpec):
    command = " ".join('"%s"' % part if " " in part else part for part in command_for(spec))
    service = (
        "[Unit]\n"
        "Description=filetidy -- file away loose files\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=%s\n" % command
    )
    timer = (
        "[Unit]\n"
        "Description=filetidy timer\n\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        "OnUnitActiveSec=%ds\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n" % spec.interval
    )
    return service, timer


def install_systemd(spec: ServiceSpec) -> Outcome:
    directory = systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    service, timer = build_systemd_units(spec)
    service_file = directory / ("filetidy-%s.service" % spec.name)
    timer_file = directory / ("filetidy-%s.timer" % spec.name)
    service_file.write_text(service, encoding="utf-8")
    timer_file.write_text(timer, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", timer_file.name],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        return Outcome(False, result.stdout.strip() or "systemctl failed", timer_file)
    return Outcome(True, "systemd timer enabled", timer_file)


def uninstall_systemd(name: str) -> Outcome:
    directory = systemd_dir()
    timer_file = directory / ("filetidy-%s.timer" % name)
    service_file = directory / ("filetidy-%s.service" % name)
    subprocess.run(["systemctl", "--user", "disable", "--now", timer_file.name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    existed = timer_file.exists() or service_file.exists()
    for path in (timer_file, service_file):
        if path.exists():
            path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return Outcome(True, "removed" if existed else "was not installed", timer_file)


def status_systemd(name: str) -> Outcome:
    timer_file = systemd_dir() / ("filetidy-%s.timer" % name)
    if not timer_file.exists():
        return Outcome(False, "not installed", timer_file)
    result = subprocess.run(
        ["systemctl", "--user", "is-active", timer_file.name],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    state = result.stdout.strip() or "unknown"
    return Outcome(state == "active", state, timer_file)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "linux"


def install(spec: ServiceSpec) -> Outcome:
    return {
        "macos": install_launchd,
        "windows": install_windows,
        "linux": install_systemd,
    }[platform_name()](spec)


def uninstall(name: str) -> Outcome:
    return {
        "macos": uninstall_launchd,
        "windows": uninstall_windows,
        "linux": uninstall_systemd,
    }[platform_name()](name)


def status(name: str) -> Outcome:
    return {
        "macos": status_launchd,
        "windows": status_windows,
        "linux": status_systemd,
    }[platform_name()](name)
