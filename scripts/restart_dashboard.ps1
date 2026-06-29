param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 8765,
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "dashboard_server.pids"

if (-not (Test-Path -LiteralPath $Python)) {
  throw "Missing venv python: $Python"
}

if (-not (Test-Path -LiteralPath $DataDir)) {
  New-Item -ItemType Directory -Path $DataDir | Out-Null
}

$KnownPids = @()
if (Test-Path -LiteralPath $PidFile) {
  $KnownPids = Get-Content -LiteralPath $PidFile |
    Where-Object { $_ -match "^\d+$" } |
    ForEach-Object { [int]$_ }
}

$ListeningPids = netstat -ano |
  Select-String ":$Port\s" |
  Where-Object { $_.Line -match "\sLISTENING\s+(\d+)\s*$" } |
  ForEach-Object { [int]$Matches[1] } |
  Sort-Object -Unique

$StopPids = @($KnownPids + $ListeningPids) | Sort-Object -Unique

foreach ($ProcId in $StopPids) {
  Write-Host "Stopping dashboard PID $ProcId"
  Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
}

if ($NoStart) {
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  Write-Host "Stopped dashboard port $Port owners. NoStart was set."
  exit 0
}

Write-Host "Starting dashboard with venv python: $Python"
$Process = Start-Process `
  -FilePath $Python `
  -ArgumentList "-m", "uvicorn", "dashboard_server:app", "--host", $HostAddress, "--port", "$Port" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -PassThru

Start-Sleep -Seconds 3
$NewListeningPids = netstat -ano |
  Select-String ":$Port\s" |
  Where-Object { $_.Line -match "\sLISTENING\s+(\d+)\s*$" } |
  ForEach-Object { [int]$Matches[1] } |
  Sort-Object -Unique

@($Process.Id; $NewListeningPids) |
  Where-Object { $_ } |
  Sort-Object -Unique |
  Set-Content -LiteralPath $PidFile

$Dashboard = Invoke-RestMethod -Uri "http://$HostAddress`:$Port/api/dashboard" -TimeoutSec 15

[pscustomobject]@{
  ok = $true
  url = "http://$HostAddress`:$Port"
  started_pid = $Process.Id
  listening_pids = $NewListeningPids
  scanner_status = $Dashboard.stats.scanner_status
  strategy_status = $Dashboard.stats.strategy_readiness_status
  data_age_minutes = $Dashboard.stats.data_age_minutes
} | ConvertTo-Json
