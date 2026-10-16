"""Version resources and checksums for locally built release artifacts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product import APP_NAME, VERSION, PUBLISHER


def version_resource():
    version = tuple(map(int, VERSION.split("."))) + (0,)
    fields = {"CompanyName": PUBLISHER, "FileDescription": APP_NAME, "FileVersion": VERSION,
              "InternalName": APP_NAME, "OriginalFilename": APP_NAME + ".exe",
              "ProductName": APP_NAME, "ProductVersion": VERSION,
              "LegalCopyright": f"Copyright (c) 2026 {PUBLISHER}"}
    strings = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [{strings}])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    output = ROOT / "build" / "windows-version.txt"
    output.parent.mkdir(exist_ok=True)
    output.write_text(content, encoding="utf-8")


def manifest():
    files = [ROOT / "release" / f"DeskRec-Buddy-{VERSION}-{suffix}" for suffix in ("portable.zip", "setup.exe")]
    entries = [{"file": file.name, "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()} for file in files]
    git = subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", "status", "--porcelain"], capture_output=True, text=True)
    data = {"product": APP_NAME, "version": VERSION, "publisher": PUBLISHER, "platform": "Windows 10 2004+ / 11 x64",
            "built_utc": datetime.now(timezone.utc).isoformat(), "source_commit": git.stdout.strip(),
            "source_dirty": bool(status.stdout.strip()), "signed": False, "artifacts": entries}
    (ROOT / "release" / "release-manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (ROOT / "release" / "SHA256SUMS.txt").write_text("\n".join(f"{entry['sha256']}  {entry['file']}" for entry in entries) + "\n", encoding="ascii")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    manifest() if sys.argv[1:] == ["manifest"] else version_resource()
