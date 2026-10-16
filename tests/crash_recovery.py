"""Kill our own recording process/tree and verify a single mixed MP4 survives."""

import json
from pathlib import Path
import re
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))
from recorder import ffmpeg_path, recover_pending_recordings
from windows_support import process_alive


def wait_for(condition, timeout=25):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("Recording did not reach the required state")
        time.sleep(0.1)


def decode(video, strict):
    result = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(video), "-f", "null", "NUL"],
                            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    assert "Video: h264" in result.stderr and "Audio: aac" in result.stderr, result.stderr
    frames = re.findall(r"frame=\s*(\d+)", result.stderr)
    assert frames and int(frames[-1]) >= 48, result.stderr
    if strict:
        assert result.returncode == 0, result.stderr
    return int(frames[-1])


def main():
    portable = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    for kill_tree in (False, True):
        destination = ROOT / ".smoke" / f"crash-{'tree' if kill_tree else 'parent'}-{time.time_ns()}"
        destination.mkdir(parents=True)
        command = [str(portable)] if portable else [sys.executable, str(ROOT / "app.py")]
        command += ["--smoke", str(destination), "--seconds", "60"]
        with (destination / "test.log").open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            def ready():
                files = list(destination.glob("DeskRec_*.mp4"))
                return bool(files and files[0].read_bytes().count(b"moof") >= 4)
            wait_for(ready)
            state_file, = destination.glob(".deskrec-*/state.json")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            video = destination / state["output"]
            assert process.poll() is None
            if kill_tree:
                result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                assert result.returncode == 0, result.stderr
            else:
                process.kill()
            process.wait(timeout=10)
            wait_for(lambda: not process_alive(state["encoder_pid"]))
            before = decode(video, strict=not kill_tree)
            if kill_tree:
                from app import DeskRecApp, make_dpi_aware
                settings = destination / "test-settings.json"
                settings.write_text(json.dumps({"folder": str(destination)}), encoding="utf-8")
                with patch("app.SETTINGS_PATH", settings):
                    make_dpi_aware()
                    application = DeskRecApp()
                    try:
                        application.update()
                        def recovered_on_startup():
                            application.update()
                            return not application.recovering
                        wait_for(recovered_on_startup)
                        assert "Восстановлена запись" in application.status_var.get(), application.status_var.get()
                    finally:
                        application.destroy()
            else:
                recovered, errors = recover_pending_recordings(destination)
                assert not errors, errors
                assert recovered == [video], recovered
            after = decode(video, strict=True)
            assert after >= before - 2, (before, after)
            assert not list(destination.glob("*.m4a")), "Recording must already contain mixed audio"
            print(f"PASS {'process tree' if kill_tree else 'parent process'} kill: {before} frames before recovery, {after} after; {video}", flush=True)
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
