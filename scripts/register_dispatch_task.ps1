param(
    [string]$TaskName = "ScraperWorkflowDispatch",
    [int]$Minute = 11
)

if ($Minute -lt 0 -or $Minute -gt 59) {
    Write-Error "Minute must be between 0 and 59."
    exit 1
}

$scriptPath = Join-Path $PSScriptRoot "dispatch_workflow.ps1"
if (-not (Test-Path $scriptPath)) {
    Write-Error "Missing script: $scriptPath"
    exit 1
}

$start = "00:{0:D2}" -f $Minute
$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

schtasks.exe /Create /F /SC HOURLY /MO 1 /ST $start /TN $TaskName /TR $taskCommand | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create scheduled task."
    exit 1
}

Write-Output "Scheduled task '$TaskName' created. Runs hourly at minute $Minute (local time)."
Write-Output "First, set user env var GITHUB_PAT to a GitHub token with Actions write permission."
