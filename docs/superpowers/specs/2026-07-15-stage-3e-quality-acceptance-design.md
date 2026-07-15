# 阶段 3E：RAG 质量综合验收设计

> **状态：** 设计已由用户逐段确认；本文只定义阶段 3E 的边界和验收口径，不包含功能实现。
>
> **基线：** 阶段 3D 完成提交 `f6aead3`。阶段 3C 的 MRR@5 质量门未通过，但已有明确、受限且必须继续保留的风险豁免。

## 1. 目标

阶段 3E 使用同一数据集和同一知识库快照，自动生成并比较以下四条检索链路：

1. `vector`：纯向量检索。
2. `hybrid`：向量检索、关键词检索和 RRF。
3. `rerank`：混合候选检索和本地 BGE Reranker。
4. `rewrite`：选择性问题改写、混合候选检索和本地 BGE Reranker。

阶段必须交付可重复执行的一键验收命令、机器可读的原始报告、可提交的脱敏中文报告、明确的自动退出码，以及适合求职展示的五分钟演示说明。

## 2. 已确认的产品决策

- 3E 的绝对质量门只验收最终推荐的 `rewrite` 链路；其余三种模式作为演进对照，不要求全部达到最终门槛。
- 四份报告必须自动证明来自同一知识库快照，不能只依赖人工按顺序运行。
- 3C 必须显示为“质量门未通过、已获风险豁免”，不能写成技术通过。
- 3C 豁免只允许 MRR 相对提升为 0 且引用不退化；负增长或引用下降不在豁免范围内。
- 四份原始 JSON 继续由 Git 忽略；脱敏后的中文综合报告提交到 `docs/阶段3质量验收报告.md`。
- 使用独立 `scripts.accept_stage3` 编排验收；不把现有 `scripts.evaluate_rag` 强制改造成子命令式 CLI。
- 3E 拆分为 5 个功能 Task，第三阶段总任务数从 25 调整为 26。

## 3. 范围外事项

阶段 3E 不包含：

- 修改固定 30 条评估数据以制造通过。
- 调整 3A～3E 的历史指标或把 3C 豁免改写成通过。
- 新增线上监控、持续评测平台、CI 定时任务或远程报告存储。
- 更换 Embedding、Chat、Reranker、PostgreSQL 或 pgvector。
- 修改问答 HTTP/SSE 契约、前端交互或会话存储。
- Agent、多 Agent、模型训练或微调。
- 没有评估证据支持的大规模重构。

## 4. 总体架构

现有 `scripts.evaluate_rag` 继续负责单模式评测并保持 3A～3D 命令兼容。新增 `scripts.accept_stage3` 作为阶段级编排器：

```text
读取版本化质量策略
  -> 生成本次验收 run_id
  -> 计算知识库基准快照 S0
  -> 依次运行 vector / hybrid / rerank / rewrite
  -> 每种模式运行前后重新计算快照并要求等于 S0
  -> 校验四份报告的 schema、案例、环境和溯源信息
  -> 计算总体指标、分类指标、模式差异和 3A～3E 门禁
  -> 对 3C 应用版本化且受限的风险豁免
  -> 生成四份 JSON、验收清单和中文 Markdown
  -> 最后写入 manifest 作为本次产物完整提交标记
  -> 返回 0 / 1 / 2
```

模块边界：

| 文件 | 单一职责 |
|---|---|
| `backend/app/evaluation/snapshot.py` | 读取检索语料并生成确定性知识库快照 |
| `backend/app/evaluation/schemas.py` | 报告 1.0/1.1、案例分类和溯源模型 |
| `backend/app/evaluation/policy.py` | 加载并校验版本化阶段 3 质量策略 |
| `backend/app/evaluation/comparison.py` | 报告兼容性、指标差异、历史门和最终门 |
| `backend/app/evaluation/reporting.py` | 把结构化比较结果渲染为中文 Markdown |
| `backend/scripts/evaluate_rag.py` | 保持单模式评测入口兼容，并生成 1.1 报告 |
| `backend/scripts/accept_stage3.py` | 四模式编排、快照复核、原子输出和退出码 |
| `backend/config/evaluation/stage3-quality-policy.json` | 最终阈值和 3C 受限豁免的版本化事实来源 |

任何模块都不得重新实现 Recall、MRR、引用或上限感知目标公式；必须复用 `app.evaluation.metrics`。

## 5. 报告 1.1 与向后兼容

现有报告 schema 为 `1.0`。阶段 3E 将读取模型扩展为同时接受 `1.0` 和 `1.1`，但新生成报告固定为 `1.1`。

### 5.1 `CaseResult` 新字段

- `category`：`keyword`、`semantic`、`refusal`、`multi_turn` 或 `interference`。
- `citation_hit_rate`：当前案例的引用命中率，范围为 0～1。

这两个字段用于稳定计算分类指标和失败案例 ID，禁止继续依赖 `case_id` 前缀推断分类。

### 5.2 `EvaluationProvenance`

1.1 报告增加以下溯源信息：

- `run_id`：同一次 `accept_stage3` 的四份报告共享同一 UUID。
- `knowledge_base_id`：本地原始 JSON 可记录知识库 UUID；公开 Markdown 只显示脱敏摘要。
- `snapshot_sha256`：知识库检索语料的 SHA-256 指纹。
- `document_count`：该知识库文档数。
- `chunk_count`：该知识库片段数。
- `generated_at`：UTC 时间，仅用于追踪，不参与兼容性比较。

旧 1.0 报告可以被单独读取和展示，但由于缺少分类和溯源字段，不能通过 3E 的四模式兼容性门禁。错误必须明确指出需要重新生成报告，不能抛出含连接信息的底层异常。

## 6. 知识库快照

快照只覆盖会影响检索、引用或拒答判断的语料，不包含知识库名称、描述、所有者、连接串或密钥。

### 6.1 规范化输入

按固定顺序读取并编码：

- 知识库 UUID。
- 文档按 UUID 排序：`id`、`original_file_name`、`file_hash`、`status`。
- 片段按 `document_id`、`chunk_index`、`id` 排序：
  - `id`、`document_id`、`chunk_index`。
  - `content_hash`、`content`、`search_text`。
  - `page_number`、`sheet_name`、`row_start`、`section_title`、`start_index`。
  - 排序键固定的 `extra_metadata`。
  - 以固定 IEEE 754 字节格式编码的 Embedding 数值。

`search_vector` 是 `search_text` 的数据库计算字段，不重复纳入。时间戳不影响检索语料，不纳入指纹。

所有可变长度字段使用长度前缀编码，JSON 使用排序键和固定 UTF-8 编码，避免简单分隔符造成歧义。

### 6.2 运行期间一致性

编排器先计算基准快照 `S0`。每种模式运行前和运行后都重新计算快照，并要求：

```text
before_mode == S0 == after_mode
```

任意一次不相等都视为输入一致性错误，返回退出码 1，不覆盖上一次正式报告。其他知识库的变化不得影响当前知识库指纹。

## 7. 质量策略

策略保存在 `backend/config/evaluation/stage3-quality-policy.json`，不得通过临时 CLI 参数放宽门槛。策略至少包含：

- 策略 schema 版本。
- 最终模式 `rewrite`。
- 最少案例数 30。
- 五个必需分类。
- 3B、3C、3D 的历史门定义。
- 3E 最终绝对阈值。
- 3C 豁免的 gate ID、批准日期、允许下限、原因和证据文档路径。

策略加载失败、重复 gate ID、未知模式、非法比例、缺少证据路径或豁免范围不完整时，命令返回退出码 1。

## 8. 可比性门禁

比较指标前必须校验：

1. `vector`、`hybrid`、`rerank`、`rewrite` 四种模式齐全、唯一且没有额外模式。
2. 四份报告均为 schema 1.1。
3. `dataset_sha256`、`top_k`、`case_count` 完全一致。
4. 案例 ID、分类和顺序一致，不允许缺失、重复或重排。
5. `run_id`、`knowledge_base_id`、`snapshot_sha256`、文档数和片段数一致。
6. Embedding、Chat 模型、设备、维度、批大小和分数阈值等公共环境一致。
7. 模式特有环境符合固定矩阵：
   - vector：`rag_retrieval_mode=vector`，Reranker 禁用。
   - hybrid：`rag_retrieval_mode=hybrid`，Reranker 禁用。
   - rerank/rewrite：`rag_retrieval_mode=hybrid`，使用相同本地 Reranker，fallback 关闭，候选数、模型、设备、批大小和接受门一致。

错误只显示字段名、模式和脱敏值，不显示案例问题、片段、Prompt、连接串或密钥。

## 9. 历史阶段门禁

综合报告必须重新计算，而不是复制看板数字：

### 9.1 阶段 3A

- 案例数不少于 30。
- `keyword`、`semantic`、`refusal`、`multi_turn`、`interference` 五类均存在。

### 9.2 阶段 3B

- hybrid 总体 Recall@5 不低于 vector。
- hybrid 关键词 Recall@5 达到 `ceiling_aware_target(vector_keyword_recall, 0.10)`。
- hybrid 引用命中率和拒答准确率不得低于 vector。

### 9.3 阶段 3C

- rerank MRR@5 相对 hybrid 提升至少 5%。
- rerank 引用命中率不得低于 hybrid。

当前固定数据的 MRR 相对提升为 0%，因此第一条未通过。版本化豁免仅在以下条件同时满足时生效：

- 实际 MRR 相对提升不小于 0%。
- rerank MRR@5 不低于 hybrid。
- rerank 引用命中率不低于 hybrid。
- 策略中的 gate ID、批准日期、证据文档和允许下限均有效。

如果真实结果已经达到 5%，状态直接显示“通过”，不再消费豁免。如果 MRR 负增长或引用下降，显示“未通过且豁免不适用”，3E 返回退出码 2。

### 9.4 阶段 3D

- rewrite 多轮 Recall@5 达到 `ceiling_aware_target(rerank_multi_turn_recall, 0.15)`。
- 问题改写选择性与 `QUESTION_REWRITE_ERROR` 回退由自动化测试验证，不从 JSON 报告虚构结论。

## 10. 最终阶段 3E 门禁

最终绝对门只应用于 `rewrite`：

- Recall@5 不低于 0.85。
- 引用命中率不低于 0.90。
- 拒答准确率不低于 0.90。

MRR@5、P50 和 P95 必须展示，但不作为 3E 的绝对通过条件。vector 低于 85% 只作为基线事实，不导致最终门失败。

## 11. 比较结果模型

比较模块返回结构化对象，至少包含：

- 按模式索引的四份报告。
- 每种模式相对 vector 的总体指标差异。
- 每个分类的 Recall@5。
- 3A～3E 每条 gate 的 `passed`、`failed` 或 `waived` 状态。
- 豁免原因、允许范围和证据引用。
- Recall、引用或拒答失败的案例 ID，不包含问题全文。
- 最终 `passed` 布尔值和脱敏失败原因。
- 推荐配置 `rewrite` 与回退配置 `vector`。

任何 gate 只能有一个稳定 gate ID，策略、比较器、测试和 Markdown 必须使用相同 ID。

## 12. 中文报告

正式报告路径固定为 `docs/阶段3质量验收报告.md`，章节顺序固定：

1. 验收结论。
2. 数据集与知识库快照。
3. 执行环境。
4. 四模式总体指标。
5. 各分类 Recall@5。
6. 相对 vector 的提升或退化。
7. 3A～3E 质量门结果。
8. 3C 风险豁免及适用边界。
9. 失败案例 ID。
10. 最终推荐配置与纯向量回退配置。
11. 可重复执行命令。
12. 已知风险与延迟说明。

公开 Markdown 不显示完整知识库 UUID，只显示快照指纹前 12 位、文档数、片段数和 run ID。不得包含问题全文、片段全文、完整 Prompt、数据库 URL、API Key 或 Access Token。

## 13. CLI 与退出码

新增入口示意：

```powershell
uv run python -m scripts.accept_stage3 `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --policy config/evaluation/stage3-quality-policy.json `
  --reports-dir reports `
  --markdown-output ../docs/阶段3质量验收报告.md
```

控制台只输出：run ID、四个报告路径、Markdown 路径、最终三项指标、门禁状态和脱敏失败原因。

退出码：

| 退出码 | 含义 |
|---:|---|
| 0 | 报告兼容、最终门通过，历史失败均通过或具有有效且未超范围的豁免 |
| 1 | 参数、文件、schema、策略、快照、环境或输出错误 |
| 2 | 最终质量门失败，或存在未获豁免/超出豁免范围的历史失败 |

质量失败时仍生成标记为“未通过”的中文报告，以保留真实证据。输入、schema、快照或环境不兼容时不覆盖现有正式报告。

## 14. 输出完整性

四份报告固定为：

- `backend/reports/stage3e-vector.json`
- `backend/reports/stage3e-hybrid.json`
- `backend/reports/stage3e-rerank.json`
- `backend/reports/stage3e-rewrite.json`

另生成 Git 忽略的 `backend/reports/stage3e-manifest.json`，记录 run ID、快照指纹、各文件 SHA-256、Markdown 文件 SHA-256 和最终 gate 状态。

所有内容先写入编排器创建的临时目录，单文件使用 `os.replace` 原子替换。manifest 最后写入，作为整组产物完整提交的标记；消费者只接受 run ID 和哈希均与 manifest 一致的文件。官方 Markdown 在四份 JSON、比较结果和安全扫描全部完成后再替换。

编排器只清理自己创建的临时目录，不删除用户文件或其他历史报告。

## 15. 错误与安全

- Provider、数据库和文件异常使用现有脱敏错误风格，不打印底层连接信息。
- 报告和控制台输出经过敏感标记扫描，至少拒绝 `database_url`、`api_key`、`access_token`、数据库连接协议和已知密钥字段名。
- 报告比较错误只列 case ID，不列问题、历史、片段或 Prompt。
- `QUESTION_REWRITE_ERROR` 继续由既有 3D 逻辑回退；3E 不新增第二套回退。
- Reranker fallback 在真实 rerank/rewrite 验收中必须关闭，防止静默生成非重排报告。
- 质量失败不得通过改写 Markdown、调整数据集或临时 CLI 参数转成成功。

## 16. 测试策略

### 16.1 快照与 schema

- 相同行数据多次读取生成相同指纹。
- 文档、片段内容、检索 Token、Embedding 或引用元数据变化会改变指纹。
- 其他知识库变化不影响当前知识库指纹。
- 旧 1.0 报告仍可读取；3E 比较会明确拒绝缺少溯源的旧报告。
- 1.1 案例保存分类和逐案例引用命中率。

### 16.2 比较与策略

- 缺少、重复或未知模式失败。
- 数据集、Top K、案例、run ID、知识库或快照不一致失败。
- 公共环境漂移失败；固定模式矩阵通过。
- 3B 和 3D 使用同一个 `ceiling_aware_target`。
- 3C 零提升且引用持平显示 `waived`；负增长或引用下降显示 `failed`。
- 最终门只读取 rewrite，不因 vector 83.33% 自动失败。
- 策略重复 gate、非法比例、未知 gate 或证据缺失失败。

### 16.3 Markdown

- 章节顺序、表头、模式顺序和小数格式固定。
- 失败案例只显示 ID。
- 3C 同时显示原门槛、实际结果、豁免原因和边界。
- 敏感字段、问题、片段和 Prompt 不进入 Markdown。

### 16.4 CLI

- 成功返回 0，输入/兼容性错误返回 1，质量失败返回 2。
- 质量失败仍写入失败报告。
- 输入或快照错误不覆盖旧报告。
- 模拟中途写入失败时，manifest 不更新。
- 现有 `scripts.evaluate_rag --mode ...` 命令和测试保持兼容。

### 16.5 最终验证

- 使用同一真实知识库重新生成四模式报告。
- 后端全量 pytest。
- Ruff check 和 format check。
- 临时空数据库从零迁移、全部 integration、迁移降级与重新升级，并在 `finally` 删除临时库。
- 前端全量 Vitest 和生产构建。
- `git diff --check`、报告敏感信息扫描和文档一致性审查。

## 17. 实施 Task 与模型

### Task 1：报告溯源与知识库快照

- 模型：`Sol｜high`。
- 交付：报告 1.1、案例分类、逐案例引用、知识库快照和数据库集成测试。
- 理由：涉及 SQLAlchemy 查询、确定性哈希和旧报告兼容。

### Task 2：报告比较、质量策略与受限豁免

- 模型：`Sol｜xhigh`。
- 交付：策略模型、兼容性矩阵、模式差异、3A～3E gate 和 3C 受限豁免。
- 理由：直接决定阶段是否通过，错误会掩盖真实质量退化。

### Task 3：脱敏中文验收报告

- 模型：`Terra｜medium`。
- 交付：纯函数 Markdown 渲染器和敏感内容回归测试。
- 理由：输入输出固定，不访问数据库或模型。

### Task 4：`accept_stage3` 验收编排器

- 模型：`Sol｜xhigh`。
- 交付：四模式顺序运行、快照前后校验、原子输出、manifest 和退出码。
- 理由：跨数据库、真实 Provider、文件系统和 CLI，失败语义复杂。

### Task 5：真实验收、演示与第三阶段收口

- 模型：`Sol｜xhigh`。
- 交付：真实报告、正式 Markdown、`docs/阶段3验证与演示.md`、README、学习笔记和执行看板。
- 理由：需要判断真实模型波动、完整质量门、迁移与第三阶段最终结论。

计划设计和文档确认不计入功能 Task；执行看板在正式开始 Task 1 时把总任务数调整为 26。

## 18. 完成定义

阶段 3E 只有同时满足以下条件才可标记完成：

- 五个 Task 均有失败测试、最小实现、目标验证和独立提交证据。
- 四份 1.1 报告具有相同 run ID、知识库快照、数据集、Top K 和案例集合。
- rewrite 的 Recall@5、引用命中率和拒答准确率达到最终门槛。
- 3C 仍显示原门未通过，豁免未超范围，且没有其他未获豁免的失败。
- 正式中文报告和 manifest 哈希一致，敏感信息扫描通过。
- 纯向量回退、权限隔离、引用真实性、流式问答和多轮回退没有退化。
- 后端、数据库、迁移、前端、Ruff、构建和 diff 检查全部通过。
- README、学习笔记、执行看板、正式验收报告和演示文档一致。
- 阶段 3 总状态更新为“已完成”，并保留 3C 质量门豁免事实。

## 19. 设计自检结论

- 无占位内容或未决定的实现口径。
- 最终质量门的适用对象明确为 rewrite。
- 知识库快照、run ID 和 manifest 共同证明四份报告属于同一次验收。
- 3C 豁免有明确允许下限，不会覆盖新的负增长或引用退化。
- 单模式 CLI、HTTP/SSE、前端和数据库 schema 均不被无关重构。
- 设计范围可以由一个实施计划覆盖，无需拆成独立子项目。
