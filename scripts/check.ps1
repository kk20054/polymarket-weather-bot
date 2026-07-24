[CmdletBinding()]
param(
  [ValidateSet("quick", "docs", "backend", "frontend", "full")]
  [string]$Scope = "quick",
  [switch]$Runtime
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$FrontendRoot = Join-Path $ProjectRoot "frontend"

function Invoke-NativeStep {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][scriptblock]$Action
  )

  Write-Host "`n==> $Name" -ForegroundColor Cyan
  & $Action
  if ($LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE."
  }
}

Push-Location $ProjectRoot
try {
  Invoke-NativeStep "Git whitespace check" { git diff --check }

  if ($Scope -in @("quick", "backend")) {
    if (-not (Test-Path -LiteralPath $Python)) {
      throw "Missing venv Python: $Python"
    }
    Invoke-NativeStep "Core and dashboard contract tests" {
      & $Python -m unittest tests.test_v3_core tests.test_polywx_contract
    }
  }

  if ($Scope -eq "full") {
    if (-not (Test-Path -LiteralPath $Python)) {
      throw "Missing venv Python: $Python"
    }
    Invoke-NativeStep "Full Python test suite" {
      & $Python -m unittest discover tests
    }
  }

  if ($Scope -in @("quick", "frontend", "full")) {
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "package.json"))) {
      throw "Frontend package.json was not found: $FrontendRoot"
    }
    Push-Location $FrontendRoot
    try {
      Invoke-NativeStep "Frontend production build" { npm.cmd run build }
    } finally {
      Pop-Location
    }
  }

  if ($Scope -in @("backend", "full")) {
    Invoke-NativeStep "Observation readiness verification" {
      & $Python -m weatherbot_v3.cli project-verify --verification-mode observation
    }
  }

  if ($Runtime) {
    Write-Host "`n==> Local runtime smoke check" -ForegroundColor Cyan
    $Scheduler = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/scheduler/status" -TimeoutSec 10
    $DashboardTimer = Measure-Command {
      $null = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/dashboard" -TimeoutSec 30
    }
    [pscustomobject]@{
      scheduler_running = [bool]$Scheduler.running
      dashboard_ms = [math]::Round($DashboardTimer.TotalMilliseconds, 1)
    } | Format-Table -AutoSize
  }

  Write-Host "`n==> Working tree" -ForegroundColor Cyan
  git status --short --branch
  if ($LASTEXITCODE -ne 0) {
    throw "git status failed with exit code $LASTEXITCODE."
  }

  Write-Host "`nWeatherBot checks passed for scope '$Scope'." -ForegroundColor Green
} finally {
  Pop-Location
}
