param([int]$Seconds = 180)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot
$destination = Join-Path $projectRoot ('.smoke\soak-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$capture = Start-Process -FilePath 'dist\DeskRec Buddy\DeskRec Buddy.exe' -ArgumentList ('--smoke "' + $destination + '" --full-hd --seconds ' + $Seconds + ' --keep-name') -WindowStyle Hidden -PassThru
$samples = @()
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $encoder = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($capture.Id)" | Where-Object { $_.Name -like 'ffmpeg*' -and $_.CommandLine -match 'rawvideo' } | Select-Object -First 1
    } while (-not $encoder -and -not $capture.HasExited -and [DateTime]::UtcNow -lt $deadline)
    if (-not $encoder) { throw 'Capture encoder did not start.' }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $Seconds - 5 -and -not $capture.HasExited) {
        Start-Sleep -Seconds 10
        if ($capture.HasExited) { break }
        $appProcess = Get-Process -Id $capture.Id
        $encoderProcess = Get-Process -Id $encoder.ProcessId
        $video = Get-ChildItem -LiteralPath $destination -Filter '*.mp4' | Select-Object -First 1
        # Directory entries can report a stale size for a file still open by
        # FFmpeg. Query the live handle without blocking writes or replacement.
        $reader = [IO.File]::Open($video.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        try { $liveBytes = $reader.Length } finally { $reader.Dispose() }
        $sample = [pscustomobject]@{seconds=[math]::Round($clock.Elapsed.TotalSeconds, 1); app_cpu_seconds=$appProcess.TotalProcessorTime.TotalSeconds; encoder_cpu_seconds=$encoderProcess.TotalProcessorTime.TotalSeconds; memory_mib=[math]::Round(($appProcess.WorkingSet64+$encoderProcess.WorkingSet64)/1MB, 1); bytes=$liveBytes}
        $samples += $sample
        if ($samples.Count -gt 1 -and $sample.bytes -lt $samples[-2].bytes) { throw 'Live recording shrank.' }
        $sample | ConvertTo-Json -Compress | Write-Output
    }
    if (-not $capture.WaitForExit(45000) -or $capture.ExitCode -ne 0) { throw 'Soak capture did not save normally.' }
    $video = Get-ChildItem -LiteralPath $destination -Filter '*.mp4' | Select-Object -First 1
    if (-not $video -or (Get-ChildItem -LiteralPath $destination -Directory -Filter '.deskrec-*')) { throw 'Recording was not finalized.' }
    & '.\vendor\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe' -v error -xerror -i $video.FullName -f null NUL
    if ($LASTEXITCODE -ne 0) { throw 'Soak MP4 decode failed.' }
    & python tests\mp4_timing.py $video.FullName --expect-seconds $Seconds
    if ($LASTEXITCODE -ne 0) { throw 'Soak MP4 timing failed.' }
    $first, $last = $samples[0], $samples[-1]
    $duration = $last.seconds - $first.seconds
    $cpu = 100*(($last.app_cpu_seconds+$last.encoder_cpu_seconds)-($first.app_cpu_seconds+$first.encoder_cpu_seconds))/$duration/[Environment]::ProcessorCount
    $growth = $last.memory_mib - $first.memory_mib
    if ($growth -gt 64) { throw 'Unexplained memory growth over 64 MiB.' }
    if ($cpu -gt 15) { throw 'Sustained CPU exceeds 15%.' }
    $report = [pscustomobject]@{seconds=$Seconds; resolution='1920x1080'; fps=30; logical_processors=[Environment]::ProcessorCount; average_total_cpu_percent=[math]::Round($cpu, 2); memory_growth_mib=[math]::Round($growth, 1); video=$video.FullName; samples=$samples}
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination 'report.json') -Encoding utf8NoBOM
    Write-Output "PASS: $Seconds seconds, finalized MP4 decode, CPU $([math]::Round($cpu, 2))%, memory growth $growth MiB; $destination"
} finally {
    if (-not $capture.HasExited) { & taskkill /PID $capture.Id /T /F | Out-Null }
}
