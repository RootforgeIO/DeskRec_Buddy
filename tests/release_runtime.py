"""Release integration: settings modes, real .lnk, mutex and UI error paths."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_support import create_desktop_shortcut, ensure_first_launch_shortcut, InstanceGuard, settings_path
from app import DeskRecApp, make_dpi_aware
from ui_probe import pump_until


def main():
    make_dpi_aware()
    with tempfile.TemporaryDirectory(dir=ROOT / ".smoke", prefix="runtime-") as directory:
        directory = Path(directory)
        with patch("runtime_support.sys.frozen", True, create=True), patch("runtime_support.application_directory", return_value=directory), patch.dict(os.environ, LOCALAPPDATA=str(directory / "Local")):
            assert settings_path() == directory / "Local" / "DeskRec Buddy" / "deskrec-settings.json"
            (directory / "portable.flag").touch()
            assert settings_path() == directory / "deskrec-settings.json"
        # A running user copy must not prevent an isolated mutex regression.
        with patch("runtime_support.APP_MUTEX", rf"Local\DeskRecBuddy.Test.{time.time_ns()}"):
            first, second = InstanceGuard(), InstanceGuard()
            try:
                assert first.acquired and not second.acquired
            finally:
                second.close()
                first.close()
            third = InstanceGuard()
            assert third.acquired
            third.close()
        # Apostrophes, Cyrillic and spaces must survive the real Shell COM API.
        desktop = directory / "Рабочий стол O'Brien"
        desktop.mkdir()
        target = desktop / "DeskRec Buddy O'Brien.exe"
        target.write_bytes(b"shortcut target only")
        name = create_desktop_shortcut(target, desktop, portable=True)
        link = desktop / name
        assert link.is_file() and link.stat().st_size > 100
        payload = json.dumps({"link": str(link), "target": str(target)})
        script = "$p=[Console]::In.ReadToEnd() | ConvertFrom-Json; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p.link); if($s.TargetPath -ne $p.target -or $s.WorkingDirectory -ne (Split-Path -LiteralPath $p.target)){exit 1}"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], input=payload, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        with patch("runtime_support.sys.frozen", True, create=True), \
                patch("runtime_support.sys.executable", str(target)), \
                patch("runtime_support.application_directory", return_value=directory):
            assert ensure_first_launch_shortcut(desktop)
            marker = directory / "desktop-shortcut.json"
            assert json.loads(marker.read_text(encoding="utf-8"))["executable"] == str(target.resolve())
            link.unlink()
            assert not ensure_first_launch_shortcut(desktop)
            assert not link.exists(), "A user-deleted shortcut must not reappear at every launch"
            marker.unlink()
            with patch("runtime_support.create_desktop_shortcut", side_effect=OSError("Desktop unavailable")):
                try:
                    ensure_first_launch_shortcut(desktop)
                except OSError:
                    pass
                else:
                    raise AssertionError("Shortcut creation unexpectedly succeeded")
                assert not marker.exists(), "A failed first launch must remain retryable"
            assert ensure_first_launch_shortcut(desktop)
            # Installed mode uses LocalAppData and a distinct, unambiguous link name.
            (directory / "portable.flag").unlink()
            with patch.dict(os.environ, LOCALAPPDATA=str(directory / "Local")):
                assert ensure_first_launch_shortcut(desktop)
                assert (desktop / "DeskRec Buddy.lnk").is_file()
                assert not ensure_first_launch_shortcut(desktop)
        with patch("app.SETTINGS_PATH", directory / "broken-settings.json"), patch("app.devices", side_effect=RuntimeError("Device unavailable")), patch("app.messagebox.showerror"):
            (directory / "broken-settings.json").write_text('{"microphone_gain": "9999%"}', encoding="utf-8")
            application = DeskRecApp()
            try:
                application.update()
                pump_until(application, lambda: not application.recovering)
                assert application.microphone_gain_var.get() == "200%"
                assert not application.speaker_names and not application.microphone_names
                application.start_recording()
                assert not application.starting and application.picker is not None
            finally:
                if application.picker:
                    application.picker.window.destroy()
                application.destroy()
    print("PASS: installed/portable first-launch shortcuts, no recreation after deletion, retry after error, single instance, Unicode paths and device errors")


if __name__ == "__main__":
    main()
