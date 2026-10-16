"""A failed microphone must not leave a capture worker or FFmpeg running."""

from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import DeskRecApp, make_dpi_aware
from recorder import CaptureSession
import soundcard as sc
from ui_probe import pump_until


def main():
    make_dpi_aware()
    destination = ROOT / ".smoke" / f"startup-failure-{time.time_ns()}"
    destination.mkdir(parents=True)
    sessions, errors = [], []
    real_get = sc.get_microphone
    def unavailable(device, **kwargs):
        if not kwargs.get("include_loopback"):
            raise RuntimeError("Test microphone disconnected")
        return real_get(device, **kwargs)
    def create_session(options):
        session = CaptureSession(options)
        sessions.append(session)
        return session
    with patch("app.SETTINGS_PATH", destination / "settings.json"), patch("app.CaptureSession", side_effect=create_session), patch("recorder.sc.get_microphone", side_effect=unavailable), patch("app.messagebox.showerror", side_effect=lambda *args: errors.append(args)):
        application = DeskRecApp()
        try:
            application.update()
            pump_until(application, lambda: not application.recovering)
            application.folder_var.set(str(destination))
            application.set_region((100, 100, 740, 460))
            application.start_recording()
            pump_until(application, lambda: bool(errors), timeout=45)
            assert not application.starting and application.session is None
            session, = sessions
            assert all(not worker.is_alive() for worker in session.threads)
            assert session.encoder is None or session.encoder.poll() is not None
            assert application.start_button.instate(["!disabled"])
            print("PASS: disconnected microphone reports error, resets UI and ends all capture processes")
        finally:
            for session in sessions:
                session.cancel()
            application.destroy()


if __name__ == "__main__":
    main()
