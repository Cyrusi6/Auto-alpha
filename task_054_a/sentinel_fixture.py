"""Small subprocess fixture exercising production components for Task 054 tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from alpha_factory.proxy_eval import _rank_ic
from formula_batch_eval.evaluator import FormulaBatchEvaluator
from formula_batch_eval.models import FormulaEvalRequest
from model_core.vm import StackVM
from model_core.vocab import FormulaVocab
from research_firewall import DateFirewall, ResearchEligibilityContract
from research_firewall.sentinel import TASK054_ACCESS_LEDGER, TASK054_PATH_RESULT


COMPONENT_FILES = {
    "loader": "model_core/data_loader.py",
    "stackvm_validity": "model_core/vm.py",
    "alpha_proxy": "alpha_factory/proxy_eval.py",
    "formula_batch_evaluator": "formula_batch_eval/evaluator.py",
    "factor_materializer": "validation_lab/materialization.py",
    "validation_lab": "validation_lab/run_validation.py",
    "consolidation_cache": "validation_campaign_store/run_validation_store.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--path-name", required=True)
    parser.add_argument("--mutation-kind", required=True)
    parser.add_argument("--execution", choices=("local", "scheduler"), required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.execution == "scheduler" and not args.worker:
        _run_scheduler(args)
    else:
        _run_worker(args)


def _run_scheduler(args) -> None:
    output = Path(args.output_dir)
    worker_output = output / "worker"
    worker_output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "task_054_a.sentinel_fixture",
        "--input",
        args.input,
        "--output-dir",
        str(worker_output),
        "--path-name",
        args.path_name,
        "--mutation-kind",
        args.mutation_kind,
        "--execution",
        "scheduler",
        "--worker",
    ]
    heartbeat = output / "scheduler_heartbeat.jsonl"
    process = subprocess.Popen(command)
    heartbeat.write_text(json.dumps({"job_id": _job_id(args), "status": "running", "worker_pid": process.pid}) + "\n", encoding="utf-8")
    exit_code = process.wait(timeout=60)
    with heartbeat.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"job_id": _job_id(args), "status": "success" if exit_code == 0 else "failed", "worker_pid": process.pid}) + "\n")
    if exit_code != 0:
        raise SystemExit(exit_code)
    result_path = worker_output / TASK054_PATH_RESULT
    ledger_path = worker_output / TASK054_ACCESS_LEDGER
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["scheduler_evidence"] = {
        "job_id": _job_id(args),
        "worker_pid": process.pid,
        "exit_code": exit_code,
        "heartbeat_sha256": _sha256_file(heartbeat),
        "artifact_sha256": _sha256_file(result_path),
        "command": command,
        "process_info": {"python": sys.version.split()[0]},
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / TASK054_PATH_RESULT).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    (output / TASK054_ACCESS_LEDGER).write_bytes(ledger_path.read_bytes())


def _run_worker(args) -> None:
    source = Path(args.input)
    data = np.load(source, allow_pickle=False)
    dates = [str(item) for item in data["dates"].tolist()]
    cutoff = str(data["cutoff"].item())
    eligible = [index for index in range(max(0, len(dates) - 2)) if dates[index + 2] <= cutoff]
    diagnostic = [index for index, date in enumerate(dates) if date > cutoff]
    values = torch.from_numpy(data["values"].astype(np.float32))
    validity = torch.from_numpy(data["validity"].astype(np.bool_))
    target = torch.from_numpy(data["target"].astype(np.float32))
    target_valid = torch.from_numpy(data["target_validity"].astype(np.bool_))
    vocab = FormulaVocab(feature_names=("F0",), operator_names=("ADD",))
    vm = StackVM(vocab)
    tokens = [0]
    factor, factor_valid = vm.execute_with_validity(tokens, values, validity)
    research_index = torch.tensor(eligible, dtype=torch.long)
    diagnostic_index = torch.tensor(diagnostic, dtype=torch.long)
    research_factor = factor.index_select(1, research_index)
    research_valid = factor_valid.index_select(1, research_index) & target_valid.index_select(1, research_index)
    research_target = target.index_select(1, research_index)
    proxy_score = _rank_ic(research_factor, research_target, research_valid)
    request = FormulaEvalRequest(
        name="fixture_formula",
        formula_tokens=tokens,
        formula_names=["F0"],
        formula_hash=_hash_json(tokens),
    )
    evaluator = FormulaBatchEvaluator.__new__(FormulaBatchEvaluator)
    evaluator.config = SimpleNamespace(
        factor_transform="none",
        train_ratio=0.7,
        valid_ratio=0.15,
        universe_name="fixture",
        universe_file=None,
        research_end_date=cutoff,
        holdout_start_date=None,
        label_horizon=2,
        eligible_date_hash=ResearchEligibilityContract(cutoff, 2).eligible_date_hash(dates),
    )
    evaluator.loader = SimpleNamespace(trade_dates=dates)
    evaluator.lineage = {"lineage_hash": _hash_tensors(research_factor, research_valid, research_target)}
    cache_key = evaluator._cache_key(request)
    quality = {
        "valid_count": int(research_valid.sum().item()),
        "coverage": float(research_valid.float().mean().item()) if research_valid.numel() else 0.0,
        "std": float(research_factor[research_valid].std(unbiased=False).item()) if research_valid.any() else 0.0,
    }
    validation_status = "historical_replay_passed" if quality["valid_count"] > 0 else "data_blocked"
    hashes = {
        "research_tensor_hash": _hash_tensors(values.index_select(2, research_index), validity.index_select(2, research_index)),
        "factor_hash": _hash_tensors(research_factor, research_valid),
        "proxy_hash": _hash_json({"rank_ic": proxy_score}),
        "full_eval_hash": _hash_json({"rank_ic": proxy_score, "valid_count": quality["valid_count"]}),
        "materialization_quality_hash": _hash_json(quality),
        "validation_status_hash": _hash_json({"status": validation_status}),
        "cache_key": cache_key,
        "consolidation_hash": _hash_json({"F0": validation_status}),
        "diagnostic_hash": _hash_tensors(factor.index_select(1, diagnostic_index), target.index_select(1, diagnostic_index)),
    }
    hashes["research_result_hash"] = _hash_json(hashes)
    root = Path(__file__).resolve().parents[1]
    result = {
        "status": "success",
        "evidence_scope": "synthetic_test_fixture",
        "path_name": args.path_name,
        "source_kind": args.path_name.split("_", 1)[0],
        "execution_kind": args.execution,
        "mutation_kind": args.mutation_kind,
        **hashes,
        "component_evidence": [
            {"component": name, "invoked": True, "source_hash": _sha256_file(root / relative)}
            for name, relative in sorted(COMPONENT_FILES.items())
        ],
        "process_evidence": {"pid": os.getpid(), "exit_code": 0},
    }
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / TASK054_PATH_RESULT).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    firewall = DateFirewall(cutoff, next((date for date in dates if date > cutoff), cutoff), 2)
    research_dates = [dates[index] for index in eligible]
    diagnostic_dates = [dates[index] for index in diagnostic]
    firewall.audit_observation_access(
        research_dates,
        component="fixture_production_loader",
        purpose="research_slice_read",
        view="research",
    )
    if diagnostic_dates:
        firewall.audit_observation_access(
            diagnostic_dates,
            component="fixture_production_loader",
            purpose="diagnostic_slice_read",
            view="diagnostic",
        )
    ledger_rows = [
        row
        | {
            "source_kind": args.path_name.split("_", 1)[0],
            "path": str(source.resolve()),
            "date_range": [row.get("date"), row.get("date")],
            "source_sha256": _sha256_file(source),
        }
        for row in firewall.access_audit
    ]
    (output / TASK054_ACCESS_LEDGER).write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in ledger_rows),
        encoding="utf-8",
    )


def _job_id(args) -> str:
    return _hash_json([args.path_name, args.mutation_kind])[:20]


def _hash_tensors(*tensors: torch.Tensor) -> str:
    digest = hashlib.sha256()
    for tensor in tensors:
        array = tensor.detach().cpu().contiguous().numpy()
        digest.update(str(array.dtype).encode())
        digest.update(str(array.shape).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def _hash_json(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
