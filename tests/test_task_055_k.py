from __future__ import annotations

import json
import inspect
import os
import subprocess
from pathlib import Path

import pytest
import numpy as np

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.network_capability import (
    TushareExecutionCapability,
    TushareExecutionCapabilityError,
    _issue_task055j_execution_capability,
    _validated_task055k_execution_capability,
)
from data_pipeline.ashare.providers.tushare_client import TushareHttpClient, TushareNetworkError
from data_pipeline.ashare.request_identity import TushareRequestIdentity
from dev_tools.task055kr_harness import (
    _lightweight_stages,
    run_lightweight_recovery_matrix,
    synthetic_accepted_response,
)
from task_055_h.io import canonical_hash, read_json
from task_055_f.valuation import (
    publish_valuation_projection,
    valuation_surface_from_projection,
)
from task_055_k.application import APPLICATION_STAGES, runtime_semantic_source_hash
from task_055_k.authority import (
    normalize_ordered_keys,
    publish_historical_supersession,
    validate_candidate_checkpoint,
)
from task_055_k.broker import (
    Task055KBrokerError,
    _validate_receipt_against_reservation,
)
from task_055_k.contracts import CANARY
from task_055_k.immutable import write_immutable_generation
from task_055_k import network_cli
from task_055_k.source_tree import git_index_source_entries
from task_055_k.stage_machine import ApplicationStageMachine, Task055KStageMachineError


def _ordered_keys() -> list[dict]:
    payload = read_json("evidence/task_055_j/task055j_scrubbed_evidence.json")
    return normalize_ordered_keys(payload["ordered_exact_daily_keys"])


def _accepted(tmp_path: Path, *, positive: bool = False):
    items = (
        [["000413.SZ", "20160726", 10.0, 11.0, 9.0, 10.5, 10.0, 100.0, 1000.0]]
        if positive
        else []
    )
    return synthetic_accepted_response(
        authority_root=tmp_path / "authority",
        ordered_keys=_ordered_keys(),
        implementation_commit="c" * 40,
        source_root="d" * 64,
        items=items,
    )


def _machine(tmp_path: Path, accepted, *, suffix: str = "app") -> ApplicationStageMachine:
    return ApplicationStageMachine(
        application_root=tmp_path / suffix,
        application_spec_hash=canonical_hash(["fixture", suffix]),
        evidence_scope="synthetic_rehearsal_only",
        accepted=accepted,
        context={
            "context_root": canonical_hash("fixture-context"),
            "runtime_semantic_source_hash": canonical_hash("fixture-source"),
        },
        stages=_lightweight_stages(),
    )


def test_fixed_canary_has_three_distinct_identities() -> None:
    assert CANARY["request_fingerprint"] == "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869"
    assert CANARY["transport_identity"] == "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e"
    assert len(
        {
            CANARY["request_fingerprint"],
            CANARY["transport_identity"],
            CANARY["evidence_use_identity"],
        }
    ) == 3


def test_all_python_capability_and_generic_client_paths_fail_closed() -> None:
    capability = TushareExecutionCapability(
        authority_content_hash="a" * 64,
        final_execution_seal_hash="b" * 64,
        api_name="daily",
        params={"ts_code": "000413.SZ", "trade_date": "20160726"},
        fields=list(CANARY["fields"]),
        identity=TushareRequestIdentity(
            CANARY["request_fingerprint"],
            CANARY["transport_identity"],
            CANARY["evidence_use_identity"],
        ),
        attempt_id="c" * 64,
        broker_contract_hash="d" * 64,
        grant_verified=True,
        _validation_token=object(),
    )
    with pytest.raises(TushareExecutionCapabilityError, match="canonical_transport_gateway"):
        capability.authorize("daily", capability.params, capability.fields)
    for issuer in (_issue_task055j_execution_capability, _validated_task055k_execution_capability):
        with pytest.raises(TushareExecutionCapabilityError, match="canonical_transport_gateway"):
            issuer()
    with pytest.raises(TushareNetworkError, match="task055k_execution_capability"):
        TushareHttpClient(AShareDataConfig(tushare_token="must-not-be-used"))
    with pytest.raises(TypeError):
        TushareHttpClient(
            AShareDataConfig(tushare_token="must-not-be-used"),
            urlopen=lambda *_args, **_kwargs: None,
        )


def test_all_legacy_network_entrypoints_fail_before_injected_io() -> None:
    from task_052_a.backfill import run_governed_backfill
    from task_055_c.cascade import execute_transport_stage
    from task_055_d.network import execute_plan
    from task_055_f.network import execute_canary as execute_f_canary
    from task_055_f.network import execute_l1_resume as execute_f_resume
    from task_055_g.network_state import execute_l1_canary, execute_l1_resume
    from task_055_g.network_state import execute_l2_canary, execute_l2_resume
    from task_055_h.network import (
        load_file_credential_after_offline_gates,
        ordered_future_canary_gate,
    )
    from task_055_i.executor import execute_single_canary as execute_i_canary
    from task_055_j.executor import execute_single_canary as execute_j_canary

    calls = {"credential": 0, "network": 0}

    def forbidden_credential(*_args, **_kwargs):
        calls["credential"] += 1
        raise AssertionError("credential boundary reached")

    def forbidden_network(*_args, **_kwargs):
        calls["network"] += 1
        raise AssertionError("network boundary reached")

    probes = [
        lambda: run_governed_backfill(None),
        lambda: execute_transport_stage(
            plan_manifest="x", output_root="x", stage="L1", request_budget=0
        ),
        lambda: execute_plan(
            plan={}, output_root="x", cache_roots=[], allow_network=True,
            sealed_plan_hash="x", request_budget=1,
            credential_loader=forbidden_credential, client_factory=forbidden_network,
        ),
        lambda: execute_f_canary(
            causal_manifest="x", output_root="x", cache_data_root="x",
            allow_network=True, sealed_plan_hash="x", repo_root="x", governed_root="x",
            credential_loader=forbidden_credential, client_factory=forbidden_network,
        ),
        lambda: execute_f_resume(
            causal_manifest="x", canary_acceptance_manifest="x", output_root="x",
            cache_data_root="x", allow_network=True, sealed_plan_hash="x",
            repo_root="x", governed_root="x",
            credential_loader=forbidden_credential, client_factory=forbidden_network,
        ),
        lambda: execute_l1_canary(
            state_root="x", plan_manifest={}, allow_network=True,
            sealed_plan_hash="x", request_executor=forbidden_network,
        ),
        lambda: execute_l1_resume(
            state_root="x", plan_manifest={}, canary_manifest={}, allow_network=True,
            sealed_plan_hash="x", request_executor=forbidden_network,
        ),
        lambda: execute_l2_canary(
            state_root="x", plan_manifest={}, allow_network=True,
            sealed_plan_hash="x", request_executor=forbidden_network,
        ),
        lambda: execute_l2_resume(
            state_root="x", plan_manifest={}, canary_manifest={}, allow_network=True,
            sealed_plan_hash="x", request_executor=forbidden_network,
        ),
        lambda: load_file_credential_after_offline_gates(
            credential_file="x", forbidden_root_identities={}
        ),
        lambda: ordered_future_canary_gate(
            authorization_seal="x", allow_network=True, sealed_plan_hash="x",
            tls_checker=forbidden_network, credential_loader=forbidden_credential,
        ),
        lambda: execute_i_canary(
            runtime_authority="x", reviewed_authority_hash="x",
            credential_file="x", allow_network=True,
        ),
        lambda: execute_j_canary(
            final_execution_seal="x", reviewed_final_execution_seal_hash="x",
            credential_file="x", allow_network=True,
        ),
    ]
    for probe in probes:
        with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
            probe()
    assert calls == {"credential": 0, "network": 0}


@pytest.mark.parametrize("positive", [False, True])
def test_synthetic_boundary_uses_real_parser_signed_receipt_and_v3_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, positive: bool
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    accepted, checkpoint = _accepted(tmp_path, positive=positive)
    assert accepted.checkpoint["content_hash"] == checkpoint["content_hash"]
    assert len(accepted.records) == int(positive)
    assert accepted.receipt["response_fields"] == CANARY["fields"]
    assert accepted.receipt["empty_response_semantics"] == (
        None if positive else "vendor_absence_only"
    )
    assert accepted.receipt["response_payload_hash"]
    assert accepted.acceptance["resume_authorized"] is False
    cache = json.loads(accepted.cache_path.read_text(encoding="utf-8"))
    assert cache["schema_version"] == "tushare_cache_envelope.v3"
    assert cache["provider"]["endpoint"] == "https://api.tushare.pro"


@pytest.mark.parametrize(
    "field,value",
    [
        ("signature", "AAAA"),
        ("response_payload_hash", "0" * 64),
        ("response_fields", ["ts_code"]),
        ("tls_attestation", {"status": "synthetic_passed"}),
        ("attempt_id", "f" * 64),
    ],
)
def test_receipt_tampering_is_rejected_after_self_hash_rewrite(
    tmp_path: Path, field: str, value
) -> None:
    accepted, _checkpoint = _accepted(tmp_path)
    semantic = {
        key: val
        for key, val in accepted.receipt.items()
        if key not in {"content_hash", "generation_id", "manifest_path"}
    }
    semantic[field] = value
    forged = write_immutable_generation(
        tmp_path / "forged",
        prefix="forged_receipt",
        manifest_name="transport_receipt.json",
        semantic=semantic,
    )
    with pytest.raises(Exception):
        _validate_receipt_against_reservation(
            forged["manifest_path"],
            checkpoint=accepted.checkpoint,
            reservation=accepted.reservation,
        )


def test_candidate_checkpoint_rejects_self_consistent_second_key_tamper(tmp_path: Path) -> None:
    _accepted_response, checkpoint = _accepted(tmp_path)
    payload = read_json(checkpoint["manifest_path"])
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"content_hash", "generation_id", "manifest_path"}
    }
    semantic["ordered_exact_daily_keys"][1]["trade_date"] = "20160101"
    semantic["ordered_key_root"] = canonical_hash(semantic["ordered_exact_daily_keys"])
    forged = write_immutable_generation(
        tmp_path / "forged_checkpoint",
        prefix="forged_checkpoint",
        manifest_name="candidate_checkpoint.json",
        semantic=semantic,
    )
    with pytest.raises(Exception):
        validate_candidate_checkpoint(forged["manifest_path"])


def test_stage_machine_executes_once_and_resumes_all_stages(tmp_path: Path) -> None:
    accepted, _checkpoint = _accepted(tmp_path)
    machine = _machine(tmp_path, accepted)
    first = machine.run()
    resume = machine.run()
    assert first["resume_summary"] == {
        "executed_stage_count": 12,
        "reused_stage_count": 0,
        "recomputed_stage_count": 0,
    }
    assert resume["resume_summary"] == {
        "executed_stage_count": 0,
        "reused_stage_count": 12,
        "recomputed_stage_count": 0,
    }
    assert first["content_hash"] == resume["content_hash"]
    assert [row["stage"] for row in first["stages"]] == list(APPLICATION_STAGES)


def test_all_stage_boundaries_and_final_pointer_recover(tmp_path: Path) -> None:
    accepted, _checkpoint = _accepted(tmp_path)
    result = run_lightweight_recovery_matrix(
        accepted=accepted,
        output_root=tmp_path / "recovery",
    )
    assert result["case_count"] == 37
    assert result["all_stage_boundaries_tested"] is True


@pytest.mark.parametrize("target", ["lock", "pointer", "journal", "artifact"])
def test_lock_pointer_journal_and_generation_replacement_are_detected(
    tmp_path: Path, target: str
) -> None:
    accepted, _checkpoint = _accepted(tmp_path)
    machine = _machine(tmp_path, accepted)
    result = machine.run()
    root = tmp_path / "app"
    if target == "lock":
        (root / "application.lock").unlink()
        (root / "application.lock").write_text("replacement", encoding="utf-8")
    elif target == "pointer":
        pointer = read_json(root / "current.json")
        pointer["content_hash"] = "0" * 64
        (root / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    elif target == "journal":
        journal = root / result["stage_journal_relative_path"]
        payload = read_json(journal)
        payload["stages"][0]["input_root"] = "0" * 64
        journal.write_text(json.dumps(payload), encoding="utf-8")
    else:
        stage = root / "stages/01_response_acceptance/work/response_acceptance.txt"
        stage.write_text("tampered", encoding="utf-8")
    with pytest.raises(Exception):
        machine.run()


def test_two_processes_share_single_publisher(tmp_path: Path) -> None:
    accepted, _checkpoint = _accepted(tmp_path)
    root = tmp_path / "concurrent"
    children = []
    for _ in range(2):
        pid = os.fork()
        if pid == 0:
            try:
                _machine(tmp_path, accepted, suffix="concurrent").run()
            except Exception:
                os._exit(1)
            os._exit(0)
        children.append(pid)
    statuses = [os.waitpid(pid, 0)[1] for pid in children]
    assert all(os.waitstatus_to_exitcode(status) == 0 for status in statuses)
    final = _machine(tmp_path, accepted, suffix="concurrent").run()
    assert final["resume_summary"]["reused_stage_count"] == 12
    assert (root / "current.json").is_file()


def test_historical_ready_evidence_is_explicitly_superseded(tmp_path: Path) -> None:
    row = publish_historical_supersession(output_root=tmp_path / "supersession")
    assert row["superseded"] is True
    assert row["executable"] is False
    assert row["authorization_eligible"] is False


def test_production_package_has_no_task055k_synthetic_transport_entry() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("task_055_k").glob("*.py")
    )
    assert "response_bytes_provider" not in sources
    assert "execute_synthetic_rehearsal_response" not in sources
    assert "apply_staged_synthetic_response" not in sources
    assert "urllib.request.urlopen" not in sources
    assert "urllib.request.build_opener(_NoRedirect).open" in Path("task_055_k/gateway.py").read_text(
        encoding="utf-8"
    )
    cli_source = inspect.getsource(network_cli)
    assert 'add_parser("canary")' in cli_source
    assert 'add_parser("resume")' not in cli_source
    assert 'add_parser("batch")' not in cli_source
    assert "request_executor" not in cli_source
    assert "client_factory" not in cli_source


def test_source_entries_use_git_blobs_and_include_full_runtime_boundary() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "task_055_k/stage_machine.py"],
        capture_output=True,
        check=False,
    )
    if tracked.returncode:
        pytest.skip("Task055-KR runtime sources are not committed yet")
    entries = git_index_source_entries(Path(".").resolve())
    paths = {row["path"] for row in entries}
    assert {
        "task_055_k/gateway.py",
        "task_055_k/stage_machine.py",
        "task_055_k/application_components.py",
        "dev_tools/task055kr_harness.py",
    } <= paths
    assert {row["git_index_mode"] for row in entries} <= {"100644", "100755"}


def test_runtime_semantic_hash_changes_with_production_source_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = runtime_semantic_source_hash()
    original = Path.read_bytes

    def changed(path: Path) -> bytes:
        payload = original(path)
        return payload + b"\n# semantic mutation" if path.name == "broker.py" else payload

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert runtime_semantic_source_hash() != baseline


def test_independent_verifier_does_not_reuse_production_valuation_builder() -> None:
    import task_055_k.independent as independent_module

    source = inspect.getsource(independent_module)
    assert "from task_055_f.causal import build_valuation_surface" not in source
    assert "prepare_simulation_inputs" not in source
    assert "from task_055_j.application import _matrix_marks" not in source


def test_independent_valuation_surface_matches_contract_for_official_and_stale_marks() -> None:
    from task_055_k.independent import _independent_valuation_surface

    surface = _independent_valuation_surface(
        truth={
            "records": [
                {
                    "ts_code": "000413.SZ",
                    "trade_date": "20160727",
                    "state": "VENDOR_DAILY_NON_TRADING_MODELED",
                    "modeled_stale_candidate": True,
                    "evidence_hash": "a" * 64,
                }
            ]
        },
        assets=["000413.SZ"],
        dates=["20160726", "20160727"],
        matrix={
            "open": np.asarray([[10.0], [0.0]]),
            "open_valid": np.asarray([[True], [False]]),
            "close": np.asarray([[10.5], [0.0]]),
            "close_valid": np.asarray([[True], [False]]),
        },
        corporate_actions=[],
    )
    assert surface["values"]["open"].tolist() == [[10.0], [10.5]]
    assert surface["values"]["close"].tolist() == [[10.5], [10.5]]
    assert surface["metadata"]["open"]["method"].tolist() == [
        ["OFFICIAL_OPEN"],
        ["STALE_VENDOR_DAILY_NON_TRADING_MODELED"],
    ]
    assert surface["metadata"]["close"]["source_date"].tolist() == [
        ["20160726"],
        ["20160726"],
    ]
    assert surface["blockers"] == {}


def test_invalid_sentinel_cache_moves_to_new_semantic_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import task_055_k.application_components as components
    from task_055_k.stage_machine import StageRuntime

    application_root = tmp_path / "application"
    cache_root = tmp_path / "cache"
    runtime = StageRuntime(
        application_root=application_root,
        stage_work_root=application_root / "stage_work",
        application_spec_hash="a" * 64,
        evidence_scope="synthetic_rehearsal_only",
        accepted=object(),
        context={
            "component_cache_root": str(cache_root),
            "context_root": "b" * 64,
            "runtime_semantic_source_hash": "c" * 64,
        },
        prior_stages={},
    )
    monkeypatch.setattr(components, "_matrix_root", lambda _runtime: tmp_path / "matrix")
    monkeypatch.setattr(components, "_tensor_root", lambda _runtime: tmp_path / "tensor")
    monkeypatch.setattr(components, "_freeze_root", lambda _runtime: tmp_path / "freeze")
    monkeypatch.setattr(
        components,
        "validate_strict_matrix_generation",
        lambda _root: {"content_hash": "d" * 64},
    )
    monkeypatch.setattr(
        components,
        "validate_v3_tensor_generation",
        lambda _root, matrix: {"content_hash": "e" * 64},
    )
    monkeypatch.setattr(
        components,
        "validate_task052_governed_freeze",
        lambda _root: {"content_hash": "f" * 64},
    )
    base_identity = canonical_hash(
        {
            "component": "task054b_production_sentinel",
            "freeze": "f" * 64,
            "matrix": "d" * 64,
            "tensor": "e" * 64,
            "context_root": "b" * 64,
            "evidence_scope": "synthetic_rehearsal_only",
        }
    )
    stale = cache_root / "firewall_sentinel" / base_identity
    stale.mkdir(parents=True)
    (stale / "task_054b_production_sentinel.json").write_text("{}", encoding="utf-8")

    def validate(path, **_kwargs):
        if Path(path).resolve() == (stale / "task_054b_production_sentinel.json").resolve():
            raise RuntimeError("stale sentinel")
        return {"status": "passed", "run_count": 12, "content_hash": "1" * 64}

    def run(**arguments):
        root = Path(arguments["stage_root"])
        root.mkdir(parents=True, exist_ok=True)
        artifact = root / "task_054b_production_sentinel.json"
        artifact.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "content_hash": "1" * 64,
                    "exact_run_count": 12,
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "passed",
            "content_hash": "1" * 64,
            "exact_run_count": 12,
            "artifact_path": str(artifact),
        }

    monkeypatch.setattr(components, "validate_task054b_production_sentinel", validate)
    monkeypatch.setattr(components, "_run_production_sentinel", run)
    result = components._execute_sentinel(runtime)
    assert result.cache_status == "miss_after_invalid_prior_cache"
    assert result.outputs["sentinel_cache_identity"] != base_identity


def test_persisted_valuation_projection_round_trips_to_causal_surface(
    tmp_path: Path,
) -> None:
    surface = {
        "values": {
            "open": np.asarray([[10.0]], dtype=float),
            "close": np.asarray([[10.5]], dtype=float),
        },
        "metadata": {
            "open": {
                "method": np.asarray([["OFFICIAL_OPEN"]], dtype=object),
                "source_date": np.asarray([["20160726"]], dtype=object),
                "stale_age": np.asarray([[0]], dtype=np.int32),
                "evidence_id": np.asarray([["a" * 64]], dtype=object),
            },
            "close": {
                "method": np.asarray([["OFFICIAL_CLOSE"]], dtype=object),
                "source_date": np.asarray([["20160726"]], dtype=object),
                "stale_age": np.asarray([[0]], dtype=np.int32),
                "evidence_id": np.asarray([["b" * 64]], dtype=object),
            },
        },
        "blockers": {},
    }
    projection = publish_valuation_projection(
        output_root=tmp_path / "projection",
        dates=["20160726"],
        assets=["000413.SZ"],
        surface=surface,
        truth_v2_content_hash="c" * 64,
        matrix_content_hash="d" * 64,
        builder_code_hash="e" * 64,
    )
    loaded = valuation_surface_from_projection(
        projection["manifest_path"],
        dates=["20160726"],
        assets=["000413.SZ"],
    )
    assert loaded["values"]["open"].tolist() == [[10.0]]
    assert loaded["values"]["close"].tolist() == [[10.5]]
    assert loaded["metadata"]["open"]["method"].tolist() == [["OFFICIAL_OPEN"]]
    assert loaded["metadata"]["close"]["source_date"].tolist() == [["20160726"]]
