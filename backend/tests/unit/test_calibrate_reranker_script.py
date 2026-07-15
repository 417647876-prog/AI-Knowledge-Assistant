import hashlib
import json
from pathlib import Path

import pytest

from scripts import calibrate_reranker


class StubReranker:
    def __init__(self, scores_by_question: dict[str, list[float]]) -> None:
        self._scores_by_question = scores_by_question
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return list(self._scores_by_question[query])


def write_dataset(path: Path, cases: list[dict[str, object]]) -> bytes:
    raw = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases).encode()
    path.write_bytes(raw)
    return raw


def paired_cases() -> list[dict[str, object]]:
    return [
        {
            "id": "leave-positive",
            "question": "年假怎么计算？",
            "document": "年假为五天。",
            "relevant": True,
        },
        {
            "id": "password-positive",
            "question": "密码有什么要求？",
            "document": "密码长度不少于十二位。",
            "relevant": True,
        },
        {
            "id": "leave-negative",
            "question": "年假怎么计算？",
            "document": "机房访客需要登记。",
            "relevant": False,
        },
        {
            "id": "password-negative",
            "question": "密码有什么要求？",
            "document": "员工每周最多远程办公两天。",
            "relevant": False,
        },
    ]


def test_parse_args_uses_fixed_defaults_and_accepts_overrides() -> None:
    args = calibrate_reranker.parse_args(["--dataset", "cases.jsonl", "--output", "report.json"])

    assert args.dataset == Path("cases.jsonl")
    assert args.output == Path("report.json")
    assert args.model == "BAAI/bge-reranker-base"
    assert args.device == "cpu"
    assert args.batch_size == 16

    overridden = calibrate_reranker.parse_args(
        [
            "--dataset",
            "cases.jsonl",
            "--output",
            "report.json",
            "--model",
            "BAAI/test",
            "--device",
            "cuda",
            "--batch-size",
            "256",
        ]
    )
    assert overridden.model == "BAAI/test"
    assert overridden.device == "cuda"
    assert overridden.batch_size == 256


@pytest.mark.parametrize("batch_size", ["0", "257", "not-a-number"])
def test_parse_args_rejects_batch_size_outside_range(batch_size: str) -> None:
    with pytest.raises(SystemExit):
        calibrate_reranker.parse_args(
            [
                "--dataset",
                "cases.jsonl",
                "--output",
                "report.json",
                "--batch-size",
                batch_size,
            ]
        )


@pytest.mark.asyncio
async def test_run_calibration_batches_by_question_and_restores_case_order(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    raw = write_dataset(dataset, paired_cases())
    provider = StubReranker(
        {
            "年假怎么计算？": [1.0, -2.0],
            "密码有什么要求？": [2.0, -1.0],
        }
    )

    report = await calibrate_reranker.run_calibration(
        dataset=dataset,
        model_name="BAAI/test-reranker",
        device="cpu",
        provider=provider,
    )

    assert report.recommended_min_score == pytest.approx(0.0)
    assert report.false_accept_rate == 0.0
    assert report.positive_accept_rate == 1.0
    assert report.dataset_sha256 == hashlib.sha256(raw).hexdigest()
    assert provider.calls == [
        ("年假怎么计算？", ["年假为五天。", "机房访客需要登记。"]),
        (
            "密码有什么要求？",
            ["密码长度不少于十二位。", "员工每周最多远程办公两天。"],
        ),
    ]


@pytest.mark.parametrize("bad_scores", [[1.0], [1.0, float("nan")]])
@pytest.mark.asyncio
async def test_run_calibration_rejects_invalid_provider_scores_without_echoing_data(
    tmp_path: Path, bad_scores: list[float]
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    secret = "private-calibration-document"
    write_dataset(
        dataset,
        [
            {
                "id": "positive-1",
                "question": "校准问题",
                "document": secret,
                "relevant": True,
            },
            {
                "id": "negative-1",
                "question": "校准问题",
                "document": "无关片段",
                "relevant": False,
            },
        ],
    )
    provider = StubReranker({"校准问题": bad_scores})

    with pytest.raises(ValueError, match="Reranker 返回") as raised:
        await calibrate_reranker.run_calibration(
            dataset=dataset,
            model_name="BAAI/test",
            device="cpu",
            provider=provider,
        )

    assert secret not in str(raised.value)
    assert str(bad_scores) not in str(raised.value)


def test_main_writes_utf8_json_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    output = tmp_path / "reports" / "calibration.json"
    write_dataset(dataset, paired_cases())
    provider = StubReranker(
        {
            "年假怎么计算？": [1.0, -2.0],
            "密码有什么要求？": [2.0, -1.0],
        }
    )
    monkeypatch.setattr(
        calibrate_reranker,
        "get_local_reranker_provider",
        lambda model_name, device, batch_size: provider,
    )

    calibrate_reranker.main(
        [
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--model",
            "中文测试模型",
        ]
    )

    serialized = output.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert payload["model_name"] == "中文测试模型"
    assert "中文测试模型" in serialized
    assert "\\u4e2d" not in serialized
    assert serialized.endswith("\n")


def test_main_fails_without_writing_report_when_no_threshold_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    output = tmp_path / "report.json"
    secret = "private-calibration-document"
    write_dataset(
        dataset,
        [
            {
                "id": "positive-1",
                "question": "校准问题",
                "document": secret,
                "relevant": True,
            },
            {
                "id": "negative-1",
                "question": "校准问题",
                "document": "无关片段",
                "relevant": False,
            },
        ],
    )
    monkeypatch.setattr(
        calibrate_reranker,
        "get_local_reranker_provider",
        lambda model_name, device, batch_size: StubReranker({"校准问题": [0.1, 0.9]}),
    )

    with pytest.raises(SystemExit) as raised:
        calibrate_reranker.main(["--dataset", str(dataset), "--output", str(output)])

    assert raised.value.code != 0
    assert secret not in str(raised.value)
    assert not output.exists()
