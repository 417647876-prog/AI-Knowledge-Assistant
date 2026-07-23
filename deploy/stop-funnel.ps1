[CmdletBinding()]
param(
    [switch]$ConfirmDisable
)

$ErrorActionPreference = 'Stop'
$markerPath = Join-Path $PSScriptRoot '.stage5-funnel.json'

if (-not $ConfirmDisable) {
    throw 'Funnel disablement refused; pass -ConfirmDisable explicitly.'
}
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    throw 'Tailscale CLI is not installed or is not on PATH.'
}
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw 'This project does not own a Funnel marker; nothing was changed.'
}

$marker = Get-Content -LiteralPath $markerPath -Raw -Encoding utf8 | ConvertFrom-Json
if (
    $marker.schema_version -ne 1 `
    -or [string]::IsNullOrWhiteSpace([string]$marker.target) `
    -or @('443', '8443', '10000') -notcontains [string]$marker.https_port
) {
    throw 'The project Funnel marker is invalid; nothing was changed.'
}

$tailscaleState = & tailscale status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $tailscaleState.BackendState -ne 'Running') {
    throw 'Tailscale is not logged in and running.'
}
$currentStatusRaw = [string](& tailscale funnel status --json)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the current Funnel configuration.'
}
$target = [string]$marker.target
if (-not $currentStatusRaw.Contains($target)) {
    throw 'The active Funnel does not match this project marker; nothing was changed.'
}

$httpsPort = [int]$marker.https_port
$arguments = @('funnel', "--https=$httpsPort", $target, 'off')
& tailscale @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'Disabling the project Funnel mapping failed.'
}
Remove-Item -LiteralPath $markerPath

Write-Output 'Project Funnel mapping disabled. Docker and persistent volumes were not changed.'

