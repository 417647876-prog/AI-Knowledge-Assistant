# 项目文档分类与中文命名设计

## 目标

在保留全部历史设计和实施计划的前提下，按用途分类项目文档，统一使用中文目录和文件名，并保持 README 与文档内部链接有效。

## 分类规则

| 新目录 | 内容 |
|---|---|
| `docs/设计` | 总体设计、各阶段设计、后续路线图 |
| `docs/实施计划` | 各阶段的详细实施计划 |
| `docs/验收与演示` | 验收步骤、演示脚本与运行说明 |
| `docs/学习记录` | 学习笔记与 C# 转 AI 的过程记录 |
| `docs/课程材料` | HTML 等教学辅助材料 |

根目录保留入口性文档：`README.md`、`项目任务与资源.md`、`项目笔记.md`、`参考资料.md`。

## 迁移映射

| 旧路径 | 新路径 |
|---|---|
| `docs/superpowers/specs/2026-07-10-rag-backend-design.md` | `docs/设计/2026-07-10-RAG后端总体设计.md` |
| `docs/superpowers/specs/2026-07-12-stage-1b-knowledge-base-document-parsing-design.md` | `docs/设计/2026-07-12-阶段1B文档解析设计.md` |
| `docs/superpowers/specs/2026-07-13-stage-1c-chunking-vector-ingestion-design.md` | `docs/设计/2026-07-13-阶段1C向量入库设计.md` |
| `docs/superpowers/specs/2026-07-13-stage-1d-retrieval-question-answering-design.md` | `docs/设计/2026-07-13-阶段1D检索问答设计.md` |
| `docs/superpowers/specs/2026-07-13-stage-2a-frontend-design.md` | `docs/设计/2026-07-13-阶段2A前端工作台设计.md` |
| `docs/superpowers/specs/2026-07-13-stage-2b-auth-rbac-design.md` | `docs/设计/2026-07-13-阶段2B认证与权限设计.md` |
| `docs/superpowers/specs/2026-07-14-interview-demo-roadmap-design.md` | `docs/设计/2026-07-14-面试演示作品路线图.md` |
| `docs/superpowers/specs/2026-07-14-document-organization-design.md` | `docs/设计/2026-07-14-文档分类与中文命名设计.md` |
| `docs/superpowers/plans/*.md` | `docs/实施计划/` 下对应的中文阶段计划文件 |
| `docs/阶段1E验证与演示.md` | `docs/验收与演示/阶段1E验证与演示.md` |
| `docs/阶段2B验证与演示.md` | `docs/验收与演示/阶段2B验证与演示.md` |
| `docs/学习笔记.md` | `docs/学习记录/学习笔记.md` |
| `learning-records/0001-csharp-to-python-ai.md` | `docs/学习记录/0001-CSharp转AI学习记录.md` |
| `lessons/0001-rag-backend-map.html` | `docs/课程材料/0001-RAG后端架构图.html` |
| `MISSION.md` | `项目任务与资源.md` |
| `NOTES.md` | `项目笔记.md` |
| `RESOURCES.md` | `参考资料.md` |

## 约束与验收

- 保留所有历史计划和设计；不删除阶段文档。
- 保留日期和阶段编号，便于回溯开发顺序。
- 更新 `README.md`、实施计划及其他 Markdown 中的受影响链接。
- 使用 Git 识别移动与改名，避免把历史文档误判为新建或删除。
- 最终执行全文链接扫描和 `git diff --check`。
