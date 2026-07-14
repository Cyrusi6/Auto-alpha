from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from research_firewall.sentinel import ProductionSentinelCommand, ProductionSentinelPlan, run_production_firewall_sentinel


def test_task054_blackbox_sentinel_runs_four_real_subprocess_paths(tmp_path: Path):
    dates = np.array(["20240527", "20240528", "20240529", "20240530", "20240531", "20240603"], dtype="U8")
    values = np.arange(24, dtype=np.float32).reshape(4, 1, 6)
    validity = np.ones_like(values, dtype=np.bool_)
    target = np.arange(24, dtype=np.float32).reshape(4, 6) / 100.0
    target_validity = np.ones_like(target, dtype=np.bool_)
    sources = {kind: {} for kind in ("raw", "matrix")}
    for kind in sources:
        sources[kind]["baseline"] = tmp_path / f"{kind}_baseline.npz"
        _write_fixture(sources[kind]["baseline"], dates, values, validity, target, target_validity)
    post_values = values.copy()
    post_target = target.copy()
    post_values[:, :, -1] += 1000
    post_target[:, -1] += 1000
    for kind in sources:
        sources[kind]["post_cutoff"] = tmp_path / f"{kind}_post.npz"
        _write_fixture(sources[kind]["post_cutoff"], dates, post_values, validity, post_target, target_validity)
    inside_values = values.copy()
    inside_target = target.copy()
    inside_values[:, :, 1] += np.array([1000, -200, 300, -400], dtype=np.float32)[:, None]
    inside_target[:, 1] += np.array([-5, 8, -2, 4], dtype=np.float32)
    for kind in sources:
        sources[kind]["inside_cutoff"] = tmp_path / f"{kind}_inside.npz"
        _write_fixture(sources[kind]["inside_cutoff"], dates, inside_values, validity, inside_target, target_validity)

    commands = []
    for mutation_kind in ("baseline", "post_cutoff", "inside_cutoff"):
        for path_name in ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler"):
            execution = path_name.split("_", 1)[1]
            source = sources[path_name.split("_", 1)[0]][mutation_kind]
            output = tmp_path / "jobs" / mutation_kind / path_name
            command = (
                sys.executable,
                "-m",
                "task_054_a.sentinel_fixture",
                "--input",
                str(source),
                "--output-dir",
                str(output),
                "--path-name",
                path_name,
                "--mutation-kind",
                mutation_kind,
                "--execution",
                execution,
            )
            commands.append(
                ProductionSentinelCommand(
                    path_name=path_name,
                    source_kind=path_name.split("_", 1)[0],
                    execution_kind=execution,
                    mutation_kind=mutation_kind,
                    command=command,
                    output_dir=str(output),
                    timeout_seconds=60,
                )
            )
    result = run_production_firewall_sentinel(
        ProductionSentinelPlan(
            tuple(commands),
            research_end_date="20240530",
            label_horizon=2,
            allow_synthetic_test_fixture=True,
        ),
        tmp_path / "sentinel",
    )
    assert result["status"] == "passed"
    assert result["proof"]["access_violation_count"] == 0
    assert all(not fields for fields in result["proof"]["post_cutoff_research_changes"].values())
    assert all(result["proof"]["diagnostic_changed"].values())
    assert all(result["proof"]["inside_cutoff_cache_misses"].values())
    baseline_rows = result["executions"]["baseline"]
    assert len({row["launcher_evidence"]["pid"] for row in baseline_rows.values()}) == 4
    scheduler_rows = [row for name, row in baseline_rows.items() if name.endswith("scheduler")]
    assert all(row["scheduler_evidence"]["worker_pid"] != row["launcher_evidence"]["pid"] for row in scheduler_rows)
    assert all(row["scheduler_evidence"]["heartbeat_sha256"] for row in scheduler_rows)


def test_task054_production_sentinel_rejects_synthetic_fixture(tmp_path: Path):
    dates = np.array(["20240527", "20240528", "20240529", "20240530", "20240531"], dtype="U8")
    source = tmp_path / "fixture.npz"
    values = np.ones((4, 1, len(dates)), dtype=np.float32)
    _write_fixture(source, dates, values, values.astype(np.bool_), values[:, 0], values[:, 0].astype(np.bool_))
    commands = []
    for mutation_kind in ("baseline", "post_cutoff", "inside_cutoff"):
        for path_name in ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler"):
            execution = path_name.split("_", 1)[1]
            output = tmp_path / mutation_kind / path_name
            commands.append(
                ProductionSentinelCommand(
                    path_name=path_name,
                    source_kind=path_name.split("_", 1)[0],
                    execution_kind=execution,
                    mutation_kind=mutation_kind,
                    command=(
                        sys.executable,
                        "-m",
                        "task_054_a.sentinel_fixture",
                        "--input",
                        str(source),
                        "--output-dir",
                        str(output),
                        "--path-name",
                        path_name,
                        "--mutation-kind",
                        mutation_kind,
                        "--execution",
                        execution,
                    ),
                    output_dir=str(output),
                )
            )
    import pytest

    with pytest.raises(RuntimeError, match="synthetic sentinel evidence forbidden"):
        run_production_firewall_sentinel(
            ProductionSentinelPlan(tuple(commands), research_end_date="20240530", label_horizon=2),
            tmp_path / "production",
        )


def _write_fixture(path, dates, values, validity, target, target_validity):
    np.savez(
        path,
        dates=dates,
        cutoff=np.array("20240530"),
        values=values,
        validity=validity,
        target=target,
        target_validity=target_validity,
    )
