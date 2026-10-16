"""Manual hardware smoke test: records two seconds and inspects the MP4."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from recorder import CaptureOptions, CaptureSession
import soundcard as sc


def main() -> None:
    output_dir = ROOT / ".smoke"
    output_dir.mkdir(exist_ok=True)
    options = CaptureOptions((100, 100, 740, 460), "Экономно", output_dir,
                             sc.default_speaker().id, sc.default_microphone().id)
    session = CaptureSession(options)
    session.start()
    time.sleep(2.5)
    output = session.stop_and_save(output_dir / f"smoke-{int(time.time())}.mp4")
    assert output.stat().st_size > 10_000, "MP4 is unexpectedly small"
    print(f"Recorded {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
