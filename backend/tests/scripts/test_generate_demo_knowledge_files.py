from collections import Counter
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from app.knowledge.parser_factory import create_parser_registry
from scripts.demo_knowledge_manifest import (
    KNOWLEDGE_DOCUMENTS,
    QUESTION_SETS,
    KnowledgeDocumentSpec,
    QuestionSpec,
)
from scripts.generate_demo_knowledge_files import generate_demo_files


EXPECTED_DOCUMENTS = {
    "项目快速开始.md": (
        "01-项目使用与故障排查", "OPS-START-2026",
        ("ai-knowledge-assistant", "http://127.0.0.1:8080", "/api/ready", "HTTP 200", "2 GiB"),
    ),
    "知识库上传规范.txt": (
        "01-项目使用与故障排查", "OPS-UPLOAD-20M",
        (".txt/.md/.pdf/.docx/.xlsx", "20 MB", "ready", "错误原因"),
    ),
    "RAG问答与引用指南.docx": (
        "01-项目使用与故障排查", "RAG-CITE-2026",
        ("先检索再生成", "文件名和摘录", "未知答案不得编造", "测试问题不得上传"),
    ),
    "部署端口与健康检查.xlsx": (
        "01-项目使用与故障排查", "DEPLOY-HEALTH-8080",
        ("8080", "8000", "5432", "/api/ready", "gateway、api、worker、postgres"),
    ),
    "常见故障排查手册.pdf": (
        "01-项目使用与故障排查", "OPS-TROUBLE-180",
        ("180 秒", "端口 8080", "Docker Desktop", "3 个持久卷"),
    ),
    "Python与CSharp语法对照.md": (
        "02-CSharp转AI学习笔记", "CS-PY-ASYNC-01",
        ("async/await", "List<T>", "Dictionary<TKey,TValue>", "uv.lock", "NuGet", "restore"),
    ),
    "RAG检索流程.txt": (
        "02-CSharp转AI学习笔记", "RAG-PIPELINE-06",
        ("清洗、切块、向量化、检索、重排序、生成", "600 字符", "80 字符", "重排序后"),
    ),
    "FastAPI与ASP.NET-Core对照.docx": (
        "02-CSharp转AI学习笔记", "API-COMPARE-422",
        ("依赖注入", "Pydantic", "模型绑定", "HTTP 422", "400", "中间件"),
    ),
    "向量检索参数实验.xlsx": (
        "02-CSharp转AI学习笔记", "VECTOR-EXP-K6",
        ("top-k 为 3、6、10", "0.82", "top-k 6", "148 ms", "切块 600", "重叠 80"),
    ),
    "AI-Agent学习路线.pdf": (
        "02-CSharp转AI学习笔记", "AGENT-ROADMAP-12",
        ("12 周", "1-3 周", "4-6 周", "7-9 周", "10-12 周", "每周日"),
    ),
    "客服退款FAQ.md": (
        "03-模拟企业资料", "REFUND-7D-2026",
        ("7 个自然日", "1 个工作日", "3 个工作日", "2 小时"),
    ),
    "IT账号安全规范.txt": (
        "03-模拟企业资料", "SEC-MFA-14",
        ("14 个字符", "24 小时", "30 分钟", "4 小时"),
    ),
    "员工手册与休假制度.docx": (
        "03-模拟企业资料", "HR-LEAVE-10",
        ("10 天", "超过 2 天", "90 天", "前一工作日 16:00"),
    ),
    "产品套餐与服务时效.xlsx": (
        "03-模拟企业资料", "PLAN-SLA-2026",
        ("¥99/月", "8 小时", "¥399/月", "2 小时", "Enterprise", "30 分钟", "15 天"),
    ),
    "项目会议纪要与行动项.pdf": (
        "03-模拟企业资料", "PM-MEETING-0915",
        ("2026-08-28", "2026-09-15", "2026-09-08", "2026-09-12"),
    ),
}


def test_manifest_has_complete_unique_document_contract() -> None:
    assert len(KNOWLEDGE_DOCUMENTS) == 15
    assert Counter(item.filename.rsplit(".", 1)[-1] for item in KNOWLEDGE_DOCUMENTS) == {
        "md": 3,
        "txt": 3,
        "docx": 3,
        "xlsx": 3,
        "pdf": 3,
    }
    assert len({item.filename for item in KNOWLEDGE_DOCUMENTS}) == 15
    assert len({item.document_code for item in KNOWLEDGE_DOCUMENTS}) == 15
    assert {item.filename for item in KNOWLEDGE_DOCUMENTS} == set(EXPECTED_DOCUMENTS)
    assert Counter(item.folder for item in KNOWLEDGE_DOCUMENTS) == {
        "01-项目使用与故障排查": 5,
        "02-CSharp转AI学习笔记": 5,
        "03-模拟企业资料": 5,
    }
    for item in KNOWLEDGE_DOCUMENTS:
        expected_folder, expected_code, expected_facts = EXPECTED_DOCUMENTS[item.filename]
        assert item.folder == expected_folder
        assert item.document_code == expected_code
        content = "\n".join((item.summary, *item.sections))
        assert item.title
        assert len(item.sections) >= 3
        if item.filename.endswith(".xlsx"):
            # XLSX 可通过工作表体现等价表格信息密度，但总内容仍受上限控制。
            assert item.table_sheets
            assert len(content) <= 1800
        else:
            assert 800 <= len(content) <= 1800
            assert item.table_sheets == ()
        assert all(fact in content for fact in expected_facts)
        assert all(len(section) >= 80 and "。" in section for section in item.sections)


def test_manifest_dataclass_field_order_and_frozen_contract() -> None:
    assert [field.name for field in fields(KnowledgeDocumentSpec)] == [
        "folder", "filename", "document_code", "title", "summary", "sections", "table_sheets",
    ]
    assert [field.name for field in fields(QuestionSpec)] == [
        "question", "kind", "expected_points", "source_files", "pass_criteria",
    ]
    with pytest.raises(FrozenInstanceError):
        KNOWLEDGE_DOCUMENTS[0].filename = "不应允许修改.md"
    with pytest.raises(FrozenInstanceError):
        QUESTION_SETS["01-项目使用与故障排查"][0].kind = "cross_document"


def test_question_manifest_uses_required_mix_and_document_coverage() -> None:
    assert len(QUESTION_SETS) == 3
    all_questions = [question for questions in QUESTION_SETS.values() for question in questions]
    assert len(all_questions) == 30
    expected_mix = {
        "single_document": 6,
        "cross_document": 2,
        "citation_check": 1,
        "unanswerable": 1,
    }
    for questions in QUESTION_SETS.values():
        assert len(questions) == 10
        assert Counter(question.kind for question in questions) == expected_mix
        unanswerable = [q for q in questions if q.kind == "unanswerable"]
        assert len(unanswerable) == 1
        assert unanswerable[0].source_files == ()
        assert "知识库" in unanswerable[0].pass_criteria
        assert "缺少" in unanswerable[0].pass_criteria
    source_counts = Counter(source for question in all_questions for source in question.source_files)
    filenames = {document.filename for document in KNOWLEDGE_DOCUMENTS}
    assert all(source in filenames for source in source_counts)
    assert all(source_counts[document.filename] >= 2 for document in KNOWLEDGE_DOCUMENTS)


def test_single_document_questions_explicitly_bind_expected_points_to_sources() -> None:
    expected_source_for_point = {
        "错误原因": "知识库上传规范.txt",
        "async/await": "Python与CSharp语法对照.md",
    }
    for questions in QUESTION_SETS.values():
        for question in questions:
            if question.kind != "single_document":
                continue
            assert len(question.source_files) == 1
            for point in question.expected_points:
                if point in expected_source_for_point:
                    assert question.source_files == (expected_source_for_point[point],)


def test_manifest_contains_exact_retrieval_facts_and_no_sensitive_placeholders() -> None:
    text = "\n".join(
        "\n".join((item.title, item.summary, *item.sections)) for item in KNOWLEDGE_DOCUMENTS
    )
    for fact in (
        "http://127.0.0.1:8080", "/api/ready", "20 MB", "top-k 6", "0.82",
        "2026-09-15", "14 个字符", "3 个持久卷",
    ):
        assert fact in text
    for forbidden in ("sk-", "AKIA", "13800138000", "110101199001011234"):
        assert forbidden not in text


def test_generator_creates_exact_tree_and_parseable_knowledge_files(tmp_path: Path) -> None:
    knowledge_root, questions_root = generate_demo_files(tmp_path)
    files = sorted(path for path in knowledge_root.rglob("*") if path.is_file())
    question_files = sorted(path for path in questions_root.rglob("*.md"))

    expected_knowledge_paths = {
        Path(document.folder) / document.filename for document in KNOWLEDGE_DOCUMENTS
    }
    expected_question_paths = {
        Path(folder) / "测试问题与预期答案.md" for folder in QUESTION_SETS
    }
    assert {path.relative_to(knowledge_root) for path in files} == expected_knowledge_paths
    assert {path.relative_to(questions_root) for path in question_files} == expected_question_paths
    registry = create_parser_registry()
    for path in files:
        assert path.stat().st_size < 20 * 1024 * 1024
        sections = registry.get_parser(path.suffix).parse(path)
        parsed_text = "\n".join(section.text for section in sections)
        expected = next(item for item in KNOWLEDGE_DOCUMENTS if item.filename == path.name)
        assert expected.document_code in parsed_text
        minimum_chars = 400 if path.suffix == ".xlsx" else 800
        assert len(parsed_text.strip()) >= minimum_chars


def test_question_documents_are_marked_not_for_upload(tmp_path: Path) -> None:
    _, questions_root = generate_demo_files(tmp_path)
    for folder, questions in QUESTION_SETS.items():
        path = questions_root / folder / "测试问题与预期答案.md"
        text = path.read_text(encoding="utf-8")
        assert "不要上传知识库" in text
        assert text.count("### 问题 ") == len(questions) == 10
        for number, question in enumerate(questions, start=1):
            expected_sources = (
                "、".join(question.source_files)
                if question.source_files
                else "无；应明确说明知识库缺少答案"
            )
            question_block = (
                f"### 问题 {number}\n\n"
                f"问题类型：{question.kind}\n\n"
                f"提问：{question.question}\n\n"
                f"预期要点：{'；'.join(question.expected_points)}\n\n"
                f"预期来源：{expected_sources}\n\n"
                f"通过标准：{question.pass_criteria}\n\n"
                "实际回答：\n\n实际引用："
            )
            assert question_block in text


def test_generator_rebuilds_only_its_two_output_directories(tmp_path: Path) -> None:
    unrelated = tmp_path / "保留目录" / "说明.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("不可删除", encoding="utf-8")
    knowledge_root, _ = generate_demo_files(tmp_path)
    stale = knowledge_root / "过期资料.txt"
    stale.write_text("下次生成应删除", encoding="utf-8")

    generate_demo_files(tmp_path)

    assert unrelated.read_text(encoding="utf-8") == "不可删除"
    assert not stale.exists()


def test_generator_rejects_a_filesystem_root() -> None:
    with pytest.raises(ValueError, match="文件系统根目录"):
        generate_demo_files(Path(Path.cwd().anchor))
