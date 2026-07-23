[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot,

    [switch]$UseDocker
)

$ErrorActionPreference = 'Stop'

function Assert-MinimumMemory {
    $availableKiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
    if ($availableKiB -lt 2MB) {
        throw 'Available physical memory is below 2 GiB; backup refused.'
    }
}

function Assert-SafeRoot([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $rootPath = [IO.Path]::GetPathRoot($fullPath).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($fullPath) -or $fullPath -eq $rootPath) {
        throw 'The backup destination cannot be a filesystem root.'
    }
    return $fullPath
}

if (-not $UseDocker) {
    throw 'Docker mode requires explicit -UseDocker opt-in.'
}

Assert-MinimumMemory
$composeFile = Join-Path $PSScriptRoot 'docker-compose.yml'
$destination = Assert-SafeRoot $DestinationRoot
New-Item -ItemType Directory -Path $destination -Force | Out-Null

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDirectory = Join-Path $destination "stage5-backup-$timestamp"
if (Test-Path -LiteralPath $backupDirectory) {
    throw 'The timestamped backup directory already exists; retry later.'
}
New-Item -ItemType Directory -Path $backupDirectory | Out-Null
New-Item -ItemType Directory -Path (Join-Path $backupDirectory 'uploads') | Out-Null

$containerDump = "/tmp/stage5-$([Guid]::NewGuid().ToString('N')).dump"
$databaseDump = Join-Path $backupDirectory 'database.dump'

try {
    & docker compose -f $composeFile exec -T postgres pg_dump `
        -U knowledge -d knowledge -Fc --file $containerDump
    if ($LASTEXITCODE -ne 0) { throw 'The database backup command failed.' }

    & docker compose -f $composeFile cp "postgres:$containerDump" $databaseDump
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $databaseDump)) {
        throw 'Copying the database backup failed.'
    }

    $mount = "${backupDirectory}:/backup"
    & docker compose -f $composeFile run --rm --no-deps `
        -v $mount --entrypoint sh api `
        -c 'cp -R /app/uploads/. /backup/uploads/'
    if ($LASTEXITCODE -ne 0) { throw 'Backing up uploaded files failed.' }

    $manifest = [ordered]@{
        format_version = 1
        created_at = (Get-Date).ToUniversalTime().ToString('o')
        database_file = 'database.dump'
        uploads_directory = 'uploads'
    }
    $manifest | ConvertTo-Json | Set-Content `
        -LiteralPath (Join-Path $backupDirectory 'manifest.json') -Encoding utf8
}
finally {
    & docker compose -f $composeFile exec -T postgres rm -f $containerDump 2>$null
}

Write-Output "Backup completed: $backupDirectory"
