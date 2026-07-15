import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.evaluation.comparison import Stage3Comparison, compare_stage3_reports
from app.evaluation.policy import Stage3QualityPolicy
from app.evaluation.reporting import render_stage3_markdown
from app.evaluation.schemas import CaseResult, EvaluationProvenance, EvaluationReport
from app.evaluation.snapshot import KnowledgeBaseSnapshot

MODES = ("vector", "hybrid", "rerank", "rewrite")
CATEGORIES = ("keyword", "semantic", "refusal", "multi_turn", "interference")


def _module():
    return import_module("scripts.accept_stage3")


def valid_arguments(root: Path | None = None) -> list[str]:
    root = root or Path("acceptance-fixture")
    return [
        "--dataset",
        str(root / "stage3.jsonl"),
        "--knowledge-base-id",
        "11111111-1111-4111-8111-111111111111",
        "--policy",
        str(root / "stage3-quality-policy.json"),
        "--reports-dir",
        str(root / "reports"),
        "--markdown-output",
        str(root / "docs/验收与演示/阶段3质量验收报告.md"),
    ]


def _policy_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "final_mode": "rewrite",
        "minimum_case_count": 30,
        "required_categories": list(CATEGORIES),
        "historical_thresholds": {
            "stage3b_keyword_gain": 0.10,
            "stage3c_mrr_relative_gain": 0.05,
            "stage3d_multi_turn_gain": 0.15,
        },
        "final_thresholds": {
            "recall_at_5": 0.85,
            "citation_hit_rate": 0.90,
            "refusal_accuracy": 0.90,
        },
        "waivers": [
            {
                "gate_id": "stage3c.mrr_relative_gain",
                "approved_on": "2026-07-15",
                "minimum_allowed": 0.0,
                "reason": "重排无退化时接受当前无提升风险",
                "evidence": "evidence.md",
            }
        ],
    }


@pytest.fixture
def args(tmp_path: Path) -> argparse.Namespace:
    (tmp_path / "stage3.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "evidence.md").write_text("已批准。\n", encoding="utf-8")
    (tmp_path / "stage3-quality-policy.json").write_text(
        json.dumps(_policy_data(), ensure_ascii=False),
        encoding="utf-8",
    )
    return _module().parse_args(valid_arguments(tmp_path))


def _make_cases() -> list[CaseResult]:
    return [
        CaseResult(
            case_id=f"{category}-{index:02d}",
            category=category,
            retrieved_files=[f"{category}.md"],
            citation_files=[f"{category}.md"],
            accepted_chunk_count=1,
            recall_at_k=1.0,
            reciprocal_rank=1.0,
            citation_hit_rate=1.0,
            refused=category == "refusal",
            refusal_correct=True,
            latency_ms=10.0,
        )
        for category in CATEGORIES
        for index in range(1, 7)
    ]


def _environment(mode: str) -> dict[str, str]:
    environment = {
        "app_env": "test",
        "embedding_provider": "local",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "embedding_device": "cpu",
        "embedding_batch_size": "32",
        "chat_provider": "fake",
        "chat_model": "fake-chat",
        "embedding_dimensions": "512",
        "rag_score_threshold": "0.55",
        "rag_rrf_rank_constant": "60",
        "rag_reranker_provider": "disabled",
        "rag_reranker_model": "BAAI/bge-reranker-base",
        "rag_reranker_device": "cpu",
        "rag_reranker_batch_size": "16",
        "rag_candidate_k": "20",
        "rag_reranker_allow_fallback": "True",
        "rag_reranker_min_score": "disabled",
    }
    if mode == "vector":
        environment["rag_retrieval_mode"] = "vector"
    elif mode == "hybrid":
        environment["rag_retrieval_mode"] = "hybrid"
    else:
        environment["rag_retrieval_mode"] = "hybrid"
        environment["rag_reranker_provider"] = "local"
        environment["rag_reranker_allow_fallback"] = "False"
    return environment


def make_report(
    mode: str,
    *,
    run_id: UUID,
    snapshot: KnowledgeBaseSnapshot,
) -> EvaluationReport:
    values = {
        "vector": (0.83, 0.83, 0.83, 0.83, 20.0, 40.0),
        "hybrid": (0.93, 0.93, 0.93, 0.93, 30.0, 60.0),
        "rerank": (0.93, 0.93, 0.93, 0.93, 70.0, 110.0),
        "rewrite": (0.9666666667, 0.96, 0.9666666667, 0.9666666667, 90.0, 250.0),
    }
    recall, mrr, citation, refusal, p50, p95 = values[mode]
    return EvaluationReport(
        schema_version="1.1",
        mode=mode,
        dataset_sha256="a" * 64,
        top_k=5,
        case_count=30,
        recall_at_5=recall,
        mrr_at_5=mrr,
        citation_hit_rate=citation,
        refusal_accuracy=refusal,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        environment=_environment(mode),
        provenance=EvaluationProvenance(
            run_id=run_id,
            knowledge_base_id=snapshot.knowledge_base_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            document_count=snapshot.document_count,
            chunk_count=snapshot.chunk_count,
            generated_at=datetime(2026, 7, 15, tzinfo=UTC),
        ),
        cases=_make_cases(),
    )


def _policy() -> Stage3QualityPolicy:
    return Stage3QualityPolicy.model_validate(_policy_data())


def _comparison(*, passed: bool) -> Stage3Comparison:
    snapshot = KnowledgeBaseSnapshot(
        UUID("11111111-1111-4111-8111-111111111111"),
        "b" * 64,
        5,
        13,
    )
    run_id = UUID("22222222-2222-4222-8222-222222222222")
    reports = [make_report(mode, run_id=run_id, snapshot=snapshot) for mode in MODES]
    if not passed:
        reports[-1].recall_at_5 = 0.80
    return compare_stage3_reports(reports, _policy())


def _manifest(comparison: Stage3Comparison):
    module = _module()
    provenance = comparison.reports["rewrite"].provenance
    assert provenance is not None
    return module.Stage3AcceptanceManifest(
        run_id=provenance.run_id,
        snapshot_sha256=provenance.snapshot_sha256,
        artifacts={
            "stage3e-vector.json": "0" * 64,
            "stage3e-hybrid.json": "1" * 64,
            "stage3e-rerank.json": "2" * 64,
            "stage3e-rewrite.json": "3" * 64,
            "docs/验收与演示/阶段3质量验收报告.md": "4" * 64,
        },
        gate_statuses={gate.gate_id: gate.status for gate in comparison.gates},
        passed=comparison.passed,
    )


def _run(*, passed: bool):
    module = _module()
    comparison = _comparison(passed=passed)
    root = Path("acceptance-fixture")
    return module.AcceptanceRun(
        comparison=comparison,
        manifest=_manifest(comparison),
        report_paths={mode: root / f"stage3e-{mode}.json" for mode in MODES},
        markdown_path=root / "docs/验收与演示/阶段3质量验收报告.md",
        manifest_path=root / "stage3e-manifest.json",
    )


def passing_run():
    return _run(passed=True)


def failing_run():
    return _run(passed=False)


def write_bundle_fixture(tmp_path: Path):
    comparison = _comparison(passed=True)
    markdown = render_stage3_markdown(comparison, reproduce_command="accept-stage3")
    return _module().write_acceptance_bundle(
        comparison,
        markdown,
        reports_dir=tmp_path / "reports",
        markdown_output=tmp_path / "docs/验收与演示/阶段3质量验收报告.md",
    )


def assert_all_artifact_hashes_match(manifest, run) -> None:
    paths = {
        **{path.name: path for path in run.report_paths.values()},
        "docs/验收与演示/阶段3质量验收报告.md": run.markdown_path,
    }
    assert set(paths) == set(manifest.artifacts)
    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest.artifacts[name]


def test_parse_args_uses_fixed_stage3_outputs(tmp_path: Path) -> None:
    knowledge_base_id = uuid4()
    args = _module().parse_args(
        [
            "--dataset",
            str(tmp_path / "stage3.jsonl"),
            "--knowledge-base-id",
            str(knowledge_base_id),
            "--policy",
            str(tmp_path / "policy.json"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--markdown-output",
            str(tmp_path / "阶段3质量验收报告.md"),
        ]
    )

    assert args.knowledge_base_id == knowledge_base_id
    assert args.top_k == 5
    assert args.reports_dir.name == "reports"


def test_cli_has_no_threshold_override(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _module().parse_args([*valid_arguments(tmp_path), "--recall-threshold", "0.1"])


@pytest.mark.asyncio
async def test_runs_four_modes_in_order_with_shared_snapshot_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
    args: argparse.Namespace,
) -> None:
    module = _module()
    snapshot = KnowledgeBaseSnapshot(args.knowledge_base_id, "a" * 64, 5, 13)
    calls: list[tuple[str, UUID, KnowledgeBaseSnapshot]] = []

    async def fake_run(mode_args, settings, *, run_id, expected_snapshot):
        calls.append((mode_args.mode, run_id, expected_snapshot))
        return make_report(mode_args.mode, run_id=run_id, snapshot=snapshot)

    monkeypatch.setattr("scripts.accept_stage3.run_from_args", fake_run)
    monkeypatch.setattr(
        "scripts.accept_stage3.compute_baseline_snapshot",
        AsyncMock(return_value=snapshot),
    )
    result = await module.run_acceptance(args, Settings(_env_file=None))

    assert [call[0] for call in calls] == list(MODES)
    assert len({call[1] for call in calls}) == 1
    assert all(call[2] == snapshot for call in calls)
    provenance = result.comparison.reports["rewrite"].provenance
    assert provenance is not None
    assert provenance.run_id == calls[0][1]


def test_main_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        lambda args, settings: passing_run(),
    )

    assert _module().main(valid_arguments()) == 0


def test_main_returns_two_on_quality_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        lambda args, settings: failing_run(),
    )

    assert _module().main(valid_arguments()) == 2


def test_main_returns_one_on_input_error_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql+psycopg://private:private@localhost/private"
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        Mock(side_effect=RuntimeError(secret)),
    )

    assert _module().main(valid_arguments()) == 1
    assert secret not in capsys.readouterr().out


def test_writes_manifest_last_and_hashes_all_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replaced: list[str] = []
    real_replace = os.replace

    def recording_replace(source, target):
        replaced.append(Path(target).name)
        real_replace(source, target)

    monkeypatch.setattr("scripts.accept_stage3.os.replace", recording_replace)
    run = write_bundle_fixture(tmp_path)

    assert replaced[-1] == "stage3e-manifest.json"
    manifest = _module().Stage3AcceptanceManifest.model_validate_json(
        run.manifest_path.read_text(encoding="utf-8")
    )
    assert set(manifest.artifacts) == {
        "stage3e-vector.json",
        "stage3e-hybrid.json",
        "stage3e-rerank.json",
        "stage3e-rewrite.json",
        "docs/验收与演示/阶段3质量验收报告.md",
    }
    assert_all_artifact_hashes_match(manifest, run)


def test_replace_failure_does_not_update_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_manifest = tmp_path / "reports/stage3e-manifest.json"
    old_manifest.parent.mkdir()
    old_manifest.write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(
        "scripts.accept_stage3.os.replace",
        Mock(side_effect=[None, OSError("disk full")]),
    )

    with pytest.raises(OSError):
        write_bundle_fixture(tmp_path)
    assert old_manifest.read_text(encoding="utf-8") == '{"old": true}'


@pytest.mark.asyncio
async def test_compatibility_error_does_not_write(
    args: argparse.Namespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = KnowledgeBaseSnapshot(args.knowledge_base_id, "a" * 64, 5, 13)

    async def fake_run(mode_args, settings, *, run_id, expected_snapshot):
        return make_report(mode_args.mode, run_id=run_id, snapshot=snapshot)

    writer = Mock()
    monkeypatch.setattr("scripts.accept_stage3.run_from_args", fake_run)
    monkeypatch.setattr(
        "scripts.accept_stage3.compute_baseline_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(
        "scripts.accept_stage3.compare_stage3_reports",
        Mock(side_effect=ValueError("不兼容")),
    )
    monkeypatch.setattr("scripts.accept_stage3.write_acceptance_bundle", writer)

    with pytest.raises(ValueError, match="不兼容"):
        await _module().run_acceptance(args, Settings(_env_file=None))
    writer.assert_not_called()


def test_quality_failure_still_writes_all_artifacts(tmp_path: Path) -> None:
    comparison = _comparison(passed=False)
    markdown = render_stage3_markdown(comparison, reproduce_command="accept-stage3")

    run = _module().write_acceptance_bundle(
        comparison,
        markdown,
        reports_dir=tmp_path / "reports",
        markdown_output=tmp_path / "docs/验收与演示/阶段3质量验收报告.md",
    )

    assert run.manifest.passed is False
    assert all(path.is_file() for path in run.report_paths.values())
    assert run.markdown_path.is_file()
    assert run.manifest_path.is_file()


def test_temporary_directory_is_removed_and_user_file_is_preserved(tmp_path: Path) -> None:
    user_report = tmp_path / "reports/user-report.json"
    user_report.parent.mkdir()
    user_report.write_text("用户文件", encoding="utf-8")

    write_bundle_fixture(tmp_path)

    assert user_report.read_text(encoding="utf-8") == "用户文件"
    assert not list((tmp_path / "reports").glob(".stage3e-*"))


def test_manifest_rejects_wrong_artifact_count_or_digest() -> None:
    manifest = _manifest(_comparison(passed=True)).model_dump(mode="python")
    manifest["artifacts"].pop("stage3e-vector.json")
    with pytest.raises(ValueError, match="五个产物"):
        _module().Stage3AcceptanceManifest.model_validate(manifest)

    manifest = _manifest(_comparison(passed=True)).model_dump(mode="python")
    manifest["artifacts"]["stage3e-vector.json"] = "not-a-digest"
    with pytest.raises(ValueError, match="无效 SHA-256"):
        _module().Stage3AcceptanceManifest.model_validate(manifest)
