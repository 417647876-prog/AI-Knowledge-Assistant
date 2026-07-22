[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory,

    [switch]$UseDocker,

    [switch]$ConfirmRestore
)

$ErrorActionPreference = 'Stop'

function Assert-MinimumMemory {
    $availableKiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
    if ($availableKiB -lt 2MB) {
        throw 'Available physical memory is below 2 GiB; restore refused.'
    }
}

if (-not $ConfirmRestore) {
    throw 'Restore refused by default; pass -ConfirmRestore explicitly.'
}
if (-not $UseDocker) {
    throw 'Docker mode requires explicit -UseDocker opt-in.'
}

Assert-MinimumMemory
$composeFile = Join-Path $PSScriptRoot 'docker-compose.yml'
$backupPath = [IO.Path]::GetFullPath($BackupDirectory).TrimEnd('\', '/')
$rootPath = [IO.Path]::GetPathRoot($backupPath).TrimEnd('\', '/')
if ($backupPath -eq $rootPath -or -not (Test-Path -LiteralPath $backupPath -PathType Container)) {
    throw 'The backup directory does not exist or is unsafe.'
}

$manifestPath = Join-Path $backupPath 'manifest.json'
$databaseDump = Join-Path $backupPath 'database.dump'
$uploadsPath = Join-Path $backupPath 'uploads'
if (
    -not (Test-Path -LiteralPath $manifestPath -PathType Leaf) `
    -or -not (Test-Path -LiteralPath $databaseDump -PathType Leaf) `
    -or -not (Test-Path -LiteralPath $uploadsPath -PathType Container)
) {
    throw 'Invalid backup structure: manifest.json, database.dump, and uploads are required.'
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if (
    $manifest.format_version -ne 1 `
    -or $manifest.database_file -ne 'database.dump' `
    -or $manifest.uploads_directory -ne 'uploads'
) {
    throw 'The backup manifest version or filenames are unsupported.'
}

$containerDump = "/tmp/stage5-$([Guid]::NewGuid().ToString('N')).dump"
$servicesStopped = $false
try {
    & docker compose -f $composeFile stop api worker gateway
    if ($LASTEXITCODE -ne 0) { throw 'Stopping application services failed.' }
    $servicesStopped = $true

    & docker compose -f $composeFile cp $databaseDump "postgres:$containerDump"
    if ($LASTEXITCODE -ne 0) { throw 'Copying the database restore file failed.' }

    & docker compose -f $composeFile exec -T postgres `
        dropdb --if-exists --force -U knowledge knowledge
    if ($LASTEXITCODE -ne 0) { throw 'Dropping the database before restore failed.' }
    & docker compose -f $composeFile exec -T postgres createdb -U knowledge knowledge
    if ($LASTEXITCODE -ne 0) { throw 'Creating the database before restore failed.' }
    & docker compose -f $composeFile exec -T postgres `
        pg_restore -U knowledge -d knowledge --exit-on-error $containerDump
    if ($LASTEXITCODE -ne 0) { throw 'Restoring the database failed.' }

    $mount = "${backupPath}:/backup:ro"
    $restoreUploads = @'
set -eu
old=/app/uploads/.stage5-restore-old
rm -rf "$old"
mkdir -p "$old"
find /app/uploads -mindepth 1 -maxdepth 1 ! -name .stage5-restore-old -exec mv {} "$old"/ \;
rollback() {
  find /app/uploads -mindepth 1 -maxdepth 1 ! -name .stage5-restore-old -exec rm -rf {} +
  cp -a "$old"/. /app/uploads/
}
trap rollback EXIT
cp -R /backup/uploads/. /app/uploads/
trap - EXIT
rm -rf "$old"
'@
    & docker compose -f $composeFile run --rm --no-deps `
        -v $mount --entrypoint sh api -c $restoreUploads
    if ($LASTEXITCODE -ne 0) { throw 'Restoring uploaded files failed.' }
}
finally {
    & docker compose -f $composeFile exec -T postgres rm -f $containerDump 2>$null
    if ($servicesStopped) {
        & docker compose -f $composeFile up -d api worker gateway
    }
}

Write-Output "Restore completed: $backupPath"
