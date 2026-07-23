# Task 2 完成报告：五种格式演示资料生成器

## 范围

仅完成正式计划的 Task 2：新增 `backend/scripts/generate_demo_knowledge_files.py`，并扩展聚焦测试；未生成项目根正式交付资料，未执行 Task 3 或 Task 4。

## TDD 证据

- RED：`Set-Location backend; uv run pytest tests/scripts/test_generate_demo_knowledge_files.py -q`
  - 结果：按预期收集失败，`ModuleNotFoundError: No module named 'scripts.generate_demo_knowledge_files'`。
- GREEN：`Set-Location backend; uv run pytest tests/scripts/test_generate_demo_knowledge_files.py -q`
  - 结果：`9 passed in 19.60s`。

## 实现内容

- 从 Task 1 的冻结 `KNOWLEDGE_DOCUMENTS` / `QUESTION_SETS` 生成 Markdown、TXT、DOCX、XLSX、PDF 和问题 Markdown。
- 提供 `generate_demo_files(output_root)` 及命令行入口；仅重建输出根目录下精确的“演示知识库资料”和“演示测试问题”目录，并拒绝文件系统根目录。
- DOCX 使用 A4、2.2 cm 页边距、中文 eastAsia 字体、紧凑参考指南层级、速览表与页脚页码。
- XLSX 生成“资料说明”和业务数据两个工作表，包含冻结标题、筛选、交替填充、自动换行与限定列宽。
- PDF 嵌入 Windows 中文字体，使用 A4、可提取中文文本、页码和至少两页。
- 测试直接调用生产 `ParserRegistry`，验证 15 个知识文件均可解析、含文档代号、受大小限制且满足内容密度；验证三份问题文档的禁止上传标记和实际答复/引用字段；补充重生成不删除无关目录与根目录保护测试。

## 结构抽查

使用临时输出目录检查：DOCX 为 A4、含 1 张表；XLSX 有 2 个工作表、`freeze_panes=A2` 和筛选范围；PDF 为 2 页且第一页可提取 357 个字符。

## 遗留边界

- Task 2 只在 pytest 的临时目录中生成文件，正式的 15 个知识资料与 3 份测试问题留给 Task 3 生成、解析和可视化抽查。
- 本机未检测到 LibreOffice，因此 DOCX 渲染 PNG 的可视化验收留给 Task 3 按环境能力处理；本 Task 已完成结构与生产解析器验证。

## 审查修复

- 审查指出生成测试只校验文件总数，无法阻止文件被放错主题目录；现已从 `KNOWLEDGE_DOCUMENTS` 和 `QUESTION_SETS` 构造精确相对路径集合并断言生成结果完全一致。
- 审查指出问题文档测试仅校验通用标记，无法覆盖 30 道题的内容；现已按每个主题、每题逐一断言题型、题干、预期要点、来源（或“知识库缺少答案”表述）、通过标准，以及实际回答/实际引用字段。
- 修复后复跑：`Set-Location backend; uv run pytest tests/scripts/test_generate_demo_knowledge_files.py -q`，结果 `9 passed in 20.62s`。
