param([string]$Setup = 'release\DeskRec-Buddy-1.0.0-setup.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot
$testRoot = Join-Path $projectRoot ('.smoke\installer-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
$installDirectory = Join-Path $testRoot 'Приложение с пробелами'
$recordings = Join-Path $installDirectory 'Мои видео'
$registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6C799F17-789D-4AC0-8E25-799A94498439}_is1'
if (Test-Path -LiteralPath $registryKey) { throw 'An existing DeskRec Buddy installation must not be replaced by this test.' }
$shell = New-Object -ComObject WScript.Shell
$desktopLink = Join-Path $shell.SpecialFolders.Item('Desktop') 'DeskRec Buddy.lnk'
$menuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'DeskRec Buddy.lnk'
if ((Test-Path -LiteralPath $desktopLink) -or (Test-Path -LiteralPath $menuLink)) { throw 'Existing user shortcuts must not be replaced by this test.' }
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$settings = Join-Path $env:LOCALAPPDATA 'DeskRec Buddy\deskrec-settings.json'
$savedSettings = if (Test-Path -LiteralPath $settings) { [IO.File]::ReadAllBytes($settings) } else { $null }
$application = $null

function Run-Setup([string]$LogName, [bool]$ExpectSuccess = $true) {
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /TASKS=desktopicon /DIR="' + $installDirectory + '" /LOG="' + (Join-Path $testRoot $LogName) + '"'
    $process = Start-Process -FilePath $Setup -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) {
        & taskkill /PID $process.Id /T /F | Out-Null
        throw 'Installer timed out.'
    }
    if ($ExpectSuccess -and $process.ExitCode -ne 0) { throw "Installer exit code $($process.ExitCode)" }
    if (-not $ExpectSuccess -and $process.ExitCode -eq 0) { throw 'Installer updated a running application.' }
    return $process.ExitCode
}

try {
    Run-Setup 'install.log' | Out-Null
    $executable = Join-Path $installDirectory 'DeskRec Buddy.exe'
    if (-not (Test-Path -LiteralPath $executable)) { throw 'Installed EXE missing.' }
    if (Test-Path -LiteralPath (Join-Path $installDirectory 'portable.flag')) { throw 'Installed app incorrectly marked portable.' }
    if (-not (Test-Path -LiteralPath $registryKey)) { throw 'Uninstaller registry entry missing.' }
    $metadata = Get-ItemProperty -LiteralPath $registryKey
    if ($metadata.DisplayName -ne 'DeskRec Buddy' -or $metadata.DisplayVersion -ne '1.0.0' -or $metadata.Publisher -ne 'rootforged.io') { throw 'Incorrect installed product metadata.' }
    foreach ($link in @($desktopLink, $menuLink)) {
        if (-not (Test-Path -LiteralPath $link)) { throw "Shortcut missing: $link" }
        $shortcut = $shell.CreateShortcut($link)
        if ($shortcut.TargetPath -ne $executable -or $shortcut.WorkingDirectory -ne $installDirectory) { throw 'Shortcut points to an incorrect executable or directory.' }
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $settings) -Force | Out-Null
    @{folder=$recordings; microphone_gain='300%'} | ConvertTo-Json | Set-Content -LiteralPath $settings -Encoding utf8NoBOM
    Start-Process -FilePath $desktopLink | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 150
        $entries = @(Get-CimInstance Win32_Process -Filter "Name = 'DeskRec Buddy.exe'" | Where-Object { $_.ExecutablePath -eq $executable })
        $application = $entries | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } | Where-Object { $_.MainWindowTitle -eq 'DeskRec Buddy' } | Select-Object -First 1
    } while (-not $application -and [DateTime]::UtcNow -lt $deadline)
    if (-not $application) { throw 'App did not launch correctly from the Desktop shortcut.' }
    Run-Setup 'blocked-update.log' $false | Out-Null
    if ($application.HasExited) { throw 'Update check terminated the running app.' }
    if (-not $application.CloseMainWindow() -or -not $application.WaitForExit(15000)) { throw 'Installed GUI did not close gracefully.' }
    $application = $null
    Run-Setup 'upgrade.log' | Out-Null
    if ((Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json).microphone_gain -ne '300%') { throw 'Reinstallation overwrote user settings.' }
    $capture = Start-Process -FilePath $executable -ArgumentList ('--smoke "' + $recordings + '" --seconds 3') -WindowStyle Hidden -PassThru
    if (-not $capture.WaitForExit(45000) -or $capture.ExitCode -ne 0) { throw 'Installed EXE capture failed.' }
    $video = Get-ChildItem -LiteralPath $recordings -Filter '*.mp4' | Select-Object -First 1
    if (-not $video -or $video.Length -lt 1024) { throw 'Installed EXE did not save MP4.' }
    & '.\vendor\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe' -v error -xerror -i $video.FullName -f null NUL
    if ($LASTEXITCODE -ne 0) { throw 'Installed EXE MP4 failed to decode.' }
    $uninstaller = Start-Process -FilePath (Join-Path $installDirectory 'unins000.exe') -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -PassThru
    if (-not $uninstaller.WaitForExit(45000) -or $uninstaller.ExitCode -ne 0) { throw 'Uninstallation failed.' }
    if ((Test-Path -LiteralPath $executable) -or (Test-Path -LiteralPath $desktopLink) -or (Test-Path -LiteralPath $menuLink) -or (Test-Path -LiteralPath $registryKey)) { throw 'Uninstall left program, registry entry or shortcuts.' }
    if (-not (Test-Path -LiteralPath $video.FullName) -or -not (Test-Path -LiteralPath $settings)) { throw 'Uninstall deleted a user recording or settings.' }
    Write-Output "PASS: install, metadata, Desktop launch, running-app update block, reinstall, audio/video capture, uninstall preserving MP4/settings: $testRoot"
} finally {
    $testProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'DeskRec Buddy.exe'" | Where-Object { $_.ExecutablePath -eq (Join-Path $installDirectory 'DeskRec Buddy.exe') })
    foreach ($entry in $testProcesses) {
        $testProcess = Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue
        if ($testProcess) { $testProcess.CloseMainWindow() | Out-Null; $testProcess.WaitForExit(15000) | Out-Null }
    }
    $remainingUninstaller = Join-Path $installDirectory 'unins000.exe'
    if (Test-Path -LiteralPath $remainingUninstaller) { Start-Process -FilePath $remainingUninstaller -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -Wait }
    if ($null -ne $savedSettings) { [IO.File]::WriteAllBytes($settings, $savedSettings) }
    elseif (Test-Path -LiteralPath $settings) { Remove-Item -LiteralPath $settings }
}
