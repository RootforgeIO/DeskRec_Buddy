"""Extract the public ZIP to a Unicode path and run without development paths."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from product import APP_NAME, VERSION
from recorder import ffmpeg_path
from mp4_timing import probe


def main():
    archive = ROOT / "release" / f"DeskRec-Buddy-{VERSION}-portable.zip"
    destination = ROOT / ".smoke" / f"Портабл O'Brien {time.time_ns()}"
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        names = package.namelist()
        assert all(name.startswith(APP_NAME + "/") and ".." not in Path(name).parts for name in names)
        allowed = {APP_NAME + ".exe", "portable.flag", "README.md", "THIRD_PARTY.md", "CHANGELOG.md", "_internal", "LICENSES", "assets"}
        assert all(Path(name).parts[1] in allowed for name in names), "Unexpected development files in the public package"
        assert not any("deskrec-settings.json" in name or "/logs/" in name or ".deskrec-" in name for name in names)
        assert any("/LICENSES/" in name for name in names)
        package.extractall(destination)
    directory = destination / APP_NAME
    executable = directory / (APP_NAME + ".exe")
    assert (directory / "portable.flag").is_file()
    assert hashlib.sha256(executable.read_bytes()).digest() == hashlib.sha256((ROOT / "dist" / APP_NAME / executable.name).read_bytes()).digest()
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    output = destination / "Записи"
    result = subprocess.run([str(executable), "--smoke", str(output), "--seconds", "3", "--keep-name"],
                            env=environment, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=45)
    assert result.returncode == 0, result.stderr
    video, = output.glob("*.mp4")
    assert (directory / "logs" / "deskrec.log").is_file(), "Portable app must log next to its own EXE"
    result = subprocess.run([ffmpeg_path(), "-v", "error", "-xerror", "-i", str(video), "-f", "null", "NUL"], capture_output=True)
    assert result.returncode == 0, result.stderr
    tracks = probe(video)
    assert "vide" in tracks and "soun" in tracks
    print(f"PASS: ZIP CRC, clean public files, Unicode/apostrophe extraction path, independent EXE capture and A/V decode: {destination}")


if __name__ == "__main__":
    main()
