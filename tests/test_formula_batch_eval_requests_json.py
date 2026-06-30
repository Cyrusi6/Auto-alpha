import hashlib
import json

import pytest

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from formula_batch_eval import requests_from_candidates, run_batch_eval
from research.candidates import default_candidates


def _prepare_sample_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def _request_payloads(count: int = 3) -> list[dict]:
    return [request.to_dict() for request in requests_from_candidates(default_candidates()[:count])]


def test_formula_batch_eval_cli_accepts_requests_json(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    requests_path = tmp_path / "requests.json"
    payloads = _request_payloads(3)
    requests_path.write_text(json.dumps({"requests": payloads}, ensure_ascii=False), encoding="utf-8")

    exit_code = run_batch_eval.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "eval_json"),
            "--requests-json",
            str(requests_path),
            "--factor-transform",
            "winsorize_zscore",
            "--min-coverage",
            "0.5",
            "--device",
            "cpu",
            "--continue-on-error",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["total"] == len(payloads)
    assert (tmp_path / "eval_json" / "formula_batch_eval_result.json").exists()
    assert (tmp_path / "eval_json" / "formula_eval_results.jsonl").exists()


def test_formula_batch_eval_cli_accepts_requests_jsonl_and_shards(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    requests_path = tmp_path / "requests.jsonl"
    payloads = _request_payloads(3)
    requests_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in payloads) + "\n", encoding="utf-8")

    exit_code = run_batch_eval.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "eval_jsonl"),
            "--requests-jsonl",
            str(requests_path),
            "--device",
            "cpu",
            "--continue-on-error",
            "--shard-id",
            "0",
            "--shard-count",
            "2",
            "--write-shard-manifest",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    expected_hashes = [
        row["formula_hash"]
        for row in payloads
        if int(hashlib.sha256(row["formula_hash"].encode("utf-8")).hexdigest()[:8], 16) % 2 == 0
    ]
    assert exit_code == 0
    assert payload["summary"]["total"] == len(expected_hashes)
    assert (tmp_path / "eval_jsonl" / "formula_batch_eval_result.json").exists()
    assert (tmp_path / "eval_jsonl" / "formula_eval_results.jsonl").exists()
    assert (tmp_path / "eval_jsonl" / "shard_manifest.json").exists()


def test_formula_batch_eval_requests_json_reports_missing_field(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    requests_path = tmp_path / "bad_requests.json"
    bad = _request_payloads(1)[0]
    bad.pop("formula_hash")
    requests_path.write_text(json.dumps([bad], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        run_batch_eval.main(
            [
                "--data-dir",
                str(data_dir),
                "--factor-store-dir",
                str(tmp_path / "store"),
                "--report-dir",
                str(tmp_path / "reports"),
                "--output-dir",
                str(tmp_path / "eval_bad"),
                "--requests-json",
                str(requests_path),
                "--device",
                "cpu",
            ]
        )

    assert exc.value.code == 2
    assert "request[0] missing required field: formula_hash" in capsys.readouterr().err
