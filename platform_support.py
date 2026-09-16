"""Per-platform details so the command line version runs on Windows and macOS.

Only what really differs between systems lives here: where per-user data is
stored, where Google Chrome is installed, how a browser is detached from the
console, how a user supplied notify command is split, and how sleep is blocked.
The queue, notifier and browser logic stay platform neutral.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

APP_NAME = "CoursePlayer"
NOTIFY_NAME = "codex-feishu-notify"

# Resume/sleep flags for SetThreadExecutionState.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def app_dir() -> Path:
    """Private per-user directory for state, settings and the Chrome profile."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_NAME


def restrict(path: Path, mode: int = 0o700) -> None:
    """Best effort private permissions.

    Windows has no chmod bits; the file inherits the user profile ACL, which is
    already private to the account, so this becomes a no-op there.
    """
    if IS_WINDOWS:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _registered_chrome() -> Path | None:
    """Read Chrome's install path from the Windows registry."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "")
        except OSError:
            continue
        candidate = Path(str(value).strip().strip('"'))
        if candidate.is_file():
            return candidate
    return None


def chrome_candidates() -> list[Path]:
    """Every location that is checked for Google Chrome, in priority order."""
    if IS_WINDOWS:
        candidates = []
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
        registered = _registered_chrome()
        if registered:
            candidates.insert(0, registered)
        return candidates
    if IS_MACOS:
        return [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    return [Path(found) for found in (shutil.which("google-chrome"), shutil.which("chromium"),
                                      shutil.which("chromium-browser")) if found]


def chrome_path() -> Path | None:
    """The installed Chrome binary, or None when Chrome is missing."""
    for candidate in chrome_candidates():
        if candidate.is_file():
            return candidate
    return None


def detached_kwargs() -> dict:
    """Popen options that let Chrome outlive the console that started it."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                "close_fds": True}
    return {"start_new_session": True}


def notify_candidates() -> list[Path]:
    """Local notification helpers that are tried before environment settings."""
    home = Path.home()
    if IS_WINDOWS:
        base = home / ".local" / "bin"
        return [base / (NOTIFY_NAME + extension) for extension in (".cmd", ".bat", ".exe")]
    return [home / ".local" / "bin" / NOTIFY_NAME]


def chrome_background_flags() -> list[str]:
    """Keep the dedicated window working while it stays behind other windows.

    Chrome slows down occluded and background windows. The platform reports
    progress from page scripts, so that throttling could make a finished video
    look unrecorded; the player is driven over CDP and is unaffected either way.
    """
    flags = ["--disable-background-timer-throttling",
             "--disable-backgrounding-occluded-windows",
             "--disable-renderer-backgrounding"]
    if IS_WINDOWS:
        # Windows lowers the priority of fully covered windows through its own
        # occlusion detection, which the flags above do not cover.
        flags.append("--disable-features=CalculateNativeWinOcclusion")
    return flags


def split_command(command: str) -> list[str]:
    """Split a user supplied notify command, tolerating Windows quoting.

    shlex with posix rules eats the backslashes in paths such as
    C:\\Program Files\\tool.exe, so on Windows the quotes are stripped instead.
    """
    if not IS_WINDOWS:
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] and part[0] in "\"'"
            else part for part in parts]


class SleepInhibitor:
    """Keep the machine awake during a run, best effort and easily released."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.claimed = False

    def start(self) -> None:
        if self.process or self.claimed:
            return
        if IS_WINDOWS:
            try:
                import ctypes
                if ctypes.windll.kernel32.SetThreadExecutionState(
                        ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                    self.claimed = True
            except Exception:
                self.claimed = False
        elif IS_MACOS:
            try:
                self.process = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-di", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.process = None

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None
        if self.claimed:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass
            self.claimed = False
