from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation import schemas


def make_case(**updates: object) -> schemas.CaseResult:
    values: dict[str, object] = {
        "case_id": "keyword-01",
        "retrieved_files": ["员工手册.docx"],
        "citation_files": ["员工手册.docx"],
        "accepted_chunk_count": 1,
        "recall_at_k": 1.0,
        "reciprocal_rank": 1.0,
        "refused": False,
        "refusal_correct": True,
        "latency_ms": 2.0,
    }
    values.update(updates)
    return schemas.CaseResult.model_validate(values)


def make_provenance() -> schemas.EvaluationProvenance:
    return schemas.EvaluationProvenance(
        run_id=uuid4(),
        knowledge_base_id=uuid4(),
        snapshot_sha256="b" * 64,
        document_count=5,
        chunk_count=13,
        generated_at=datetime.now(UTC),
    )


def make_report(**updates: object) -> schemas.EvaluationReport:
    values: dict[str, object] = {
        "mode": "vector",
        "dataset_sha256": "a" * 64,
        "top_k": 5,
        "case_count": 1,
        "recall_at_5": 1.0,
        "mrr_at_5": 1.0,
        "citation_hit_rate": 1.0,
        "refusal_accuracy": 1.0,
        "latency_p50_ms": 2.0,
        "latency_p95_ms": 2.0,
        "cases": [make_case()],
    }
    values.update(updates)
    return schemas.EvaluationReport.model_validate(values)


def test_report_10_remains_readable_without_provenance() -> None:
    assert make_report().schema_version == "1.0"


def test_report_11_requires_provenance_category_and_case_citation() -> None:
    provenance = make_provenance()
    report = make_report(
        schema_version="1.1",
        provenance=provenance,
        cases=[make_case(category="keyword", citation_hit_rate=1.0)],
    )
    assert report.provenance == provenance

    with pytest.raises(ValidationError, match="1.1 报告必须包含溯源信息"):
        make_report(schema_version="1.1")
    with pytest.raises(ValidationError, match="1.1 案例必须包含 category"):
        make_report(schema_version="1.1", provenance=provenance)
    with pytest.raises(ValidationError, match="1.1 案例必须包含 citation_hit_rate"):
        make_report(
            schema_version="1.1",
            provenance=provenance,
            cases=[make_case(category="keyword")],
        )


def test_report_rejects_case_count_mismatch() -> None:
    with pytest.raises(ValidationError, match="case_count 必须等于 cases 数量"):
        make_report(case_count=2)
