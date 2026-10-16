# Third-party components

DeskRec Buddy uses these separate components:

- DXcam 0.3.0 — MIT — https://github.com/ra1nty/DXcam
- SoundCard 0.4.5 — BSD 3-Clause — https://github.com/bastibe/SoundCard
- NumPy 1.26.4 — BSD 3-Clause — https://numpy.org/
- CFFI 2.1.1 — MIT — https://cffi.readthedocs.io/
- comtypes 1.4.17 — MIT — https://github.com/enthought/comtypes
- pycparser 3.11 — BSD 3-Clause — https://github.com/eliben/pycparser
- imageio-ffmpeg 0.6.0 — BSD 2-Clause — https://github.com/imageio/imageio-ffmpeg
- Python 3.11 and Tcl/Tk — their respective upstream licenses.
- PyInstaller 6.16.0 bootloader — GPL with the bootloader exception — https://pyinstaller.org/en/stable/license.html
- FFmpeg 7.1 essentials build from Gyan — GPL version 3 or later, as reported by the bundled executable. The executable runs as a separate process. Build information and source links: https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org/download.html. The license is available at https://www.gnu.org/licenses/gpl-3.0.html.

Full upstream license texts are included in `LICENSES`, together with the bundled FFmpeg's version, build configuration and license notice (`FFmpeg-build.txt`). Package license files and metadata are also included under `_internal` where supplied by the upstream packages. FFmpeg's license notice can also be displayed with `_internal/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe -L`. FFmpeg source release: https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz; build and external library source references: https://www.gyan.dev/ffmpeg/builds/.
