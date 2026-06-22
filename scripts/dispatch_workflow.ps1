param(
    [string]$Owner = "webscraper1993",
    [string]$Repo = "scraper",
    [string]$WorkflowId = "scraper-email.yml",
    [string]$Ref = "main"
)

$token = $env:GITHUB_PAT
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Error "Missing GITHUB_PAT environment variable."
    exit 1
}

$uri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$WorkflowId/dispatches"
$headers = @{
    Authorization = "Bearer $token"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "scraper-local-scheduler"
}
$body = @{ ref = $Ref } | ConvertTo-Json -Compress

try {
    Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -Body $body -ContentType "application/json" -ErrorAction Stop | Out-Null
    Write-Output ("Dispatched workflow {0} for {1}/{2}@{3} at {4}" -f $WorkflowId, $Owner, $Repo, $Ref, (Get-Date).ToString("s"))
}
catch {
    Write-Error ("Dispatch failed: " + $_.Exception.Message)
    exit 1
}
