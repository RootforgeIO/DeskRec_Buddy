"""Saving must succeed when a player/Explorer holds the live MP4 open."""

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))
from recorder import CaptureOptions, CaptureSession, ffmpeg_path, recover_pending_recordings
import soundcard as sc

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


@contextmanager
def hold_live_file(path):
    handle = kernel32.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


def create_session(label):
    directory = ROOT / ".smoke" / f"save-{label}-{time.time_ns()}"
    directory.mkdir(parents=True)
    session = CaptureSession(CaptureOptions((100, 100, 740, 460), "Экономно", directory,
                                            sc.default_speaker().id, sc.default_microphone().id))
    session.start()
    time.sleep(2.5)
    return session


def decode(path):
    result = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(path), "-f", "null", "NUL"], capture_output=True)
    assert result.returncode == 0, result.stderr


def main():
    if len(sys.argv) > 1:
        portable_test(Path(sys.argv[1]))
        return
    session = create_session("locked")
    existing = session.output_path.with_name(session.output_path.stem + "_saved.mp4")
    existing.write_bytes(b"existing file must not be overwritten")
    with hold_live_file(session.output_path):
        saved = session.stop_and_save()
        assert saved != session.output_path and saved.exists(), "Locked live file must not block publishing"
        assert saved.stem.endswith("_saved-2")
        assert existing.read_bytes() == b"existing file must not be overwritten"
        assert session.final_path == saved
        decode(saved)
        print(f"PASS: saved while the live MP4 was locked: {saved}")
    session = create_session("cleanup")
    with patch("recorder.shutil.rmtree", side_effect=PermissionError("simulated journal lock")):
        saved = session.stop_and_save()
    decode(saved)
    assert session.folder.exists()
    state = json.loads((session.folder / "state.json").read_text(encoding="utf-8"))
    assert state.get("status") == "saved", state
    before = saved.stat().st_mtime_ns
    recovered, errors = recover_pending_recordings(saved.parent)
    assert not recovered and not errors, (recovered, errors)
    assert saved.stat().st_mtime_ns == before, "Finished file must not be reprocessed"
    assert not session.folder.exists()
    print("PASS: journal cleanup failure does not turn a saved recording into an error")


def portable_test(executable):
    directory = ROOT / ".smoke" / f"save-portable-{time.time_ns()}"
    directory.mkdir(parents=True)
    with (directory / "test.log").open("wb") as log:
        process = subprocess.Popen([str(executable), "--smoke", str(directory), "--seconds", "5", "--keep-name"],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 15
        while True:
            files = list(directory.glob("DeskRec_*.mp4"))
            if files and files[0].read_bytes().count(b"moof") >= 1:
                break
            assert process.poll() is None and time.monotonic() < deadline, "Portable did not start recording"
            time.sleep(0.05)
        with hold_live_file(files[0]):
            assert process.wait(timeout=20) == 0, "Portable returned a save error"
            saved, = directory.glob("DeskRec_*_saved*.mp4")
            decode(saved)
            assert not list(directory.glob(".deskrec-*"))
            print(f"PASS: portable EXE saved with a player-style file lock: {saved}")
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
