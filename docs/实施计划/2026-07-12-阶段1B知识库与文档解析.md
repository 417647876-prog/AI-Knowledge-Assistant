# 阶段 1B：知识库与多格式文档解析实施计划

> **给执行者：** 每个任务严格遵循 Red-Green-Refactor；先运行失败测试，再写最小实现并完成一次独立提交。

**目标：** 提供知识库创建、受控文件上传、五种格式解析以及文档/任务状态查询。

**架构：** `app/knowledge` 负责 DTO、仓储、服务和解析器；路由仅转换 HTTP 输入输出。解析器返回统一 `ParsedSection`，上传服务负责安全校验、哈希、UUID 落盘和业务记录持久化。

**技术栈：** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL、pytest、PyMuPDF、python-docx、openpyxl。

## 全局约束

- 支持 `.pdf`、`.docx`、`.xlsx`、`.md`、`.txt`；单文件最大 20 MB。
- 不用原始文件名拼接服务器路径；保存名必须为 UUID。
- 同一知识库以 SHA-256 去重；错误沿用 `AppError` 的安全响应协议。
- 本阶段不创建 `DocumentChunk`，不调用 Embedding 或 Chat 模型。
- 数据库集成测试使用 `RUN_DATABASE_TESTS=1` 和本地 Docker PostgreSQL。

---

### Task 1：增加解析依赖与统一契约

**文件：**
- 修改：`backend/pyproject.toml`、`backend/uv.lock`
- 新建：`backend/app/knowledge/__init__.py`、`backend/app/knowledge/schemas.py`
- 新建：`backend/app/knowledge/parsers/__init__.py`、`base.py`、`registry.py`
- 测试：`backend/tests/unit/test_parser_contract.py`

- [ ] 编写失败测试：断言 `ParsedSection` 能携带正文、页码、工作表、起始行、标题和元数据；注册表能按扩展名返回解析器、未知格式抛出 `UNSUPPORTED_FILE_TYPE`。
- [ ] 运行 `uv run pytest tests/unit/test_parser_contract.py -v`，确认因模块缺失失败。
- [ ] 在依赖中增加 `pymupdf`、`python-docx`、`openpyxl`，执行 `uv sync --dev`。
- [ ] 实现不可变的 `ParsedSection` DTO、`DocumentParser` Protocol 和 `ParserRegistry`；注册表只接受小写规范化扩展名。
- [ ] 运行目标测试、`uv run ruff check app tests`，确认通过。
- [ ] 提交：`feat: 添加文档解析器契约`。

### Task 2：实现 TXT 与 Markdown 解析器

**文件：**
- 新建：`backend/app/knowledge/parsers/text.py`、`markdown.py`
- 测试：`backend/tests/unit/test_text_parser.py`、`test_markdown_parser.py`
- 新建夹具：`backend/tests/fixtures/sample.txt`、`sample.md`

- [ ] 编写失败测试：TXT 同时接受 UTF-8 与 UTF-8 BOM，非法编码返回 `DOCUMENT_CONTENT_EMPTY`；Markdown 按 `#` 至 `######` 标题生成段落并填充 `section_title`。
- [ ] 运行两组测试并确认失败。
- [ ] 实现仅读取 UTF-8/UTF-8 BOM 的 TXT 解析器；实现 Markdown 标题累积解析器，空白正文不产生段落。
- [ ] 将两个解析器注册到 `.txt`、`.md`，运行目标测试及全部单元测试。
- [ ] 提交：`feat: 支持文本与 Markdown 解析`。

### Task 3：实现 PDF、Word、Excel 解析器

**文件：**
- 新建：`backend/app/knowledge/parsers/pdf.py`、`word.py`、`excel.py`
- 测试：`backend/tests/unit/test_pdf_parser.py`、`test_word_parser.py`、`test_excel_parser.py`
- 新建夹具：`backend/tests/fixtures/sample.pdf`、`sample.docx`、`sample.xlsx`

- [ ] 编写失败测试：PDF 每页保留 `page_number` 且无文字返回 `OCR_REQUIRED`；Word 依原顺序输出段落和表格；Excel 保留 `sheet_name`、表头与 `row_start`。
- [ ] 分别运行三组测试，确认每组因实现缺失失败。
- [ ] 用 PyMuPDF 按页提取；用 python-docx 遍历段落/表格顺序；用 openpyxl 按工作表转换“表头：值”的可读文本。
- [ ] 注册 `.pdf`、`.docx`、`.xlsx`；运行全部解析器测试和 Ruff。
- [ ] 提交：`feat: 支持办公文档解析`。

### Task 4：知识库仓储、服务与 API

**文件：**
- 新建：`backend/app/knowledge/repositories.py`、`services.py`
- 新建：`backend/app/api/v1/knowledge_bases.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/unit/test_knowledge_base_api.py`

- [ ] 写失败测试：`POST /api/v1/knowledge-bases` 验证名称并返回 201；`GET /api/v1/knowledge-bases` 返回创建顺序；仓储会话使用 FastAPI 依赖覆盖。
- [ ] 运行测试确认失败。
- [ ] 实现 `KnowledgeBaseRepository` 和 `KnowledgeBaseService`；添加创建/列表 DTO 和 `/api/v1` 路由。
- [ ] 使用真实数据库会话的集成测试验证持久化；运行单元测试与 Ruff。
- [ ] 提交：`feat: 添加知识库 API`。

### Task 5：安全上传、文档状态 API 与数据库验证

**文件：**
- 修改：`backend/app/core/config.py`、`backend/.env.example`
- 新建：`backend/app/knowledge/upload_service.py`
- 新建：`backend/app/api/v1/documents.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/unit/test_document_upload_api.py`
- 新建：`backend/tests/integration/test_document_upload.py`

- [ ] 写失败测试：合法上传返回 202 和 pending 文档/任务；不存在知识库返回 404；非法格式 415、空文件 422、超限 413、同库重复 409；保存名不含原始文件名。
- [ ] 运行目标测试确认失败。
- [ ] 为 `upload_directory`、`max_upload_bytes` 添加配置；实现流式大小校验、SHA-256、UUID 命名落盘、重复检查和 `Document`/`IngestionJob` 原子创建。
- [ ] 添加上传端点和 `GET /api/v1/documents/{document_id}`；所有异常转换为既定错误码。
- [ ] 启动 Docker 数据库后运行 `uv run alembic upgrade head`、`RUN_DATABASE_TESTS=1 uv run pytest tests/integration/test_document_upload.py -v`、`uv run pytest -v`、Ruff 检查。
- [ ] 提交：`feat: 添加安全文档上传与状态查询`。

## 阶段验收

```powershell
Set-Location backend
$env:DATABASE_URL='postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'
$env:RUN_DATABASE_TESTS='1'
uv run alembic upgrade head
uv run pytest -v
uv run ruff check app tests migrations
uv run ruff format --check app tests migrations
```

预期：五种格式解析测试、知识库 API、上传安全规则和 PostgreSQL 持久化测试全部通过；不产生切片或模型调用。
