[CmdletBinding()]
param(
    [switch]$Build,

    [ValidateRange(10, 600)]
    [int]$ReadyTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $projectRoot 'deploy/docker-compose.yml'
$environmentFile = Join-Path $projectRoot 'deploy/.env'
$readyUrl = 'http://127.0.0.1:8080/api/ready'
$appUrl = 'http://127.0.0.1:8080'

function Test-DockerEngine {
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Show-ComposeHelp {
    Write-Host '可使用以下命令排查：'
    Write-Host "  docker compose -f `"$composeFile`" ps"
    Write-Host "  docker compose -f `"$composeFile`" logs --tail 100 gateway api worker postgres"
}

# Win32_OperatingSystem.FreePhysicalMemory 的单位是 KiB。
$minimumFreeKiB = 2GB / 1KB
$availableKiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
if ($availableKiB -lt $minimumFreeKiB) {
    throw '可用物理内存低于 2 GiB，已拒绝启动 Docker 重任务。'
}
if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw "缺少 Docker Compose 文件：`"$composeFile`"。"
}
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    Write-Host '缺少 deploy/.env，请先执行：'
    Write-Host "  Copy-Item `"$(Join-Path $projectRoot 'deploy/.env.example')`" `"$environmentFile`""
    Write-Host "  notepad `"$environmentFile`""
    throw '完成配置后请重新运行启动器。'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker CLI，请先安装或启动 Docker Desktop。'
}

if (-not (Test-DockerEngine)) {
    Write-Host 'Docker 引擎未运行，正在尝试启动 Docker Desktop...'
    $desktopHelp = (& docker desktop --help 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $desktopHelp -notmatch '(?m)^\s*start\b') {
        throw '当前 Docker CLI 不支持自动启动，请手动打开 Docker Desktop。'
    }
    & docker desktop start
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop 自动启动失败，请手动打开后重试。'
    }
    $dockerDeadline = [DateTime]::UtcNow.AddSeconds(120)
    while ([DateTime]::UtcNow -lt $dockerDeadline -and -not (Test-DockerEngine)) {
        Start-Sleep -Seconds 2
    }
    if (-not (Test-DockerEngine)) {
        throw '等待 Docker 引擎启动超时，请检查 Docker Desktop。'
    }
}

if ($Build) {
    & docker compose -f $composeFile up -d --build
}
else {
    & docker compose -f $composeFile up -d
}
$composeExitCode = $LASTEXITCODE
if ($composeExitCode -ne 0) {
    & docker compose -f $composeFile ps
    Show-ComposeHelp
    throw "Docker Compose 启动失败，退出码：$composeExitCode。"
}

$ready = $false
$readyDeadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
while ([DateTime]::UtcNow -lt $readyDeadline) {
    $remainingSeconds = [Math]::Ceiling(($readyDeadline - [DateTime]::UtcNow).TotalSeconds)
    if ($remainingSeconds -le 0) {
        break
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec ([Math]::Min(5, [Math]::Max(1, $remainingSeconds)))
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        # 容器启动期间连接失败属于预期状态，继续有限轮询。
    }
    $sleepSeconds = [Math]::Min(3, [Math]::Max(0, ($readyDeadline - [DateTime]::UtcNow).TotalSeconds))
    if ($sleepSeconds -gt 0) {
        Start-Sleep -Seconds $sleepSeconds
    }
}

if (-not $ready) {
    & docker compose -f $composeFile ps
    Show-ComposeHelp
    throw "容器已经启动，但 /api/ready 在 $ReadyTimeoutSeconds 秒内未返回 HTTP 200。"
}

& docker compose -f $composeFile ps
Write-Host "项目已就绪：$appUrl"
try {
    Start-Process $appUrl
}
catch {
    Write-Warning "无法自动打开浏览器，请手动访问 $appUrl"
}
