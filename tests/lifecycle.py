"""Closing during preparation must finish workers and save a playable recording."""

from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import DeskRecApp, make_dpi_aware
from recorder import CaptureSession, ffmpeg_path
from ui_probe import pump_until


def main():
    make_dpi_aware()
    destination = ROOT / ".smoke" / f"lifecycle-{time.time_ns()}"
    destination.mkdir(parents=True)
    errors = []
    gate = threading.Event()
    real_start = CaptureSession.start
    def delayed_start(session):
        assert gate.wait(10)
        return real_start(session)
    with patch("app.SETTINGS_PATH", destination / "settings.json"), patch("app.messagebox.showerror", side_effect=lambda *args: errors.append(args)), patch("app.messagebox.showinfo"), patch.object(CaptureSession, "start", delayed_start):
        application = DeskRecApp()
        session = None
        try:
            application.update()
            pump_until(application, lambda: not application.recovering)
            application.folder_var.set(str(destination))
            application.set_region((100, 100, 740, 460))
            application.quality_var.set("Экономно")
            application.start_recording()
            pump_until(application, lambda: application.session is not None)
            session = application.session
            assert application.starting
            application.on_close()
            assert application.winfo_exists(), "Window closed while a capture worker was starting"
            assert application.close_after_save
            gate.set()
            pump_until(application, lambda: session.final_path is not None or bool(errors), timeout=45)
            assert not errors, errors
            assert session.final_path.is_file()
            assert all(not worker.is_alive() for worker in session.threads)
            assert session.encoder.poll() == 0
            result = subprocess.run([ffmpeg_path(), "-v", "error", "-xerror", "-i", str(session.final_path), "-f", "null", "NUL"], capture_output=True)
            assert result.returncode == 0, result.stderr
            print(f"PASS: close during preparation saves MP4 and ends all workers: {session.final_path}")
        finally:
            gate.set()
            if session:
                session.cancel()
            try:
                application.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
