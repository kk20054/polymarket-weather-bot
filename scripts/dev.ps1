[CmdletBinding()]
param(
  [string]$InstallDirectory = "D:\WeatherBot\Launcher",
  [switch]$ReinstallLauncher
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Installer = Join-Path $PSScriptRoot "install_weatherbot_launcher.ps1"
$Launcher = Join-Path $InstallDirectory "WeatherBotLauncher.exe"

if ($ReinstallLauncher -or -not (Test-Path -LiteralPath $Launcher)) {
  if (-not (Test-Path -LiteralPath $Installer)) {
    throw "Launcher installer was not found: $Installer"
  }

  Write-Host "Installing the canonical WeatherBot launcher..." -ForegroundColor Cyan
  & $Installer -InstallDirectory $InstallDirectory
  if ($LASTEXITCODE -ne 0) {
    throw "WeatherBot launcher installation failed with exit code $LASTEXITCODE."
  }
}

if (-not (Test-Path -LiteralPath $Launcher)) {
  throw "WeatherBot launcher was not found after installation: $Launcher"
}

Write-Host "Starting WeatherBot from $ProjectRoot" -ForegroundColor Cyan
Start-Process -FilePath $Launcher -WorkingDirectory $InstallDirectory | Out-Null
Write-Host "Launcher started. It will verify ports 8765/5173, start the scheduler, and open the dashboard." -ForegroundColor Green
