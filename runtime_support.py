"""Windows release integration without changing shared audio device settings."""

import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys

from product import APP_NAME, APP_MUTEX, VERSION


def application_directory():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def portable_mode():
    return (application_directory() / "portable.flag").is_file()


def settings_path():
    if not getattr(sys, "frozen", False) or portable_mode():
        return application_directory() / "deskrec-settings.json"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DeskRec Buddy" / "deskrec-settings.json"


def configure_logging():
    directory = settings_path().parent / "logs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / "deskrec.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler],
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
        logging.info("%s %s started (portable=%s)", APP_NAME, VERSION, portable_mode())
    except OSError:
        logging.getLogger().addHandler(logging.NullHandler())


def create_desktop_shortcut(executable=None, desktop=None, portable=None):
    """Use Windows' actual Desktop location, including OneDrive redirection."""
    executable = Path(executable or sys.executable).resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    if portable is None:
        portable = portable_mode()
    name = APP_NAME + (" (portable)" if portable else "") + ".lnk"
    payload = {"executable": str(executable), "name": name, "desktop": str(desktop) if desktop else None}
    script = """$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
$desktop = if ($p.desktop) { $p.desktop } else { $shell.SpecialFolders.Item('Desktop') }
$link = Join-Path $desktop $p.name
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $p.executable
$shortcut.WorkingDirectory = Split-Path -LiteralPath $p.executable
$shortcut.IconLocation = $p.executable + ',0'
$shortcut.Description = 'DeskRec Buddy - screen and audio recorder'
$shortcut.Save()
[Console]::Out.Write($link)
"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    if result.returncode:
        raise RuntimeError("Не удалось создать ярлык: " + result.stderr[-800:])
    # Windows PowerShell's stdout encoding may vary; do not use it as a path.
    return name


class InstanceGuard:
    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateMutexW(None, False, APP_MUTEX)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
