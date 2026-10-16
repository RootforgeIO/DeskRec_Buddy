"""Include upstream license texts for the pinned binary distribution."""

from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product import APP_NAME


def normalized(name):
    return name.casefold().replace("-", "_")


def main():
    target = ROOT / "dist" / APP_NAME / "LICENSES"
    target.mkdir(exist_ok=True)
    for source in (ROOT / "LICENSES").iterdir():
        if source.is_file():
            shutil.copy2(source, target / source.name)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", target / "Python-LICENSE.txt")
    tkinter_license = Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms"
    shutil.copy2(tkinter_license, target / "Tk-license.terms")
    distributions = list(metadata.distributions())
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() + ["pyinstaller==6.16.0"]
    for requirement in requirements:
        name, version = requirement.split("==")
        matches = [d for d in distributions if normalized(d.metadata["Name"]) == normalized(name) and d.version == version]
        if not matches:
            raise RuntimeError(f"Missing metadata for {requirement}")
        distribution = matches[0]
        texts = [file for file in distribution.files or [] if ".dist-info" in str(file)
                 and Path(file).name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS"))]
        if not texts:
            raise RuntimeError(f"Missing license text for {requirement}")
        package_target = target / f"{name}-{version}"
        package_target.mkdir(exist_ok=True)
        for file in texts:
            shutil.copy2(distribution.locate_file(file), package_target / Path(file).name)
    executable = ROOT / "dist" / APP_NAME / "_internal" / "imageio_ffmpeg" / "binaries" / "ffmpeg-win-x86_64-v7.1.exe"
    with (target / "FFmpeg-build.txt").open("wb") as details:
        for argument in ("-version", "-buildconf", "-L"):
            result = subprocess.run([str(executable), argument], stdout=details, stderr=details, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("Cannot inspect bundled FFmpeg")
    print(f"Licenses collected: {target}")


if __name__ == "__main__":
    main()
