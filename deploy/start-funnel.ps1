[CmdletBinding()]
param(
    [switch]$ConfirmEnable,

    [ValidateSet(443, 8443, 10000)]
    [int]$FunnelHttpsPort = 443,

    [ValidateRange(1, 65535)]
    [int]$LocalPort = 8080,

    [string]$ComposeFile
)

$ErrorActionPreference = 'Stop'
$markerPath = Join-Path $PSScriptRoot '.stage5-funnel.json'

if (-not $ConfirmEnable) {
    throw 'Funnel enablement refused; pass -ConfirmEnable explicitly.'
}
if ([string]::IsNullOrWhiteSpace($ComposeFile)) {
    $ComposeFile = Join-Path $PSScriptRoot 'docker-compose.yml'
}

$availableKiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
if ($availableKiB -lt 2MB) {
    throw 'Available physical memory is below 2 GiB; Funnel enablement refused.'
}
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw 'The Compose file does not exist.'
}
if (Test-Path -LiteralPath $markerPath) {
    throw 'This project already owns a Funnel marker; stop it before enabling again.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI is not installed or is not on PATH.'
}
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    throw 'Tailscale CLI is not installed or is not on PATH.'
}

$gatewayServices = & docker compose -f $ComposeFile ps --status running --services gateway
if ($LASTEXITCODE -ne 0 -or @($gatewayServices) -notcontains 'gateway') {
    throw 'The Compose gateway is not running.'
}
$readyUrl = "http://127.0.0.1:$LocalPort/api/ready"
try {
    $ready = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec 10
}
catch {
    throw 'The gateway readiness endpoint is unavailable.'
}
if ($ready.StatusCode -ne 200) {
    throw 'The gateway readiness endpoint did not return HTTP 200.'
}

$tailscaleState = & tailscale status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $tailscaleState.BackendState -ne 'Running') {
    throw 'Tailscale is not logged in and running.'
}

$versionText = [string](& tailscale version | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $versionText -notmatch '^(\d+)\.(\d+)\.(\d+)') {
    throw 'Unable to determine the Tailscale client version.'
}
$version = [Version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
if ($version -lt [Version]'1.38.3') {
    throw 'Tailscale 1.38.3 or later is required for Funnel.'
}

$existingStatusRaw = [string](& tailscale funnel status --json)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the existing Funnel configuration.'
}
$existingStatus = $null
if (-not [string]::IsNullOrWhiteSpace($existingStatusRaw)) {
    $existingStatus = $existingStatusRaw | ConvertFrom-Json
}
if ($null -ne $existingStatus -and $existingStatus.PSObject.Properties.Count -gt 0) {
    throw 'Another Funnel configuration already exists; refusing to overwrite it.'
}

$target = "http://127.0.0.1:$LocalPort"
$arguments = @('funnel', "--https=$FunnelHttpsPort", '--bg', '--yes', $target)
& tailscale @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'Enabling Funnel failed; complete any browser approval and retry.'
}

try {
    $enabledStatusRaw = [string](& tailscale funnel status --json)
    if ($LASTEXITCODE -ne 0 -or -not $enabledStatusRaw.Contains($target)) {
        throw 'Funnel did not report the expected gateway target.'
    }
    $dnsName = [string]$tailscaleState.Self.DNSName
    if ([string]::IsNullOrWhiteSpace($dnsName)) {
        throw 'Tailscale did not report a DNS name for this device.'
    }
    $hostName = $dnsName.TrimEnd('.')
    $publicUrl = if ($FunnelHttpsPort -eq 443) {
        "https://$hostName"
    }
    else {
        "https://${hostName}:$FunnelHttpsPort"
    }
    $marker = [ordered]@{
        schema_version = 1
        target = $target
        https_port = $FunnelHttpsPort
        public_url = $publicUrl
    }
    $marker | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding utf8
}
catch {
    & tailscale funnel "--https=$FunnelHttpsPort" $target off | Out-Null
    throw
}

Write-Output "Funnel enabled: $publicUrl"
Write-Output 'Only the loopback gateway target is published; API, worker, database, and metrics remain internal.'
