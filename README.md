# AI Knowledge Assistant

面向企业私有文档的 RAG 知识库后端，也是一个面向 C# 开发者的 Python AI 工程学习项目。

## 当前进度

- 阶段 1A：FastAPI 基础、统一错误、PostgreSQL + pgvector、Alembic。
- 阶段 1B：知识库 API、安全上传、PDF/DOCX/XLSX/Markdown/TXT 解析。
- 阶段 1C：文本清洗、中文递归切片、事务性向量入库。
- 阶段 1D：本地 BGE Small、pgvector 余弦检索、DeepSeek 问答与结构化引用。
- 阶段 1E：自动化测试、Docker 冒烟脚本、中文使用文档与演示材料。
- 阶段 2A：Vue 3 单页工作台、上传与问答界面。
- 阶段 2B：管理员账号、JWT 登录、可撤销刷新会话、角色权限和知识库隔离。
- 阶段 2C：持久化文档列表、处理状态恢复、失败重处理和安全删除。
- 阶段 2D：SSE 流式回答、浏览器会话历史、多轮问题改写和检索耗时事件。
- 阶段 3A：30 条中文评估数据、Recall/MRR/引用/拒答指标和纯向量基线 CLI。
- 阶段 3B：PostgreSQL 中文关键词检索、向量/关键词 RRF 融合与质量验收。
- 阶段 3C：本地 BGE Reranker、接受门校准与生产安全回退；MRR 质量门由用户明确豁免。
- 阶段 3D：选择性多轮问题改写、精确失败回退、SSE 展示与真实质量门验收。
- 阶段 3E：同快照四模式综合验收、12 个质量门、原子 manifest 和脱敏中文报告。
- 阶段 4A～4B：PostgreSQL 持久化任务、租约/心跳/重试、独立 Worker，以及重启后的任务恢复。
- 阶段 4C～4D：严格所属人隔离、临时只读支持授权、回收站、服务端会话与用量、额度、限速、日志、指标、审计和脱敏运营接口。
- 阶段 4E～4F：gateway 同源容器编排、持久卷与重启恢复，以及后端、前端、数据库和阶段 3 质量全量回归。

当前闭环为：管理员初始化账号 → 用户登录 → 创建自己的知识库 → 上传文档 → 持久化任务入队与 Worker 处理 → 检索问答 → 返回可追溯引用 → 会话、用量和反馈留存。系统不提供公开注册；普通业务接口始终按当前用户隔离，管理员也不能绕过所属人读取他人内容，只能通过限知识库、限管理员、只读、可撤销且会过期的支持授权排障。

## 阶段 3A 纯向量评估

阶段 3A 使用固定的 30 条中文案例评估关键词、语义、拒答、多轮和干扰问题。先准备已导入
`backend/tests/fixtures/documents/01-` 至 `05-` 测试资料的知识库，再执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EVALUATION_KNOWLEDGE_BASE_ID = "知识库 UUID"
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode vector `
  --output reports/stage3a-vector-baseline.json
Remove-Item Env:EVALUATION_KNOWLEDGE_BASE_ID
```

报告记录 Recall@5、MRR@5、引用命中率、拒答准确率和检索延迟，但不会记录数据库连接串或
API Key。`backend/reports/*.json` 是本地运行产物，默认不提交 Git；执行状态和基线指标见
[阶段 3 执行进度](docs/实施计划/阶段3执行进度.md)。

## 阶段 3B 混合检索状态

阶段 3B 已实现确定性中文 Token、PostgreSQL `tsvector` 与 GIN 索引、关键词 Retriever、
RRF 融合，以及 `vector`/`hybrid` 可回退配置。默认仍使用纯向量检索；显式设置
`RAG_RETRIEVAL_MODE=hybrid` 才启用混合检索。两个数据库 Retriever 共用请求级
`AsyncSession`，因此采用顺序双路查询后再融合，避免同一会话并发执行 SQL。

固定 30 条数据的本地验收结果如下：

| 模式 | Recall@5 | MRR@5 | 引用命中率 | 拒答准确率 |
|---|---:|---:|---:|---:|
| vector | 83.33% | 83.33% | 83.33% | 83.33% |
| hybrid | 93.33% | 93.33% | 93.33% | 93.33% |

关键词分类的纯向量 Recall@5 已经是 100%，因此 3B 使用上限感知质量门：混合检索的关键词
Recall@5 必须达到 `min(100%, 纯向量关键词 Recall@5 + 10 个百分点)`，同时总体 Recall@5、
引用命中率和拒答准确率均不得低于纯向量。混合检索保持关键词 Recall@5 为 100%，四项总体
指标均由 83.33% 提升到 93.33%，阶段 3B 已通过验收。

## 阶段 3C 重排序验收与收尾状态

评估 CLI 的 `--mode rerank` 会使用 hybrid 检索取得候选，启用本地 BGE 重排序，并关闭
fallback，避免模型失败时静默生成非重排报告。固定 30 条数据的 CPU 实测使用
`candidate_k=20`、`BAAI/bge-reranker-base`：

rerank 报告通过单次最终链路计算：embedding → hybrid candidate_k → BGE → Top K；检索文件、
Recall/MRR、引用都来自最终重排结果，CPU P50/P95 覆盖 embedding、候选检索与 reranker，
不再使用独立的 raw hybrid Top5 计时。

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EVALUATION_KNOWLEDGE_BASE_ID = "知识库 UUID"
$env:EMBEDDING_DEVICE = "cpu"
$env:RAG_RERANKER_DEVICE = "cpu"
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode rerank `
  --output reports/stage3c-rerank.json
```

| 模式 | MRR@5 | 引用命中率 | CPU P50 | CPU P95 |
|---|---:|---:|---:|---:|
| 3B hybrid | 93.33% | 93.33% | 15.20 ms | 18.12 ms |
| 3C rerank | 93.33% | 93.33% | 62.77 ms | 106.07 ms |

MRR@5 相对提升为 0.00%，未达到至少 5% 的质量门；引用命中率未下降。28 条案例在 3B
已经排名第一，`refusal-03` 的单个误召回无法仅靠排序剔除，`multi-turn-06` 则没有候选可供
重排。

2026-07-15，用户明确决定不再继续追逐该质量门，并接受当前指标风险，将 3C 按
“已收尾（质量门豁免）”处理。该决策不表示质量门通过，也没有修改测试、质量门或固定评估
数据。该风险记录继续保留，但不再阻塞后续阶段；阶段 3D 已于 2026-07-15 独立完成验收。

### 3C.1 Reranker 接受门与离线校准

Reranker 接受门默认关闭，即 `RAG_RERANKER_MIN_SCORE` 未设置时保持现有重排行为。该阈值
属于 `BAAI/bge-reranker-base` 的原始相关性分数，不是概率，也不能与向量检索的
`RAG_SCORE_THRESHOLD` 混用。应使用独立校准集在目标模型和设备上离线生成建议值：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
uv run python -m scripts.calibrate_reranker `
  --dataset tests/fixtures/evaluation/stage3c-reranker-calibration.jsonl `
  --model BAAI/bge-reranker-base `
  --device cpu `
  --batch-size 16 `
  --output reports/stage3c1-reranker-calibration.json
```

只有校准报告满足负样本错误接受率为 0、正样本接受率至少 0.8，并给出有限的
`recommended_min_score` 时，才应显式设置 `RAG_RERANKER_MIN_SCORE`。Provider 调用失败仍按
`RAG_RERANKER_ALLOW_FALLBACK` 处理；Provider 成功但候选低于接受门时不会 fallback 到原候选。
若全部候选被拒绝，普通问答、流式问答和评估链路都会复用现有安全拒答，不调用回答模型。

## 阶段 3D 选择性问题改写验收

`--mode rewrite` 复用 3C 的 hybrid 候选检索和本地 BGE Reranker，仅对带历史且命中选择性
规则的 `multi_turn` 案例调用问题改写。改写后的独立问题只用于 Embedding、Retriever 和
Reranker；最终回答 Prompt 仍使用用户原问题。`QUESTION_REWRITE_ERROR` 会安全回退到原问题，
其他错误保持原错误链路，不会被吞掉。

未改写与改写报告必须使用同一数据集、Top K 和安全环境摘要，并通过同一条最终重排链路计算
Recall/MRR/引用和检索延迟：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EVALUATION_KNOWLEDGE_BASE_ID = "知识库 UUID"
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode rerank `
  --output reports/stage3d-no-rewrite.json
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode rewrite `
  --output reports/stage3d-rewrite.json
Remove-Item Env:EVALUATION_KNOWLEDGE_BASE_ID
```

2026-07-15 的 30 条真实评测结果如下；报告是 `backend/reports` 下的 Git 忽略本地产物：

| 模式 | 总体 Recall@5 | MRR@5 | 多轮 Recall@5 | 引用命中率 | 拒答准确率 | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| no-rewrite（rerank） | 93.33% | 93.33% | 83.33% | 96.67% | 93.33% | 124.76 ms | 1140.21 ms |
| rewrite | 96.67% | 96.67% | 100% | 96.67% | 96.67% | 82.89 ms | 249.08 ms |

多轮质量门使用 `ceiling_aware_target(83.33%, 15 个百分点)` 得到 98.33%，rewrite 实测 100%，
提升 16.67 个百分点并通过。rewrite 的引用命中率和拒答准确率也都不低于 3C 正式报告的
93.33%。延迟是本次真实 CPU 运行的观测值，不作为 3D 通过条件；报告不会记录数据库连接串或
API Key。阶段 3D 已完成；最终综合指标和阶段 3 收口结论见下一节。

## 阶段 3 RAG 质量验收

阶段 3E 用同一份 30 条数据、同一个 run ID 和同一个知识库快照，按
`vector → hybrid → rerank → rewrite` 顺序生成四份 schema 1.1 报告，再自动计算 3A～3E 的
12 个质量门。2026-07-15 的最终 rewrite 指标为 Recall@5 96.67%、引用命中率 100.00%、
拒答准确率 96.67%，三项绝对门均通过。

3C 的 MRR 相对提升仍是 0.00%，没有达到至少 5% 的原质量门，因此结论始终是
“质量门未通过、已获风险豁免”，不等于技术通过。豁免只在 MRR 不负增长且引用不下降时适用。
最终推荐 `rewrite`；当本地重排或问题改写链路需要故障隔离时，回退到 `vector`。

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:STAGE3_KNOWLEDGE_BASE_ID = "准备好的评估知识库 UUID"
uv run python -m scripts.accept_stage3 `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:STAGE3_KNOWLEDGE_BASE_ID `
  --policy config/evaluation/stage3-quality-policy.json `
  --reports-dir reports `
  --markdown-output ..\docs\验收与演示\阶段3质量验收报告.md
$acceptanceExitCode = $LASTEXITCODE
Remove-Item Env:STAGE3_KNOWLEDGE_BASE_ID
Write-Output "accept_stage3 exit code: $acceptanceExitCode"
```

退出码 `0` 表示所有非豁免质量门通过，`1` 表示输入、环境、快照或输出错误，`2` 表示存在未豁免
的质量门失败。质量失败仍会保留产物用于诊断，不应修改固定数据集或策略阈值制造通过。

四份 JSON 和 `stage3e-manifest.json` 保存在被 Git 忽略的 `backend/reports`，只作为本地原始证据；
可提交的公开材料是[阶段 3 质量验收报告](docs/验收与演示/阶段3质量验收报告.md)和
[阶段 3 验证与演示](docs/验收与演示/阶段3验证与演示.md)。manifest 最后写入，并记录五个公开产物的
SHA-256，便于判断一组报告是否完整且同源。

## 本地启动

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.dev.yml up -d
Set-Location backend
uv sync --dev
$env:APP_ENV = "development"
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

`create_admin` 会在终端中安全提示两次输入密码；也可仅在当前 PowerShell 进程设置 `INITIAL_ADMIN_PASSWORD`，执行后立即删除该环境变量。密码和 DeepSeek Key 只放在本地环境中，不要写入仓库、命令示例或验收报告。

另开一个 PowerShell 终端启动前端：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

首次使用本地 Embedding 时会自动下载 `BAAI/bge-small-zh-v1.5`。如只做离线验收，可按下文使用 Fake Provider。

启动后访问：

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>

## 阶段 2B 前端与认证

前端基于 Vue 3、TypeScript、Vite、Pinia、Vue Router 和 Element Plus。Access Token 只保存在页面内存中；长期 Refresh Token 只由浏览器通过 HttpOnly Cookie 发送。刷新页面时前端使用 Cookie 恢复会话，退出、账号停用或密码重置会撤销长期会话。

从仓库根目录打开两个 PowerShell 窗口。终端 1 启动数据库和 FastAPI：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.dev.yml up -d
Set-Location backend
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

终端 2 安装依赖并启动前端：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173> 后先登录。管理员可在“用户管理”中创建、启停账号、切换角色和重置密码；普通用户只能看到自己的知识库。工作台会重新加载历史文档，并恢复处理中任务的状态轮询；失败文档可重新处理，删除会要求确认且不允许删除处理中任务。

更完整的初始化、安全重置、权限验收和验证命令见 [阶段 2B 验证与演示](docs/验收与演示/阶段2B验证与演示.md)、[前端使用说明](frontend/README.md)、[阶段 2C 文档管理计划](docs/实施计划/2026-07-14-阶段2C文档管理.md) 和 [阶段 2C 验证与演示](docs/验收与演示/阶段2C验证与演示.md)。

## 离线冒烟验证

冒烟测试会实际调用已启动的 API，验证“创建知识库 → 上传 TXT → 后台入库 → 问答 → 引用”全链路。
为避免下载模型或调用付费模型，请另开一个 PowerShell 窗口，使用 Fake Provider 启动服务：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EMBEDDING_PROVIDER = "fake"
$env:CHAT_PROVIDER = "fake"
$env:RAG_SCORE_THRESHOLD = "-1"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

待服务启动后，在第二个窗口执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:SMOKE_USERNAME = "admin"
$securePassword = Read-Host "冒烟测试账号密码" -AsSecureString
$env:SMOKE_PASSWORD = [System.Net.NetworkCredential]::new("", $securePassword).Password
uv run python -m scripts.smoke_test
Remove-Item Env:SMOKE_PASSWORD
Remove-Variable securePassword
```

脚本先验证公开健康接口，再登录并验证 `/auth/me`，随后创建临时知识库、上传、轮询、问答，最后退出。账号密码只从当前进程环境读取；脚本不会输出密码、Access Token 或 Refresh Token。失败时返回非零退出码并显示脱敏的状态码、错误码和 request ID。

## 阶段 4 完整容器演示

开发模式只需要 PostgreSQL 时使用 `deploy/docker-compose.dev.yml`；它会把数据库端口暴露给本机后端。完整演示使用 `deploy/docker-compose.yml`，由 gateway 同源提供前端和 `/api`，API、Worker、PostgreSQL 与内部指标均不直接映射到宿主机。

先复制本地配置并手工替换占位值，`deploy/.env` 已被 Git 忽略，禁止提交：

```powershell
Set-Location (git rev-parse --show-toplevel)
Copy-Item deploy/.env.example deploy/.env
notepad deploy/.env
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml exec api python -m scripts.create_admin --username stage4-admin
```

本机 HTTP 演示仅可在回环地址使用 `APP_ENV=development`、`REFRESH_COOKIE_SECURE=false` 和精确的 `TRUSTED_ORIGINS=["http://127.0.0.1:8080"]`。`JWT_SECRET_KEY` 与 `GATEWAY_SHARED_SECRET` 必须使用互不相同的随机高强度值；模型 Key 只放在本地环境文件。正式 HTTPS 环境必须改回 `APP_ENV=production`、`REFRESH_COOKIE_SECURE=true` 和实际 HTTPS Origin。

完整演示只访问以下宿主入口：

- 页面与 gateway 健康检查：<http://127.0.0.1:8080/>、<http://127.0.0.1:8080/health>
- 经过 gateway 的后端就绪检查：<http://127.0.0.1:8080/api/ready>

`create_admin` 会在容器终端中安全提示输入密码。验收凭据只通过当前 PowerShell 环境传入，脚本不会输出密码、Token、API Key、文档正文、问题全文或回答全文：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:STAGE4_VERIFY_USERNAME = "stage4-admin"
$securePassword = Read-Host "阶段4测试账号密码" -AsSecureString
$env:STAGE4_VERIFY_PASSWORD = [System.Net.NetworkCredential]::new("", $securePassword).Password
uv run python -m scripts.verify_stage4_compose --base-url http://127.0.0.1:8080
Remove-Item Env:STAGE4_VERIFY_PASSWORD
Remove-Item Env:STAGE4_VERIFY_USERNAME
Remove-Variable securePassword
```

普通测试不会启动、停止或重启容器。真实重启测试必须在完整 Compose 已健康且物理可用内存不少于 2 GiB 时，显式设置 `RUN_DOCKER_TESTS=1` 并单独串行执行；它会重启 Worker、API、PostgreSQL 和 gateway。

```powershell
$env:RUN_DOCKER_TESTS = "1"
uv run pytest tests/docker/test_container_restart_recovery.py -q
Remove-Item Env:RUN_DOCKER_TESTS
```

停止但保留数据卷使用：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml down
docker volume ls
```

逻辑卷为 `knowledge_postgres_data`、`knowledge_uploads` 和 `knowledge_hf_cache`，Docker 实际名称会带 Compose 项目前缀。只有确认不再需要数据库、上传文件和模型缓存时才可执行 `down -v`；该命令会删除数据，不能作为普通停止命令。

完整命令、测试统计、隐私矩阵、恢复证据和已接受边界见[阶段 4 验证与演示](docs/验收与演示/阶段4验证与演示.md)。阶段 4 已完成，当前下一目标是阶段 5 的手机 H5、受控 HTTPS 访问与备份恢复闭环。

## 部署与手机访问边界

本阶段只验证本机开发运行，不代表已经具备公网生产安全。生产部署必须使用随机高强度 `JWT_SECRET_KEY`、`REFRESH_COOKIE_SECURE=true`，由同一 HTTPS 域名提供前端和 `/api` 反向代理，并严格配置 `TRUSTED_ORIGINS`。PostgreSQL、Vite 和 Uvicorn 内部端口不能直接暴露公网。

反向代理还必须提供两类入口保护：对登录接口按来源和账号维度做限速、限制并发连接；对文档上传设置真实请求体硬上限。例如 Nginx 的 `client_max_body_size` 应按“20 MB 文件 + multipart 编码开销”配置，不能只写 20 MB。应用内同时限制完整 multipart 请求体和文件内容大小，属于第二层防护；密码 128 字符上限和 Argon2 线程池隔离也不能替代代理层的抗 DoS、连接数与请求速率限制。

手机在同一局域网访问也需要额外绑定非回环地址、防火墙规则和可信 Origin；这些操作会扩大网络暴露面，不属于本阶段默认启动步骤。不要把开发命令直接用于公网。

## 文档导航

| 目录 | 内容 |
|---|---|
| `docs/设计/` | 总体设计、阶段设计和后续路线图 |
| `docs/实施计划/` | 各阶段的详细实施步骤，保留历史记录 |
| `docs/验收与演示/` | 本地运行、验收与面试演示说明 |
| `docs/学习记录/` | 学习笔记和 C# 转 AI 的过程记录 |
| `docs/课程材料/` | RAG 架构图等辅助学习材料 |

## 学习资料

- [项目学习笔记](docs/学习记录/学习笔记.md)
- [RAG 后端总体设计](docs/设计/2026-07-10-RAG后端总体设计.md)
- [阶段 1B 文档解析设计](docs/设计/2026-07-12-阶段1B文档解析设计.md)
- [阶段 1C 向量入库设计](docs/设计/2026-07-13-阶段1C向量入库设计.md)
- [阶段 1D 检索问答设计](docs/设计/2026-07-13-阶段1D检索问答设计.md)
- [阶段 1E 验证与演示](docs/验收与演示/阶段1E验证与演示.md)
- [阶段 2B 验证与演示](docs/验收与演示/阶段2B验证与演示.md)
- [面试演示作品路线图](docs/设计/2026-07-14-面试演示作品路线图.md)
- [项目任务与资源](项目任务与资源.md)
- [项目笔记](项目笔记.md)
- [参考资料](参考资料.md)

## 1D API

```http
POST /api/v1/knowledge-bases/{knowledge_base_id}/questions
Content-Type: application/json

{
  "question": "员工入职满一年有多少天年假？",
  "top_k": 5
}
```

向量维度从 1536 调整为 512 后，历史文档需要重新生成向量：

```http
POST /api/v1/documents/{document_id}/reprocess
```

## API 调用说明

除 `/health`、`/ready`、OpenAPI 和认证入口外，业务 API 都需要 `Authorization: Bearer <Access Token>`。普通用户直接访问他人的知识库、文档或问答资源返回 404；管理员接口要求管理员角色。完整且不在终端输出令牌的调用方式请使用前端或认证版 smoke 脚本。

## 验证

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
uv run pytest -v
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

数据库集成测试会校验“最后一个管理员”和全局列表等约束，必须使用空的临时数据库，不能直接复用包含演示数据的开发库：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
$testDatabase = "knowledge_integration_test"
docker compose -f deploy/docker-compose.yml exec -T postgres dropdb --if-exists --force -U knowledge $testDatabase
docker compose -f deploy/docker-compose.yml exec -T postgres createdb -U knowledge $testDatabase
try {
  $env:DATABASE_URL = "postgresql+psycopg://knowledge:knowledge@localhost:5432/$testDatabase"
  Set-Location backend
  uv run alembic upgrade head
  $env:RUN_DATABASE_TESTS = "1"
  uv run pytest tests/integration -q
}
finally {
  Remove-Item Env:RUN_DATABASE_TESTS -ErrorAction SilentlyContinue
  Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
  Set-Location (git rev-parse --show-toplevel)
  docker compose -f deploy/docker-compose.yml exec -T postgres dropdb --if-exists --force -U knowledge $testDatabase
}
```
