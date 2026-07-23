[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $projectRoot 'deploy/docker-compose.yml'

function Invoke-DockerCommand {
    [CmdletBinding()]
    param(
        [string[]]$DockerArguments
    )

    # Windows PowerShell 5.1 会在 Stop 模式下把 Docker 的 stderr 提升为终止错误。
    # Docker 的成功与否仍由调用方紧接着读取的 $LASTEXITCODE 决定。
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker @DockerArguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $global:LASTEXITCODE = $exitCode
}

if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw "缺少 Docker Compose 文件：`"$composeFile`"。"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker CLI，请先安装或启动 Docker Desktop。'
}

Invoke-DockerCommand -DockerArguments @('info') *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker 引擎不可用，请先打开 Docker Desktop 后重试；未对任何容器或数据卷做出更改。'
}

Invoke-DockerCommand -DockerArguments @('compose', '-f', $composeFile, 'down', '--remove-orphans')
if ($LASTEXITCODE -ne 0) {
    throw "停止本项目 Docker Compose 服务失败，退出码：$LASTEXITCODE；持久化数据卷未被删除。"
}

Write-Host '项目容器已停止；数据库、uploads 和 Hugging Face 缓存卷均已保留。'
