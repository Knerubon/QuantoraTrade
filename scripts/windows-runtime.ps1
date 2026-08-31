param(
  [Parameter(Mandatory=$true)][ValidateSet("Start", "Status", "Stop")][string]$Action
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root "artifacts\windows-runtime"
$ApiPid = Join-Path $RunDir "api.pid"
$WorkerPid = Join-Path $RunDir "worker.pid"

function Read-PidFile([string]$Path) {
  if (-not (Test-Path $Path)) { return $null }
  $Value = (Get-Content $Path -Raw).Trim()
  if ($Value -notmatch '^\d+$') { throw "Invalid PID file: $Path" }
  return [int]$Value
}

function Stop-ManagedProcess([string]$Path) {
  $ProcessId = Read-PidFile $Path
  if ($null -ne $ProcessId) {
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $Process) { Stop-Process -Id $ProcessId }
    Remove-Item $Path -Force
  }
}

Set-Location $Root
if ($Action -eq "Start") {
  if ($env:QUANTORA_TRADING_MODE -ne "paper") { throw "Set QUANTORA_TRADING_MODE=paper" }
  if ($env:QUANTORA_SMOKE_ONLY -ne "true") { throw "Set QUANTORA_SMOKE_ONLY=true" }
  if ([string]::IsNullOrWhiteSpace($env:QUANTORA_POSTGRES_PASSWORD)) { throw "Set QUANTORA_POSTGRES_PASSWORD" }
  if ([string]::IsNullOrWhiteSpace($env:QUANTORA_DATABASE_URL)) { throw "Set QUANTORA_DATABASE_URL" }
  if ($env:QUANTORA_API_TOKEN.Length -lt 24) { throw "QUANTORA_API_TOKEN must contain at least 24 characters" }
  New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
  docker compose -f docker-compose.windows.yml up -d --wait
  python -m alembic upgrade head
  $Api = Start-Process python -ArgumentList "-m", "quantora_trade.runtime.windows", "api" -PassThru -RedirectStandardOutput "$RunDir\api.log" -RedirectStandardError "$RunDir\api-error.log"
  $Worker = Start-Process python -ArgumentList "-m", "quantora_trade.runtime.windows", "worker" -PassThru -RedirectStandardOutput "$RunDir\worker.log" -RedirectStandardError "$RunDir\worker-error.log"
  Set-Content -Path $ApiPid -Value $Api.Id
  Set-Content -Path $WorkerPid -Value $Worker.Id
  Start-Sleep -Seconds 3
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/live" | ConvertTo-Json
  Write-Host "Windows PAPER smoke runtime started. This is not empirical soak evidence."
} elseif ($Action -eq "Status") {
  docker compose -f docker-compose.windows.yml ps
  foreach ($Path in @($ApiPid, $WorkerPid)) {
    $ProcessId = Read-PidFile $Path
    if ($null -eq $ProcessId) { Write-Host "${Path}: not managed"; continue }
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    Write-Host "${Path}: $(if ($null -eq $Process) {'stopped'} else {'running'})"
  }
  if (-not [string]::IsNullOrWhiteSpace($env:QUANTORA_API_TOKEN)) {
    $Headers = @{Authorization="Bearer $env:QUANTORA_API_TOKEN"}
    Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/status" -Headers $Headers | ConvertTo-Json
  }
} else {
  Stop-ManagedProcess $WorkerPid
  Stop-ManagedProcess $ApiPid
  docker compose -f docker-compose.windows.yml stop
  Write-Host "Windows PAPER smoke runtime stopped. PostgreSQL data volume was preserved."
}
