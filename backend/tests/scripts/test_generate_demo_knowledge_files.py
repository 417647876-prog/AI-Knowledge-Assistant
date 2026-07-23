from collections import Counter

from scripts.demo_knowledge_manifest import KNOWLEDGE_DOCUMENTS, QUESTION_SETS


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
    for item in KNOWLEDGE_DOCUMENTS:
        assert item.folder
        assert item.title
        assert len(item.sections) >= 3
        assert len("\n".join(item.sections)) >= 800
        assert all(len(section) >= 80 and "。" in section for section in item.sections)


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
