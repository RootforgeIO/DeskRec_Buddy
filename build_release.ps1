param([switch]$UseBundledDependencies, [string]$Python = 'python', [string]$InnoCompiler)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
& (Join-Path $projectRoot 'build_portable.ps1') -UseBundledDependencies:$UseBundledDependencies -Python $Python
if (-not $?) { throw 'Portable build failed.' }
$buildPython = if ($UseBundledDependencies) { $Python } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$version = & $buildPython -c 'from product import VERSION; print(VERSION)'
if (-not $InnoCompiler) {
    $candidates = @((Join-Path $projectRoot '.build_tools\inno\ISCC.exe'),
        (Join-Path ${env:ProgramFiles} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'))
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $InnoCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler) { throw 'Install Inno Setup 6.7+ or 7 and pass -InnoCompiler with the ISCC.exe path.' }
& $InnoCompiler '/Qp' "/DAppVersion=$version" "/DSourceRoot=$projectRoot\dist\DeskRec Buddy" "/DReleaseRoot=$projectRoot\release" 'packaging\DeskRecBuddy.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
& $buildPython tools\release_metadata.py manifest
if ($LASTEXITCODE -ne 0) { throw 'Release manifest creation failed.' }
