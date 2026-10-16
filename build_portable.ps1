param([switch]$UseBundledDependencies, [switch]$SkipArchive, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

if ($UseBundledDependencies) {
    $env:PYTHONPATH = (Join-Path $projectRoot '.build_tools') + ';' + (Join-Path $projectRoot 'vendor')
    $buildPython = $Python
} else {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    $buildPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $buildPython -m pip install -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\.pyinstaller'
& $buildPython tools\generate_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
& $buildPython tools\release_metadata.py
if ($LASTEXITCODE -ne 0) { throw 'Version metadata generation failed.' }
$version = & $buildPython -c 'from product import VERSION; print(VERSION)'
if ($LASTEXITCODE -ne 0) { throw 'Version lookup failed.' }
& $buildPython -m PyInstaller --noconfirm --clean --log-level ERROR --windowed --onedir `
    --name 'DeskRec Buddy' --specpath build --paths (Join-Path $projectRoot 'vendor') `
    --icon (Join-Path $projectRoot 'assets\deskrec-buddy.ico') --version-file (Join-Path $projectRoot 'build\windows-version.txt') `
    --add-data "${projectRoot}\assets\deskrec-buddy.ico;assets" `
    --collect-all dxcam --collect-all soundcard --collect-all imageio_ffmpeg app.py
if ($LASTEXITCODE -ne 0) { throw 'Portable build failed.' }
Copy-Item -LiteralPath 'README.md','THIRD_PARTY.md','CHANGELOG.md' -Destination 'dist\DeskRec Buddy'
New-Item -ItemType Directory -Path 'dist\DeskRec Buddy\assets' -Force | Out-Null
Copy-Item -LiteralPath 'assets\deskrec-buddy.svg' -Destination 'dist\DeskRec Buddy\assets'
Set-Content -LiteralPath 'dist\DeskRec Buddy\portable.flag' -Value 'DeskRec Buddy portable: settings are stored next to the EXE.' -Encoding ascii
Copy-Item -LiteralPath 'packaging\Create Desktop Shortcut.cmd' -Destination 'dist\DeskRec Buddy'
& $buildPython tools\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
if (-not $SkipArchive) {
    & $buildPython tools\package_portable.py
    if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed.' }
}
