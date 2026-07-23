# 项目一键启动实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Windows 用户提供可双击或一条 PowerShell 命令运行的完整 Compose 启动与安全停止入口。

**Architecture:** 根目录 CMD 只做入口转发和失败停留，`scripts/` 下 PowerShell 脚本负责全部检查、Compose 调用、ready 等待与浏览器打开。测试沿用现有 `backend/tests/docker/` 的脚本契约风格，最终再用隔离 Compose 项目执行一次真实启动、ready 和停止冒烟。

**Tech Stack:** Windows CMD、Windows PowerShell 5.1 兼容语法、Docker Desktop CLI、Docker Compose、Python 3.12、pytest。

## Global Constraints

- 仅支持 Windows；CMD 使用 `powershell.exe -NoProfile -ExecutionPolicy Bypass`。
- 可用物理内存低于 2 GiB 时不得启动 Docker 重任务。
- 完整拓扑固定使用 `deploy/docker-compose.yml`，应用入口固定为 `http://127.0.0.1:8080`。
- 只有 `http://127.0.0.1:8080/api/ready` 返回 HTTP 200 才能声明启动成功。
- 启动器不创建或修改 `deploy/.env`，不创建管理员，不读取或输出凭据。
- 停止器不得使用 `down -v`、`docker volume rm` 或 `docker desktop stop`。
- 不结束其他进程，不停止其他 Compose 项目，不触碰项目根工作树的用户未提交改动。
- 运行时提示使用中文；脚本文件保持 UTF-8，PowerShell 兼容 Windows PowerShell 5.1。

## File Map

- Create: `启动项目.cmd` — 双击启动入口，只转发到 PowerShell 并保留失败窗口。
- Create: `停止项目.cmd` — 双击停止入口，只转发到 PowerShell 并保留失败窗口。
- Create: `scripts/start-project.ps1` — 内存、配置、Docker、Compose、ready 与浏览器启动的唯一实现。
- Create: `scripts/stop-project.ps1` — 当前 Compose 项目的安全停止实现。
- Create: `backend/tests/docker/test_project_launcher.py` — CMD/PowerShell/README 的聚焦契约测试。
- Modify: `README.md` — 增加一键启动、重建、停止与首次配置说明。

---

### Task 1: CMD 双击入口

**Files:**
- Create: `启动项目.cmd`
- Create: `停止项目.cmd`
- Create: `backend/tests/docker/test_project_launcher.py`

**Interfaces:**
- Consumes: 根目录相对路径和 Windows 自带 `powershell.exe`。
- Produces: `启动项目.cmd -> scripts/start-project.ps1`；`停止项目.cmd -> scripts/stop-project.ps1`，并原样返回 PowerShell 退出码。

- [ ] **Step 1: 写 CMD 入口的失败测试**

创建 `backend/tests/docker/test_project_launcher.py`：

```python
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
START_CMD = PROJECT_ROOT / "启动项目.cmd"
STOP_CMD = PROJECT_ROOT / "停止项目.cmd"
START_SCRIPT = PROJECT_ROOT / "scripts" / "start-project.ps1"
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop-project.ps1"
README = PROJECT_ROOT / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_cmd_launchers_delegate_to_powershell_and_preserve_exit_code() -> None:
    expectations = [
        (START_CMD, r"scripts\start-project.ps1"),
        (STOP_CMD, r"scripts\stop-project.ps1"),
    ]

    for path, target in expectations:
        content = _read(path)
        assert "powershell.exe -NoProfile -ExecutionPolicy Bypass" in content
        assert target in content
        assert 'set "EXIT_CODE=%ERRORLEVEL%"' in content
        assert 'if not "%EXIT_CODE%"=="0"' in content
        assert "pause" in content.lower()
        assert "exit /b %EXIT_CODE%" in content
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py::test_cmd_launchers_delegate_to_powershell_and_preserve_exit_code -q
```

Expected: FAIL，错误为找不到 `启动项目.cmd` 或 `停止项目.cmd`。

- [ ] **Step 3: 实现最小 CMD 入口**

创建 `启动项目.cmd`：

```bat
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo 项目启动失败，请查看上方提示。
    pause
)
exit /b %EXIT_CODE%
```

创建 `停止项目.cmd`：

```bat
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-project.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo 项目停止失败，请查看上方提示。
    pause
)
exit /b %EXIT_CODE%
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py::test_cmd_launchers_delegate_to_powershell_and_preserve_exit_code -q
```

Expected: `1 passed`。

- [ ] **Step 5: 提交 CMD 入口**

```powershell
git add -- 启动项目.cmd 停止项目.cmd backend/tests/docker/test_project_launcher.py
git commit -m "feat: 增加项目双击启停入口"
```

---

### Task 2: PowerShell 启动核心

**Files:**
- Create: `scripts/start-project.ps1`
- Modify: `backend/tests/docker/test_project_launcher.py`

**Interfaces:**
- Consumes: `deploy/.env`、`deploy/docker-compose.yml`、Docker CLI、Docker Desktop、`/api/ready`。
- Produces: `scripts/start-project.ps1 [-Build] [-ReadyTimeoutSeconds 180]`；成功返回 0 并打开首页，失败抛出中文错误并返回非零。

- [ ] **Step 1: 写启动契约的失败测试**

在 `backend/tests/docker/test_project_launcher.py` 追加：

```python
def test_start_script_guards_prerequisites_and_waits_for_api_ready() -> None:
    content = _read(START_SCRIPT)

    for required in (
        "[switch]$Build",
        "ReadyTimeoutSeconds = 180",
        "FreePhysicalMemory",
        "2MB",
        "deploy/.env",
        "docker desktop --help",
        "docker desktop start",
        "docker info",
        "--build",
        "/api/ready",
        "StatusCode -eq 200",
        "Start-Process",
    ):
        assert required in content
    assert "/health" not in content


def test_start_script_does_not_read_or_print_credentials() -> None:
    content = _read(START_SCRIPT).lower()

    for forbidden in (
        "get-content",
        "jwt_secret_key",
        "gateway_shared_secret",
        "chat_api_key",
        "embedding_api_key",
        "initial_admin_password",
    ):
        assert forbidden not in content
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py::test_start_script_guards_prerequisites_and_waits_for_api_ready tests/docker/test_project_launcher.py::test_start_script_does_not_read_or_print_credentials -q
```

Expected: FAIL，错误为找不到 `scripts/start-project.ps1`。

- [ ] **Step 3: 实现启动核心脚本**

创建 `scripts/start-project.ps1`：

```powershell
[CmdletBinding()]
param(
    [switch]$Build,

    [ValidateRange(10, 600)]
    [int]$ReadyTimeoutSeconds = 180,

    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')]
    [string]$ProjectName = 'ai-knowledge-assistant'
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
    Write-Host '  docker compose -p $ProjectName -f deploy/docker-compose.yml ps'
    Write-Host '  docker compose -p $ProjectName -f deploy/docker-compose.yml logs --tail 100 gateway api worker postgres'
}

$availableKiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
if ($availableKiB -lt 2MB) {
    throw '可用物理内存低于 2 GiB，已拒绝启动 Docker 重任务。'
}
if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw '缺少 deploy/docker-compose.yml。'
}
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    Write-Host '缺少 deploy/.env，请先执行：'
    Write-Host '  Copy-Item deploy/.env.example deploy/.env'
    Write-Host '  notepad deploy/.env'
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
    & docker compose -p $ProjectName -f $composeFile up -d --build
}
else {
    & docker compose -p $ProjectName -f $composeFile up -d
}
$composeExitCode = $LASTEXITCODE
if ($composeExitCode -ne 0) {
    & docker compose -p $ProjectName -f $composeFile ps
    Show-ComposeHelp
    throw "Docker Compose 启动失败，退出码：$composeExitCode。"
}

$ready = $false
$readyDeadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
while ([DateTime]::UtcNow -lt $readyDeadline) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        # 容器启动期间连接失败属于预期状态，继续有限轮询。
    }
    Start-Sleep -Seconds 3
}

if (-not $ready) {
    & docker compose -p $ProjectName -f $composeFile ps
    Show-ComposeHelp
    throw "容器已经启动，但 /api/ready 在 $ReadyTimeoutSeconds 秒内未返回 HTTP 200。"
}

& docker compose -p $ProjectName -f $composeFile ps
Write-Host "项目已就绪：$appUrl"
try {
    Start-Process $appUrl
}
catch {
    Write-Warning "无法自动打开浏览器，请手工访问 $appUrl"
}
```

- [ ] **Step 4: 运行启动脚本聚焦测试并确认通过**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py -q
```

Expected: 当前已有 3 项测试全部通过。

- [ ] **Step 5: 解析 PowerShell 语法**

Run:

```powershell
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path scripts/start-project.ps1),
    [ref]$null,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) { $errors; exit 1 }
```

Expected: exit code 0，无解析错误。

- [ ] **Step 6: 提交启动核心**

```powershell
git add -- scripts/start-project.ps1 backend/tests/docker/test_project_launcher.py
git commit -m "feat: 增加项目一键启动检查"
```

---

### Task 3: PowerShell 安全停止核心

**Files:**
- Create: `scripts/stop-project.ps1`
- Modify: `backend/tests/docker/test_project_launcher.py`

**Interfaces:**
- Consumes: `deploy/docker-compose.yml` 和可访问的 Docker 引擎。
- Produces: `scripts/stop-project.ps1`；只执行当前 Compose `down --remove-orphans`，成功返回 0。

- [ ] **Step 1: 写停止安全边界的失败测试**

在 `backend/tests/docker/test_project_launcher.py` 追加：

```python
def test_stop_script_only_stops_the_current_compose_project() -> None:
    content = _read(STOP_SCRIPT)
    lowered = content.lower()

    assert "deploy/docker-compose.yml" in content
    assert "docker info" in content
    assert "down --remove-orphans" in content
    for forbidden in (
        "down -v",
        "docker volume",
        "volume rm",
        "docker desktop stop",
        "remove-item",
    ):
        assert forbidden not in lowered


def test_launchers_do_not_contain_credentials() -> None:
    for path in (START_CMD, STOP_CMD, START_SCRIPT, STOP_SCRIPT):
        content = _read(path).lower()
        for forbidden in (
            "jwt_secret_key",
            "gateway_shared_secret",
            "chat_api_key",
            "embedding_api_key",
            "initial_admin_password",
        ):
            assert forbidden not in content
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py::test_stop_script_only_stops_the_current_compose_project tests/docker/test_project_launcher.py::test_launchers_do_not_contain_credentials -q
```

Expected: FAIL，错误为找不到 `scripts/stop-project.ps1`。

- [ ] **Step 3: 实现停止核心脚本**

创建 `scripts/stop-project.ps1`：

```powershell
[CmdletBinding()]
param(
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')]
    [string]$ProjectName = 'ai-knowledge-assistant'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $projectRoot 'deploy/docker-compose.yml'

if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw '缺少 deploy/docker-compose.yml。'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker CLI。'
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker 引擎不可用，请先打开 Docker Desktop。'
}

& docker compose -p $ProjectName -f $composeFile down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw '停止本项目 Docker Compose 失败。'
}

Write-Host '项目容器已停止；数据库、uploads 和 Hugging Face 缓存卷均已保留。'
```

- [ ] **Step 4: 运行聚焦测试并确认通过**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py -q
```

Expected: 当前 5 项测试全部通过。

- [ ] **Step 5: 解析两个 PowerShell 文件语法**

Run:

```powershell
$scripts = @('scripts/start-project.ps1', 'scripts/stop-project.ps1')
foreach ($script in $scripts) {
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $script), [ref]$null, [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) { $errors; exit 1 }
}
```

Expected: exit code 0，无解析错误。

- [ ] **Step 6: 提交停止核心**

```powershell
git add -- scripts/stop-project.ps1 backend/tests/docker/test_project_launcher.py
git commit -m "feat: 增加项目安全停止入口"
```

---

### Task 4: README 简单使用入口

**Files:**
- Modify: `README.md`
- Modify: `backend/tests/docker/test_project_launcher.py`

**Interfaces:**
- Consumes: 四个已经实现的启动/停止文件。
- Produces: README 中可复制的一键启动、显式重建、停止和首次配置说明。

- [ ] **Step 1: 写 README 入口的失败测试**

在 `backend/tests/docker/test_project_launcher.py` 追加：

```python
def test_readme_documents_simple_project_launchers() -> None:
    content = _read(README)

    for required in (
        "## 一键启动",
        "启动项目.cmd",
        r".\scripts\start-project.ps1",
        r".\scripts\start-project.ps1 -Build",
        "停止项目.cmd",
        "Copy-Item deploy/.env.example deploy/.env",
    ):
        assert required in content
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py::test_readme_documents_simple_project_launchers -q
```

Expected: FAIL，缺少 `## 一键启动`。

- [ ] **Step 3: 在 README 的“本地启动”前增加一键启动章节**

插入以下完整内容：

````markdown
## 一键启动

Windows 用户推荐使用完整 Docker 演示模式。第一次运行前只需手工创建本地配置：

```powershell
Copy-Item deploy/.env.example deploy/.env
notepad deploy/.env
```

本机 HTTP 演示需要将 `APP_ENV`、Cookie 和 Origin 配置为 README 下方完整容器演示章节给出的本机值；`deploy/.env` 已被 Git 忽略，不能提交真实密钥。

配置完成后可以直接双击根目录的 `启动项目.cmd`。也可以使用 PowerShell：

```powershell
.\scripts\start-project.ps1
```

代码或镜像定义变化后显式重建：

```powershell
.\scripts\start-project.ps1 -Build
```

启动器会检查内存、Docker 和 `deploy/.env`，等待 `/api/ready` 返回 HTTP 200 后打开 <http://127.0.0.1:8080>。它不会自动创建配置或管理员；首次管理员仍按完整容器演示章节执行安全创建命令。

停止时双击 `停止项目.cmd`，或执行：

```powershell
.\scripts\stop-project.ps1
```

停止器只停止本项目容器，不删除数据库、上传文件和模型缓存卷。不要把 `docker compose down -v` 当作普通停止命令。
````

- [ ] **Step 4: 运行 README 测试和全部启动器聚焦测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py -q
```

Expected: `6 passed`。

- [ ] **Step 5: 提交文档入口**

```powershell
git add -- README.md backend/tests/docker/test_project_launcher.py
git commit -m "docs: 补充项目一键启动说明"
```

---

### Task 5: 最终验证、隔离真实冒烟与推送

**Files:**
- Verify only: `启动项目.cmd`
- Verify only: `停止项目.cmd`
- Verify only: `scripts/start-project.ps1`
- Verify only: `scripts/stop-project.ps1`
- Verify only: `backend/tests/docker/test_project_launcher.py`
- Verify only: `README.md`

**Interfaces:**
- Consumes: Task 1～4 的完整交付物。
- Produces: 新鲜测试、解析、真实 ready、停止保卷、Git 状态和远程 PR 更新证据。

- [ ] **Step 1: 检查可用物理内存和 owner 边界**

Run:

```powershell
$available = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1KB
if ($available -lt 2GB) { throw '可用物理内存低于 2 GiB，停止真实冒烟。' }
docker ps -a --format '{{.Names}}'
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
git status --short
```

Expected: 内存不少于 2 GiB；没有不属于本任务且占用 8080 的进程或容器；工作树只包含本计划范围内修改。

- [ ] **Step 2: 运行全部聚焦测试和脚本解析**

Run:

```powershell
Set-Location backend
uv run pytest tests/docker/test_project_launcher.py -q
Set-Location ..
$scripts = @('scripts/start-project.ps1', 'scripts/stop-project.ps1')
foreach ($script in $scripts) {
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $script), [ref]$null, [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) { $errors; exit 1 }
}
git diff --check
```

Expected: `6 passed`，PowerShell 无语法错误，`git diff --check` 返回 0。

- [ ] **Step 3: 准备隔离真实冒烟配置**

若 `deploy/.env` 不存在，使用 `apply_patch` 临时创建被 Git 忽略的文件，内容固定为：

```dotenv
JWT_SECRET_KEY=launcher-smoke-jwt-secret-32-characters
APP_ENV=development
REFRESH_COOKIE_SECURE=false
GATEWAY_SHARED_SECRET=launcher-smoke-gateway-secret-32-chars
TRUSTED_GATEWAY_NETWORKS=["172.28.0.10/32"]
TRUSTED_ORIGINS=["http://127.0.0.1:8080"]
EMBEDDING_API_KEY=
CHAT_API_KEY=
BACKUP_ROOT=../backups
```

记录该文件是否由本任务创建。若文件原本存在，不读取、不修改、不删除。

- [ ] **Step 4: 使用隔离 Compose 项目真实启动并验证 ready**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-project.ps1 -ProjectName stage5launcher
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$ready = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/ready
if ($ready.StatusCode -ne 200) { throw '真实启动后 /api/ready 未返回 HTTP 200。' }
docker compose -p stage5launcher -f deploy/docker-compose.yml ps
```

Expected: 启动脚本返回 0，gateway/API/Worker/PostgreSQL healthy，`/api/ready` 为 HTTP 200。

- [ ] **Step 5: 使用停止器停止并验证卷保留**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/stop-project.ps1 -ProjectName stage5launcher
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$remainingContainers = @(docker ps -a --filter 'label=com.docker.compose.project=stage5launcher' --format '{{.Names}}')
if ($remainingContainers.Count -ne 0) { throw '隔离冒烟容器未全部停止。' }
$remainingVolumes = @(docker volume ls --filter 'label=com.docker.compose.project=stage5launcher' --format '{{.Name}}')
if ($remainingVolumes.Count -lt 3) { throw '停止器未保留预期的三个持久卷。' }
```

Expected: 隔离容器为 0，三个持久卷仍存在。

- [ ] **Step 6: 清理本任务隔离资源**

逐个读取 `stage5launcher` 卷标签并确认 `com.docker.compose.project=stage5launcher` 后，显式删除以下隔离卷：

```text
stage5launcher_knowledge_postgres_data
stage5launcher_knowledge_uploads
stage5launcher_knowledge_hf_cache
```

无需设置或移除 `COMPOSE_PROJECT_NAME`。若 `deploy/.env` 是本任务临时创建的，使用 `apply_patch` 删除；若原本存在则保持不变。不得删除任何其他卷或停止其他进程。

- [ ] **Step 7: 最终 Git 和远程状态验证**

Run:

```powershell
git status --short
git log --oneline -8
git push origin codex/stage5-task5-mobile-conversations
gh pr view 12 --repo 417647876-prog/AI-Knowledge-Assistant --json url,isDraft,headRefOid
```

Expected: 工作树干净；分支推送成功；草稿 PR #12 的 `headRefOid` 等于本地 HEAD。
