[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ApiUrl,
    [Parameter(Mandatory)][string]$Owner,
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][int]$DurationSeconds,
    [Parameter(Mandatory)][int]$IntervalSeconds,
    [Parameter(Mandatory)][string[]]$Symbols,
    [Parameter(Mandatory)][string]$StrategyId,
    [Parameter(Mandatory)][string]$ConfigVersion,
    [Parameter(Mandatory)][string]$Config,
    [Parameter(Mandatory)][string]$DataVersion,
    [Parameter(Mandatory)][string]$CodeVersion,
    [Parameter(Mandatory)][string]$Output,
    [switch]$AcknowledgePaperOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $AcknowledgePaperOnly) {
    throw "Refusing: pass -AcknowledgePaperOnly after owner approval."
}
if ([string]::IsNullOrWhiteSpace($env:QUANTORA_API_TOKEN)) {
    throw "QUANTORA_API_TOKEN must be set in this PowerShell session."
}

$arguments = @(
    "scripts/run_paper_soak.py",
    "--api-url", $ApiUrl,
    "--owner", $Owner,
    "--run-id", $RunId,
    "--duration-seconds", $DurationSeconds,
    "--interval-seconds", $IntervalSeconds,
    "--symbols"
) + $Symbols + @(
    "--strategy-id", $StrategyId,
    "--config-version", $ConfigVersion,
    "--config", $Config,
    "--data-version", $DataVersion,
    "--code-version", $CodeVersion,
    "--output", $Output,
    "--acknowledge-paper-only"
)

& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PAPER soak runner failed with exit code $LASTEXITCODE. Inspect the evidence incident log."
}

& python scripts/evaluate_paper_soak.py `
    --input $Output `
    --output-directory (Join-Path (Split-Path $Output -Parent) "reports") `
    --acknowledge-paper-only
exit $LASTEXITCODE
