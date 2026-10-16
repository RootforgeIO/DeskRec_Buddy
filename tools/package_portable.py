"""Package only public app files; never include personal settings or test logs."""

from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product import APP_NAME, VERSION


def main():
    directory = ROOT / "dist" / APP_NAME
    files = [directory / name for name in (APP_NAME + ".exe", "portable.flag", "Create Desktop Shortcut.cmd", "README.md", "THIRD_PARTY.md", "CHANGELOG.md", "assets/deskrec-buddy.svg")]
    files += sorted(file for file in (directory / "_internal").rglob("*") if file.is_file())
    files += sorted(file for file in (directory / "LICENSES").rglob("*") if file.is_file())
    archive = ROOT / "release" / f"DeskRec-Buddy-{VERSION}-portable.zip"
    archive.parent.mkdir(exist_ok=True)
    temporary = archive.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for file in files:
            package.write(file, (Path(APP_NAME) / file.relative_to(directory)).as_posix())
    with zipfile.ZipFile(temporary) as package:
        bad = package.testzip()
        if bad:
            raise RuntimeError(f"ZIP checksum mismatch: {bad}")
    temporary.replace(archive)
    print(f"Portable archive: {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
