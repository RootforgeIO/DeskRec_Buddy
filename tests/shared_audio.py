"""All quality profiles must record while a second client uses the microphone."""

import ctypes
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))
from recorder import CaptureSession, CaptureOptions, QUALITY, ffmpeg_path
import soundcard as sc


def main():
    destination = ROOT / ".smoke" / f"shared-audio-{time.time_ns()}"
    destination.mkdir(parents=True)
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    try:
        mic = sc.default_microphone()
        with mic.recorder(samplerate=48000, channels=2, blocksize=24000) as other_client:
            for quality in QUALITY:
                session = CaptureSession(CaptureOptions((100, 100, 740, 460), quality, destination, sc.default_speaker().id, mic.id))
                try:
                    session.start()
                    block = other_client.record(numframes=960)
                    assert block.shape == (960, 2), "A second microphone client cannot capture"
                    time.sleep(2)
                    output = session.stop_and_save()
                    assert not session.errors, session.errors
                    result = subprocess.run([ffmpeg_path(), "-v", "error", "-xerror", "-i", str(output), "-f", "null", "NUL"], capture_output=True)
                    assert result.returncode == 0, result.stderr
                    print(f"PASS: {quality}, simultaneous second microphone client and playable MP4", flush=True)
                finally:
                    session.cancel()
    finally:
        ctypes.windll.ole32.CoUninitialize()


if __name__ == "__main__":
    main()
