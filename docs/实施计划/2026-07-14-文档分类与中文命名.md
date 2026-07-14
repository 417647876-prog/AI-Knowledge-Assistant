# 项目文档分类与中文命名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目文档按用途分类、改为中文名称，并保留全部历史设计与实施计划及其有效链接。

**Architecture:** 使用 `git mv` 保留文件历史；先完成分类移动，再更新引用，最后用文本扫描和 Git 检查验证。根目录只保留项目入口文档，其他说明归入 `docs/` 的中文目录。

**Tech Stack:** Git、PowerShell、ripgrep、Markdown。

## Global Constraints

- 不删除任何历史设计、实施计划、学习记录或验收文档。
- 所有新增目录和移动后的文件名使用中文，保留日期与阶段编号。
- 不修改文档的业务内容，只修改路径、名称与必要的链接文本。
- 使用 `git mv` 进行所有版本控制文件的移动或改名。
- 完成后不得保留任何指向旧文档路径的 Markdown 链接。

---

### Task 1: 建立中文分类目录并迁移设计文档

**Files:**
- Move: `docs/superpowers/specs/*.md` → `docs/设计/*.md`
- Create: `docs/设计/`

**Interfaces:**
- Consumes: 8 份现有设计文档。
- Produces: `docs/设计/`，供 README、计划与验收文档链接。

- [ ] **Step 1: 记录迁移前的设计文档清单**

Run:

```powershell
rg --files docs/superpowers/specs
```

Expected: 8 个 Markdown 文档，其中包含总体设计、阶段 1B–1D、阶段 2A–2B、路线图和文档分类设计。

- [ ] **Step 2: 创建目标目录并使用 Git 移动设计文档**

Run:

```powershell
New-Item -ItemType Directory -Force docs/设计 | Out-Null
git mv docs/superpowers/specs/2026-07-10-rag-backend-design.md docs/设计/2026-07-10-RAG后端总体设计.md
git mv docs/superpowers/specs/2026-07-12-stage-1b-knowledge-base-document-parsing-design.md docs/设计/2026-07-12-阶段1B文档解析设计.md
git mv docs/superpowers/specs/2026-07-13-stage-1c-chunking-vector-ingestion-design.md docs/设计/2026-07-13-阶段1C向量入库设计.md
git mv docs/superpowers/specs/2026-07-13-stage-1d-retrieval-question-answering-design.md docs/设计/2026-07-13-阶段1D检索问答设计.md
git mv docs/superpowers/specs/2026-07-13-stage-2a-frontend-design.md docs/设计/2026-07-13-阶段2A前端工作台设计.md
git mv docs/superpowers/specs/2026-07-13-stage-2b-auth-rbac-design.md docs/设计/2026-07-13-阶段2B认证与权限设计.md
git mv docs/superpowers/specs/2026-07-14-interview-demo-roadmap-design.md docs/设计/2026-07-14-面试演示作品路线图.md
git mv docs/superpowers/specs/2026-07-14-document-organization-design.md docs/设计/2026-07-14-文档分类与中文命名设计.md
```

- [ ] **Step 3: 验证设计文档数量和中文目录**

Run:

```powershell
rg --files docs/设计
```

Expected: 8 个 Markdown 文档；`docs/superpowers/specs` 不再包含文件。

### Task 2: 迁移实施计划、验收材料与学习材料

**Files:**
- Move: `docs/superpowers/plans/*.md` → `docs/实施计划/*.md`
- Move: `docs/阶段*验证与演示.md` → `docs/验收与演示/*.md`
- Move: `docs/学习笔记.md`、`learning-records/0001-csharp-to-python-ai.md` → `docs/学习记录/*.md`
- Move: `lessons/0001-rag-backend-map.html` → `docs/课程材料/*.html`

**Interfaces:**
- Consumes: 阶段计划、验收说明和学习材料。
- Produces: 四个可按用途浏览的中文目录。

- [ ] **Step 1: 创建四个目标目录**

Run:

```powershell
New-Item -ItemType Directory -Force docs/实施计划, docs/验收与演示, docs/学习记录, docs/课程材料 | Out-Null
```

- [ ] **Step 2: 使用 Git 移动实施计划**

Run:

```powershell
git mv docs/superpowers/plans/2026-07-10-stage-1a-foundation-database.md docs/实施计划/2026-07-10-阶段1A项目基础与数据库.md
git mv docs/superpowers/plans/2026-07-12-stage-1b-knowledge-base-document-parsing.md docs/实施计划/2026-07-12-阶段1B知识库与文档解析.md
git mv docs/superpowers/plans/2026-07-13-stage-1c-chunking-vector-ingestion.md docs/实施计划/2026-07-13-阶段1C切片与向量入库.md
git mv docs/superpowers/plans/2026-07-13-stage-1d-retrieval-question-answering.md docs/实施计划/2026-07-13-阶段1D检索与问答.md
git mv docs/superpowers/plans/2026-07-13-stage-2a-frontend.md docs/实施计划/2026-07-13-阶段2A前端工作台.md
git mv docs/superpowers/plans/2026-07-13-stage-2b-auth-rbac.md docs/实施计划/2026-07-13-阶段2B认证与权限隔离.md
git mv docs/superpowers/plans/2026-07-14-document-organization.md docs/实施计划/2026-07-14-文档分类与中文命名.md
```

- [ ] **Step 3: 使用 Git 移动验收与学习材料**

Run:

```powershell
git mv docs/阶段1E验证与演示.md docs/验收与演示/阶段1E验证与演示.md
git mv docs/阶段2B验证与演示.md docs/验收与演示/阶段2B验证与演示.md
git mv docs/学习笔记.md docs/学习记录/学习笔记.md
git mv learning-records/0001-csharp-to-python-ai.md docs/学习记录/0001-CSharp转AI学习记录.md
git mv lessons/0001-rag-backend-map.html docs/课程材料/0001-RAG后端架构图.html
```

- [ ] **Step 4: 验证目录内容**

Run:

```powershell
rg --files docs/实施计划 docs/验收与演示 docs/学习记录 docs/课程材料
```

Expected: 7 份实施计划、2 份验收文档、2 份学习记录和 1 份 HTML 课程材料。

### Task 3: 中文化根目录入口文档并更新全部引用

**Files:**
- Move: `MISSION.md` → `项目任务与资源.md`
- Move: `NOTES.md` → `项目笔记.md`
- Move: `RESOURCES.md` → `参考资料.md`
- Modify: `README.md`
- Modify: `docs/实施计划/*.md`
- Modify: `docs/设计/*.md`

**Interfaces:**
- Consumes: 旧路径和文档迁移映射。
- Produces: 只含有效 Markdown 链接的文档集合。

- [ ] **Step 1: 使用 Git 改名根目录入口文档**

Run:

```powershell
git mv MISSION.md 项目任务与资源.md
git mv NOTES.md 项目笔记.md
git mv RESOURCES.md 参考资料.md
```

- [ ] **Step 2: 更新 README 的文档入口链接**

Replace the learning-material links with:

```markdown
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
```

- [ ] **Step 3: 全文替换旧路径引用**

Update every Markdown link or inline path that contains any of these prefixes:

```text
docs/superpowers/specs/
docs/superpowers/plans/
docs/阶段1E验证与演示.md
docs/阶段2B验证与演示.md
docs/学习笔记.md
learning-records/0001-csharp-to-python-ai.md
lessons/0001-rag-backend-map.html
MISSION.md
NOTES.md
RESOURCES.md
```

Use the exact destination paths from Tasks 1 and 2. Preserve code-block examples that intentionally document an old historical path only when they are labelled as historical; otherwise update them.

- [ ] **Step 4: Verify no obsolete paths remain**

Run:

```powershell
rg -n "docs/superpowers/(specs|plans)|docs/阶段[12][BE]验证与演示\.md|docs/学习笔记\.md|learning-records/|lessons/|MISSION\.md|NOTES\.md|RESOURCES\.md" -g '*.md' -g '*.html'
```

Expected: no matches.

### Task 4: 最终检查与提交

**Files:**
- Modify: 所有已移动或更新链接的文档

**Interfaces:**
- Consumes: Tasks 1–3 的新路径。
- Produces: 可浏览、可追溯且链接完整的中文文档结构。

- [ ] **Step 1: 检查 Git 是否将操作识别为移动**

Run:

```powershell
git status --short
git diff --summary --find-renames
```

Expected: 历史文档主要显示为 rename；不出现非预期删除。

- [ ] **Step 2: 验证 Markdown 链接目标存在**

Run:

```powershell
@'
from pathlib import Path
import re

root = Path.cwd()
missing = []
for markdown in root.rglob('*.md'):
    for target in re.findall(r'\[[^\]]+\]\(([^)#]+)', markdown.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):
            continue
        if not (markdown.parent / target).resolve().exists():
            missing.append(f'{markdown}: {target}')
if missing:
    raise SystemExit('\n'.join(missing))
print('所有本地 Markdown 链接目标均存在。')
'@ | python -
```

Expected: `所有本地 Markdown 链接目标均存在。`

- [ ] **Step 3: 检查空白错误与目录结构**

Run:

```powershell
git diff --check
Get-ChildItem docs -Directory | Select-Object -ExpandProperty Name
```

Expected: `git diff --check` 退出码为 0，且显示 `设计`、`实施计划`、`验收与演示`、`学习记录`、`课程材料`。

- [ ] **Step 4: 提交文档整理**

```powershell
git add README.md 项目任务与资源.md 项目笔记.md 参考资料.md docs learning-records lessons
git commit -m "docs: 整理项目文档分类与中文命名"
```
