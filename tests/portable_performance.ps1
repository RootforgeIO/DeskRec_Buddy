$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot
$destination = Join-Path $projectRoot ('.smoke\portable-perf-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$animation = Start-Process -FilePath 'python.exe' -ArgumentList 'tests\performance.py','--animate' -PassThru -WindowStyle Hidden
$recorder = Start-Process -FilePath 'dist\DeskRec Buddy\DeskRec Buddy.exe' -ArgumentList ('--smoke "' + $destination + '" --full-hd --seconds 20') -PassThru -WindowStyle Hidden
$startupDeadline = [DateTime]::UtcNow.AddSeconds(12)
do {
    Start-Sleep -Milliseconds 250
    $encoderIds = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($recorder.Id)" | Where-Object { $_.Name -like 'ffmpeg*' -and $_.CommandLine -match 'rawvideo' } | Select-Object -ExpandProperty ProcessId)
} while ($encoderIds.Count -ne 1 -and [DateTime]::UtcNow -lt $startupDeadline -and -not $recorder.HasExited)
if ($encoderIds.Count -ne 1) {
    $recorder.WaitForExit(20000) | Out-Null
    $animation.WaitForExit(20000) | Out-Null
    throw 'Expected one combined audio/video encoder process.'
}
Start-Sleep -Seconds 1
$logicalProcessors = [Environment]::ProcessorCount
$samples = @()
$clock = [Diagnostics.Stopwatch]::StartNew()
$previous = Get-Process -Id $recorder.Id
$previousCpu = $previous.TotalProcessorTime.TotalSeconds
$previousEncoderCpu = (Get-Process -Id $encoderIds | ForEach-Object { $_.TotalProcessorTime.TotalSeconds } | Measure-Object -Sum).Sum
$previousTime = $clock.Elapsed.TotalSeconds
for ($index = 0; $index -lt 7; $index++) {
    Start-Sleep -Seconds 1
    $current = Get-Process -Id $recorder.Id
    $encoders = @(Get-Process -Id $encoderIds)
    $cpu = $current.TotalProcessorTime.TotalSeconds
    $encoderCpu = ($encoders | ForEach-Object { $_.TotalProcessorTime.TotalSeconds } | Measure-Object -Sum).Sum
    $now = $clock.Elapsed.TotalSeconds
    $divisor = ($now - $previousTime) * $logicalProcessors
    $sample = [pscustomobject]@{
        second = [math]::Round($now, 2)
        app_cpu_percent = [math]::Round(100 * ($cpu - $previousCpu) / $divisor, 2)
        ffmpeg_cpu_percent = [math]::Round(100 * ($encoderCpu - $previousEncoderCpu) / $divisor, 2)
        total_memory_mb = [math]::Round(($current.WorkingSet64 + ($encoders | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 1)
    }
    $samples += $sample
    $sample | ConvertTo-Json -Compress | Write-Output
    $previousCpu, $previousEncoderCpu, $previousTime = $cpu, $encoderCpu, $now
}
if (-not $recorder.WaitForExit(30000)) { throw 'Portable capture did not stop.' }
if ($recorder.ExitCode -ne 0) { throw 'Portable capture failed.' }
$animation.WaitForExit(10000) | Out-Null
$video = Get-ChildItem -LiteralPath $destination -Filter '*.mp4' | Select-Object -First 1
if (-not $video) { throw 'No output video.' }
$report = [pscustomobject]@{
    logical_processors = $logicalProcessors
    resolution = '1920x1080'
    fps = 30
    duration_seconds = 20
    average_app_cpu_percent = [math]::Round(($samples | Measure-Object app_cpu_percent -Average).Average, 2)
    average_ffmpeg_cpu_percent = [math]::Round(($samples | Measure-Object ffmpeg_cpu_percent -Average).Average, 2)
    average_total_memory_mb = [math]::Round(($samples | Measure-Object total_memory_mb -Average).Average, 1)
    samples = $samples
    video = $video.FullName
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination 'report.json') -Encoding UTF8
$report | ConvertTo-Json -Depth 4 | Write-Output
if ($report.average_app_cpu_percent -gt 10) { throw 'Recorder CPU regression.' }
if (($report.average_app_cpu_percent + $report.average_ffmpeg_cpu_percent) -gt 15) { throw 'Total recorder CPU regression.' }
