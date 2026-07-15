import json
from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import ValidationError


def valid_policy_dict() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "final_mode": "rewrite",
        "minimum_case_count": 30,
        "required_categories": [
            "keyword",
            "semantic",
            "refusal",
            "multi_turn",
            "interference",
        ],
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
                "reason": "固定评估集无 MRR 提升，用户接受受限风险。",
                "evidence": "docs/阶段3执行进度.md",
            }
        ],
    }


def write_policy(
    root: Path,
    data: dict[str, object],
    *,
    create_evidence: bool = True,
) -> Path:
    if create_evidence:
        evidence = root / "docs/阶段3执行进度.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("# 测试证据\n", encoding="utf-8")
    path = root / "policy.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def load_policy(path: Path, repo_root: Path):
    module = import_module("app.evaluation.policy")
    return module.load_stage3_quality_policy(path, repo_root=repo_root)


def test_loads_repository_policy() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    policy = load_policy(
        repo_root / "backend/config/evaluation/stage3-quality-policy.json",
        repo_root,
    )

    assert policy.final_mode == "rewrite"
    assert policy.final_thresholds.recall_at_5 == 0.85
    assert policy.waivers[0].gate_id == "stage3c.mrr_relative_gain"


def test_rejects_duplicate_waiver_gate_id(tmp_path: Path) -> None:
    data = valid_policy_dict()
    waivers = data["waivers"]
    assert isinstance(waivers, list)
    waivers.append(deepcopy(waivers[0]))

    with pytest.raises(ValidationError, match="重复 waiver gate_id"):
        load_policy(write_policy(tmp_path, data), tmp_path)


def test_rejects_unknown_waiver_gate(tmp_path: Path) -> None:
    data = valid_policy_dict()
    waivers = data["waivers"]
    assert isinstance(waivers, list)
    waiver = waivers[0]
    assert isinstance(waiver, dict)
    waiver["gate_id"] = "stage3e.recall"

    with pytest.raises(ValidationError, match="不允许豁免"):
        load_policy(write_policy(tmp_path, data), tmp_path)


def test_rejects_missing_waiver_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="豁免证据文件不存在"):
        load_policy(
            write_policy(
                tmp_path,
                valid_policy_dict(),
                create_evidence=False,
            ),
            tmp_path,
        )


def test_rejects_waiver_evidence_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# 外部证据\n", encoding="utf-8")
    data = valid_policy_dict()
    waivers = data["waivers"]
    assert isinstance(waivers, list)
    waiver = waivers[0]
    assert isinstance(waiver, dict)
    waiver["evidence"] = "../outside.md"

    with pytest.raises(ValueError, match="不在仓库内"):
        load_policy(
            write_policy(repository, data, create_evidence=False),
            repository,
        )


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("historical_thresholds", "stage3b_keyword_gain"),
        ("historical_thresholds", "stage3c_mrr_relative_gain"),
        ("historical_thresholds", "stage3d_multi_turn_gain"),
        ("final_thresholds", "recall_at_5"),
        ("final_thresholds", "citation_hit_rate"),
        ("final_thresholds", "refusal_accuracy"),
    ],
)
def test_rejects_ratio_above_one(tmp_path: Path, section: str, field: str) -> None:
    data = valid_policy_dict()
    values = data[section]
    assert isinstance(values, dict)
    values[field] = 1.1

    with pytest.raises(ValidationError):
        load_policy(write_policy(tmp_path, data), tmp_path)


def test_rejects_non_rewrite_final_mode(tmp_path: Path) -> None:
    data = valid_policy_dict()
    data["final_mode"] = "vector"

    with pytest.raises(ValidationError):
        load_policy(write_policy(tmp_path, data), tmp_path)


def test_rejects_missing_or_duplicate_required_category(tmp_path: Path) -> None:
    missing = valid_policy_dict()
    missing["required_categories"] = ["keyword", "semantic", "refusal", "multi_turn"]
    with pytest.raises(ValidationError, match="五个固定分类"):
        load_policy(write_policy(tmp_path, missing), tmp_path)

    duplicate = valid_policy_dict()
    duplicate["required_categories"] = [
        "keyword",
        "semantic",
        "refusal",
        "multi_turn",
        "multi_turn",
    ]
    with pytest.raises(ValidationError, match="五个固定分类"):
        load_policy(write_policy(tmp_path, duplicate), tmp_path)
