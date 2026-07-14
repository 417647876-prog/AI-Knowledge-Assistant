# 阶段 1B：知识库与多格式文档解析设计

## 1. 目标与范围

本阶段在已完成的阶段 1A 基础上，实现从创建知识库到安全接收并解析文档的最小闭环。

支持格式：PDF、Word（`.docx`）、Excel（`.xlsx`）、Markdown（`.md`）和 TXT（`.txt`）。

本阶段不实现文本切片、Embedding、向量入库和问答；这些能力由阶段 1C、1D 完成。

## 2. 模块边界

新增 `app/knowledge` 模块，职责如下：

- `schemas.py`：知识库、文档、任务和解析段落的请求/响应 DTO。
- `services.py`：创建和查询知识库；校验上传、计算哈希、安全保存文件，并创建 `Document`、`IngestionJob` 记录。
- `parsers/`：解析器接口、注册表和五种格式的具体实现。
- `repositories.py`：封装知识库、文档和任务的数据库查询与写入。

API 路由只处理 HTTP 参数与响应；服务编排业务规则；解析器只把文件转换为 `ParsedSection`，不访问数据库也不调用模型。

```text
POST 知识库 / 上传文件
  → API Router
  → KnowledgeBaseService / DocumentUploadService
  → Repository（业务记录）
  → ParserRegistry（按文件类型选择）
  → TXT / Markdown / PDF / Word / Excel Parser
```

## 3. API 与状态

- `POST /api/v1/knowledge-bases`：创建知识库。
- `GET /api/v1/knowledge-bases`：列出知识库。
- `POST /api/v1/knowledge-bases/{knowledge_base_id}/documents`：接收并校验文件，安全落盘后创建 `Document(status=pending)` 与 `IngestionJob(status=pending, stage=parse)`，返回 `202 Accepted`。
- `GET /api/v1/documents/{document_id}`：查询文档与最新任务状态。

上传接口在本阶段不触发切片或向量化任务。解析器以独立组件实现并通过测试验证，为下一小步的后台处理接入做准备。

## 4. 文件安全规则

- 仅允许 `.pdf`、`.docx`、`.xlsx`、`.md`、`.txt`。
- 单文件最大 20 MB；空文件拒绝。
- 原始文件名仅用于展示；实际落盘名使用 UUID，不参与路径拼接。
- 计算 SHA-256；同一知识库内存在相同哈希时返回 `409 DUPLICATE_DOCUMENT`。
- 不支持的格式返回 `415 UNSUPPORTED_FILE_TYPE`；文件过大返回 `413 FILE_TOO_LARGE`；空内容或无法提取文本返回 `422 DOCUMENT_CONTENT_EMPTY`。

## 5. 统一解析器契约

每个解析器返回多个 `ParsedSection`：

```text
text            正文
page_number     PDF 页码，可空
sheet_name      Excel 工作表，可空
row_start       Excel 起始行，可空
section_title   标题，可空
metadata        可扩展来源信息
```

格式约定：

- TXT：只接受 UTF-8 或 UTF-8 BOM，不能解码时返回明确错误。
- Markdown：按标题边界保留章节标题。
- PDF：逐页提取正文；无可提取文字时返回 `OCR_REQUIRED`。
- Word：按原始顺序提取段落和表格。
- Excel：按工作表将表头与数据行转换成可读文本，并保留工作表名和行号。

## 6. 测试与验收

测试先行，并为每种格式提供最小夹具。至少覆盖：

- 创建与列出知识库。
- 五种格式的成功解析及来源元数据。
- 非法扩展名、空文件、超过 20 MB 的文件。
- 同一知识库内的重复文件。
- UUID 落盘名不使用原始文件名。
- 不存在的知识库或文档返回既定错误协议。

集成测试使用阶段 1A 的 PostgreSQL + pgvector 环境，确认上传后的 `Document` 与 `IngestionJob` 能正确持久化。

## 7. 非目标与后续

本阶段不写入 `DocumentChunk`，不调用任何模型，也不执行后台解析任务。阶段 1C 将在保留本阶段解析器契约的前提下，增加清洗、切片、Fake/OpenAI 兼容 Embedding Provider 和事务性向量入库。
