# 阶段 1C：文本切片与向量入库设计

## 1. 目标与范围

阶段 1C 将阶段 1B 保存的原始文档处理为可检索的 `DocumentChunk`：解析文件、清洗文本、递归切片、批量生成 Embedding，并在 PostgreSQL + pgvector 中事务性保存切片和向量。

本阶段不实现相似度检索、Prompt、ChatProvider 和问答 API；这些属于阶段 1D。

## 2. 处理流程与状态

上传接口成功创建 `Document` 和 `IngestionJob` 后，通过 FastAPI 进程内后台任务调用 `IngestionService.process(document_id)`：

```text
pending
  → parsing：选择解析器并生成 ParsedSection
  → cleaning：规范空白、换行并丢弃空段落
  → splitting：生成带来源信息的文本切片
  → embedding：批量生成 1536 维向量
  → store：在一个数据库事务中写入全部 DocumentChunk
  → ready / succeeded
```

后台任务只用于本地开发和演示，不承诺进程重启后的任务恢复。服务本身不依赖 FastAPI，可在单元测试中直接调用，并为后续迁移到独立 Worker 保留边界。

## 3. 文本清洗

清洗仅做不改变语义的规范化：

- 将 `CRLF`、`CR` 统一为 `LF`。
- 去除每行首尾空白。
- 连续三个及以上空行压缩为两个换行。
- 去除整段首尾空白。
- 清洗后为空的段落不进入切片。

不删除标点、不改写句子、不做繁简转换，也不移除业务数字和日期。

## 4. 中文递归字符切片

默认参数：

- `chunk_size = 800` 个字符。
- `chunk_overlap = 120` 个字符。
- 参数由 `Settings` 管理，并校验 `0 <= chunk_overlap < chunk_size`。

分隔符按以下优先级递归尝试：

```text
\n\n → \n → 。 → ！ → ？ → ； → ， → 、 → 空格 → 单字符
```

算法优先在自然边界切开；只有找不到边界时才按字符硬切。每个切片不超过 `chunk_size`，相邻切片保留不超过 `chunk_overlap` 的尾部文本。空白切片不入库。

每个切片继承所属 `ParsedSection` 的 `page_number`、`sheet_name`、`row_start`、`section_title` 和 `metadata`，并记录：

- 文档内全局递增的 `chunk_index`。
- 清洗后段落内的 `start_index`。
- SHA-256 `content_hash`。

不同 `ParsedSection` 之间不做重叠，避免把 PDF 不同页或 Excel 不同行的来源信息混合。

## 5. EmbeddingProvider

定义异步接口：

```python
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
```

提供两种实现：

- `FakeEmbeddingProvider`：默认用于本地开发和自动化测试。根据输入内容生成确定性的 1536 维向量，不访问网络、不产生费用。
- `OpenAICompatibleEmbeddingProvider`：读取独立的 Base URL、API Key、模型名、维度和批大小配置，调用 OpenAI 兼容 `/embeddings` 接口。

Provider 必须保证输出数量与输入数量相同，每个向量恰好 1536 维；否则抛出 `EMBEDDING_PROVIDER_ERROR`。真实 Provider 只有显式配置 `EMBEDDING_PROVIDER=openai` 时启用，默认值为 `fake`。

## 6. 事务与失败处理

解析、清洗、切片和 Embedding 在写入事务之前完成。向量全部成功后，才开启最终写入：

1. 删除该文档已有切片，支持安全重试。
2. 一次事务写入全部新 `DocumentChunk`。
3. 更新 `Document.status = ready`。
4. 更新任务为 `status = succeeded`、`stage = store`、记录 `chunk_count` 与完成时间。

任何写入失败都回滚全部切片，不能留下半套数据。解析或 Embedding 失败时，原始文件保留；文档和任务单独更新为 `failed`，写入安全错误码和错误消息。日志保留技术异常，API 状态查询不暴露堆栈、密钥或服务器路径。

## 7. 测试与验收

单元测试覆盖：

- 换行、空白规范化且不改变正文语义。
- 中文标点优先切分、最大长度、重叠和空切片过滤。
- PDF 页码、Excel 工作表/行号等元数据继承。
- Fake Provider 对相同文本返回相同的 1536 维向量。
- Provider 数量或维度错误被拒绝。
- 解析失败与 Embedding 失败产生正确状态。

PostgreSQL 集成测试覆盖：

- 一次事务写入全部切片与向量。
- 写入失败时没有残留切片。
- 重试同一文档不会生成重复切片。
- 成功后文档为 `ready`、任务为 `succeeded` 且 `chunk_count` 正确。

阶段验收使用 Fake Provider 完成：创建知识库、上传 TXT/Markdown、等待文档变为 `ready`，并查询数据库确认 `DocumentChunk.embedding` 为 1536 维。真实模型验证由显式环境变量开启，不作为默认测试的一部分。

## 8. 非目标与后续

1C 不评估检索相关性，也不增加 HNSW 索引。阶段 1D 将基于本阶段数据实现知识库范围内的余弦检索、相关性阈值、问答生成和结构化引用；取得评估数据后，再比较 Token 切分或语义切分。
