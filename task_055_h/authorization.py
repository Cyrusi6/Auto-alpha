from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from task_055_g.causal import validate_fee_aware_causal_frontier
from task_055_g.network_state import PLAN_SCHEMA, consolidate, verify_state_read_only
from task_055_g.run import verify_task055g_final_report

from .contracts import (
    AUTHORIZATION_SEAL_SCHEMA,
    BLOCKED_STATUS,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    READY_STATUS,
    SCRUBBED_EVIDENCE_SCHEMA,
    SCRUBBED_VERIFICATION_SCHEMA,
    TASK055G_RELATIVE_ROOT,
)
from .fee import attest_fee_schedule, validate_fee_attestation
from .independent import independently_replay_causal_frontier
from .io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from .journal import DurableAccessJournal
from .operational import publish_operational_seal, validate_operational_seal


EXPECTED_G_REPORT_HASH = "c42c49d70ba237122162096db1fd40d5f154dc2194fc8ffc913f5d1c6a2b0ad7"
EXPECTED_G_FINAL_VERIFIER_HASH = "fd5028e223fe26ebc44a15eb34f51f468156609ffd9f149e085bc271e84d483b"
EXPECTED_G_FRONTIER_ROOT = "fd7e9a1468d8b5960767c2c3e4877c6cfa646a9051b8a6b2ba95f5573fb77b6f"
EXPECTED_G_PLAN_HASH = "397ac8d5190ab492c65d5f947df69e845db517b0358330c95db365186aec1e6a"
EXPECTED_G_CURRENT_SHA = "d573c78239ff018b54d0330d94d4908b473e21064041965177ae6c5d5bf47be8"
EXPECTED_G_REPORT_FILE_SHA = "48791980d1a7c320fe7919409166d0c80a195416624776d7ce60b654171cb5f1"
EXPECTED_G_FINAL_FILE_SHA = "3081ca91bc10d38e9ebcc1d70c1e2e2b3b9a7fe6aeef77c6b897288e1ea78f0d"
EXPECTED_G_ACCESS_PLAN_FILE_SHA = "3dcaf8d7bb067ac98746fdd8de717c17ed5bd61437317a4b411b4be62f24f22c"
EXPECTED_G_CAUSAL_FILE_SHA = "0a91568c2db69328b4c64067b78d7f8d8265bfe437698e274b27002e482f2476"
EXPECTED_G_FEE_FILE_SHA = "b6a446f738ce058c561193d1c0d77c9155dce84847ab2552252f941059496380"
EXPECTED_SIMULATION_BUNDLE_FILE_SHA = "ea61cc6b949822e066e670d94f457dc74a163c260c2ed65e9465dfca569566d7"
EXPECTED_ORDERED_REQUESTS = (
    ("000413.SZ", "20160726", "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e", "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f"),
    ("000538.SZ", "20160719", "dfe37041a850ebcdff107212593c5594736a7074f2af248e64be69df8182cccd", "d540a4664ece24f1d83933d7d6c9039fe293f1c1160a6f6d86ff7e5ab7af0b7d"),
    ("000568.SZ", "20160504", "e16367b54c9cd5197c29c12bc82801662685504ec6ecbec057047e718b3350b1", "2ad7e5b9efe1ab650cd7b74775c7b0cd1efef1fa181d0f78f7deb0b569d19777"),
    ("000651.SZ", "20160316", "0aba1d319c31147fa1b77cee0d6bd002a49a350e6346bc51f66b9d219fe7784d", "6f9515fcec8ccb9d27ec3e903f0ae40a68613d97c8ebd15cbd7cdb04a6b7fd59"),
    ("000917.SZ", "20160406", "782ce2b97f0b1d51e47e0de1aeff3fd4fd4758c2fc450994fd83279454ba50d6", "e4e1de03e0794ef4ea289197fc33be9f4349d7fae562926e385eb99303e361f6"),
    ("002230.SZ", "20160407", "a61c8e138de77a48e548101917ca991784e76b65ab235037c03481d2d08bb619", "1c0670edacd4d3f7cdb1820a8a884d7b6ae7d92b9bc1167ebcf650e005acfd15"),
    ("002465.SZ", "20160928", "8b8a28ed61ad28ca0d00535e39ea8729dae3edd2738b6a409ae136ed47bfa27b", "ec002f8ef413dfebae66f7807b0e08b30fd5df677e9561b91384777ab7b631e6"),
    ("002739.SZ", "20160229", "0180df7af7369bae64319130b254ed1fdae10a4fd4f6246174a96329789c2cdd", "8795e2607c7c1beaa87072142f01f0d5ca19e4a35a9e520b8e237cf4eb81c37b"),
    ("300251.SZ", "20160426", "9c5207486175718493cacba2c8225f7748750b609132c1fa0a71f3c988fdb41f", "1ffb04e3f26ce874a9bb0cd739e675d4b283f3a5e274f13cf97ad0295d3b8182"),
    ("600000.SH", "20160219", "8a338c3d0df28c0fae7844eccfe7b1d11e334e368bef52c3f93b2a482ba604e3", "348d9f22172185fe85e4dbf05a4238ad1e47cc675c2a93418146b62692aaa7fd"),
    ("600019.SH", "20160823", "39972d318809204841ca42912875cef1db3037933ff330e31de3a62ed837e8e5", "ef44284ab19aa4fb63f0f8b434e359343c6b5184f80a44e2060692bb15621899"),
    ("600170.SH", "20160323", "b5a1a08494f52ac1afa24c8bc317438b66798f9a4df2e880983008db0102ade6", "5a8efd05411810e6ce0f9e38c5e2bf515a2fb35cf7d1258d5571d7efeb68df24"),
    ("600489.SH", "20160516", "f2d8292eba226d31c4aefdc02360fcba17185739b14e44a4c714e6b92c6700b3", "b34bd4b4931fdd34d270fbfd27a0c583848891ad087178f2df7048ea8ba56420"),
    ("600649.SH", "20161212", "649ea95945c3466416d322e8dca27884e7f0f386b5f8a5b698ea8e4788c24c7c", "acb7ed77eae15c3fe73d655ce5b50543e99026b1684570a95139dd36cc685ebe"),
    ("600863.SH", "20160809", "ede5660b9ac81814c7516822c6922799ab3a764a92634bbd1888a6b5bbdd5735", "95d9ef4f3413ab51a60a690c3b38139361a797348e1310c400bb74715503f96b"),
    ("600900.SH", "20160307", "702384ac268419723358057c84b2f62bc584a0bb0ff25b7cda79ad0076a8dccb", "4e9b23694f97d611c0058fc3035d9186fcb60129b47b5e0b22536e4d8219cfa6"),
    ("601018.SH", "20160517", "0c5ba8c7b39590af73352878d31dfc7895d2590aebfaef557a5f8286a8f04662", "8d6e3eaed4f240e7c307e49f2d4f2fdc002fe3df30f48345c6daff2a91808b11"),
)


class Task055HAuthorizationError(RuntimeError):
    pass


def publish_authorization_seal(
    *,
    repository_root: str | Path,
    governed_root: str | Path,
    output_root: str | Path,
    implementation_commit: str,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    g_relative = Path(TASK055G_RELATIVE_ROOT)
    g_root = governed / g_relative
    journal_staging = Path(tempfile.mkdtemp(prefix=".task055h_access.", dir=output))
    journal = DurableAccessJournal(governed, journal_staging)
    blockers: list[str] = []
    artifacts: dict[str, Any] = {}

    current = journal.read_json(
        g_relative / "current.json",
        principal="task055h_parent_resolver",
        expected_sha256=EXPECTED_G_CURRENT_SHA,
        declared_max_date="20260630",
        date_parser="report",
    )
    report_relative = g_relative / str(current.get("manifest") or "")
    final_relative = g_relative / str(current.get("final_verification_manifest") or "")
    report = journal.read_json(
        report_relative,
        principal="task055h_parent_resolver",
        expected_sha256=EXPECTED_G_REPORT_FILE_SHA,
        declared_max_date="20260630",
        date_parser="report",
    )
    final_native = journal.read_json(
        final_relative,
        principal="task055h_parent_resolver",
        expected_sha256=EXPECTED_G_FINAL_FILE_SHA,
        declared_max_date="20260630",
        date_parser="report",
    )
    if report.get("content_hash") != EXPECTED_G_REPORT_HASH or final_native.get("content_hash") != EXPECTED_G_FINAL_VERIFIER_HASH:
        blockers.append("task055g_expected_parent_hash_mismatch")
    try:
        recomputed_final = verify_task055g_final_report(
            governed / report_relative,
            governed_root=governed,
            task_root=g_root,
        )
        if recomputed_final.get("content_hash") != final_native.get("content_hash"):
            raise Task055HAuthorizationError("task055g_final_verifier_recompute_mismatch")
        journal.record_validator_exception(
            principal="task055g_final_validator",
            manifest_relative_path=report_relative.as_posix(),
            manifest_sha256=EXPECTED_G_REPORT_FILE_SHA,
            validator_fqn="task_055_g.run.verify_task055g_final_report",
            result_content_hash=str(recomputed_final["content_hash"]),
        )
    except Exception as exc:
        blockers.append(f"task055g_native_validation_failed:{exc}")

    native_paths = _native_paths(report, g_root, governed, journal=journal, g_relative=g_relative)
    plan = native_paths.pop("_network_plan_payload")
    plan_path = native_paths["network_plan"]
    requests = list(plan.get("requests") or ())
    if plan.get("plan_hash") != EXPECTED_G_PLAN_HASH or plan.get("frontier_root") != EXPECTED_G_FRONTIER_ROOT:
        blockers.append("task055g_plan_or_frontier_hash_mismatch")
    if len(requests) != 17 or any(str(row.get("trade_date")) > "20260630" for row in requests):
        blockers.append("task055g_exact_daily_request_set_invalid")
    ordered_keys = [_request_evidence(row, ordinal=index) for index, row in enumerate(requests, start=1)]
    if canonical_hash([[row["ts_code"], row["trade_date"]] for row in ordered_keys]) == canonical_hash([]):
        blockers.append("task055g_ordered_key_root_invalid")

    fee = None
    try:
        fee = attest_fee_schedule(native_paths["fee_schedule"], output / "fee_attestation")
        validate_fee_attestation(fee["manifest_path"])
        journal.record_validator_exception(
            principal="task055h_fee_validator",
            manifest_relative_path=native_paths["fee_schedule"].relative_to(governed).as_posix(),
            manifest_sha256=EXPECTED_G_FEE_FILE_SHA,
            validator_fqn="task_055_h.fee.attest_fee_schedule",
            result_content_hash=str(fee["content_hash"]),
        )
    except Exception as exc:
        blockers.append(f"fee_schedule_attestation_failed:{exc}")
    operational = None
    try:
        operational = publish_operational_seal(
            repository_root=repository,
            governed_root=governed,
            output_root=output / "operational_seal",
        )
        validate_operational_seal(operational["manifest_path"])
        journal.record_validator_exception(
            principal="task055h_operational_validator",
            manifest_relative_path=Path(operational["manifest_path"]).relative_to(governed).as_posix(),
            manifest_sha256=sha256_file(operational["manifest_path"]),
            validator_fqn="task_055_h.operational.publish_operational_seal",
            result_content_hash=str(operational["content_hash"]),
        )
    except Exception as exc:
        blockers.append(f"operational_state_unproven:{exc}")
    causal_attestation = None
    if fee is not None:
        try:
            causal = validate_fee_aware_causal_frontier(native_paths["causal_frontier"])
            projection = Path(str(causal["valuation_projection"]["manifest_path"]))
            causal_attestation = independently_replay_causal_frontier(
                simulation_bundle_manifest=native_paths["simulation_bundle"],
                valuation_projection_manifest=projection,
                fee_schedule_manifest=native_paths["fee_schedule"],
                producer_causal_manifest=native_paths["causal_frontier"],
            )
            journal.record_validator_exception(
                principal="task055h_independent_causal_validator",
                manifest_relative_path=native_paths["causal_frontier"].relative_to(governed).as_posix(),
                manifest_sha256=EXPECTED_G_CAUSAL_FILE_SHA,
                validator_fqn="task_055_h.independent.independently_replay_causal_frontier",
                result_content_hash=str(causal_attestation["content_hash"]),
            )
        except Exception as exc:
            blockers.append(f"independent_20x5_causal_replay_failed:{exc}")

    state_root = output / "network_state"
    cache_root = output / "network_cache_data"
    transport_spend_root = output / "transport_spend"
    cache_root.mkdir(parents=True, exist_ok=True)
    transport_spend = _seed_or_validate_empty_transport_spend(transport_spend_root)
    if int(transport_spend.get("physical_attempt_count") or 0) != 0:
        blockers.append("authorization_transport_spend_not_empty")
    try:
        consolidation = consolidate(state_root=state_root, plan_manifest=plan_path)
        network = verify_state_read_only(state_root=state_root)
        parent_network = verify_state_read_only(state_root=native_paths["network_state_root"])
    except Exception as exc:
        blockers.append(f"network_state_initialization_failed:{exc}")
        consolidation = {}
        network = {}
        parent_network = {}
    budgets = {
        "unique_security_dates": int(network.get("unique_security_date_count") or 0),
        "logical_requests": int(network.get("request_count") or 0),
        "physical_attempts": int(network.get("physical_attempt_count") or 0),
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
    }
    if budgets["unique_security_dates"] != 17 or budgets["logical_requests"] != 17 or budgets["physical_attempts"] != 0:
        blockers.append("authorization_budget_seed_invalid")
    if int(parent_network.get("physical_attempt_count") or 0) != 0:
        blockers.append("task055g_parent_physical_attempt_detected")
    access = journal.publish(output / "access_journal")
    if access.get("prospective_holdout_accessed") is not False:
        blockers.append("prospective_holdout_accessed")

    root_identities = {
        "repository": _root_identity(repository),
        "governed": _root_identity(governed),
        "task055g": _root_identity(g_root),
        "output": _root_identity(output),
        "state": _root_identity(state_root),
        "cache": _root_identity(cache_root),
        "transport_spend": _root_identity(transport_spend_root),
    }
    source_hashes = _semantic_source_hashes(repository)
    artifact_catalog = _artifact_catalog(g_root, native_paths)
    first = ordered_keys[0] if ordered_keys else None
    canary_execution_plan = _canary_execution_plan(plan, first)
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    semantic = {
        "schema_version": AUTHORIZATION_SEAL_SCHEMA,
        "status": status,
        "baseline_commit": "5bc179de10a921e9547d63c393643d4438b126f3",
        "implementation_commit": implementation_commit,
        "task055g_report_content_hash": report.get("content_hash"),
        "task055g_final_verifier_content_hash": final_native.get("content_hash"),
        "task055g_plan_hash": plan.get("plan_hash"),
        "task055g_plan_lineage": dict(plan.get("lineage") or {}),
        "frontier_root": plan.get("frontier_root"),
        "ordered_exact_daily_key_count": len(ordered_keys),
        "ordered_exact_daily_keys": ordered_keys,
        "ordered_key_root": canonical_hash(ordered_keys),
        "canary": first,
        "canary_execution_plan": canary_execution_plan,
        "canary_execution_plan_hash": canary_execution_plan["plan_hash"],
        "canary_retry_count": 1,
        "resume_requires_separate_authorization": True,
        "resume_authorized": False,
        "root_identities": root_identities,
        "canonical_roots": {
            "task055h_output_relative_to_governed": "validation_runs/task_055_h_20260717",
            "state_relative_to_output": "network_state",
            "cache_data_relative_to_output": "network_cache_data",
            "transport_spend_relative_to_output": "transport_spend",
        },
        "parent_network_ledger_root": parent_network.get("ledger_root"),
        "authorization_network_ledger_root": network.get("ledger_root"),
        "authorization_transport_spend_root": transport_spend.get("content_hash"),
        "budgets": budgets,
        "consolidation_content_hash": consolidation.get("content_hash"),
        "access_journal_content_hash": access.get("content_hash"),
        "fee_attestation_content_hash": None if fee is None else fee.get("content_hash"),
        "operational_seal_content_hash": None if operational is None else operational.get("content_hash"),
        "independent_causal_attestation": causal_attestation,
        "artifact_sha_catalog": artifact_catalog,
        "semantic_source_hashes": source_hashes,
        "semantic_source_root": canonical_hash(source_hashes),
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": bool(access.get("prospective_holdout_accessed")),
        },
        "engineering_blockers": sorted(blockers),
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    seal = publish_generation(
        output / "authorization_seal",
        prefix="authorization_seal",
        manifest_name="authorization_seal.json",
        semantic=semantic,
    )
    scrubbed = publish_scrubbed_evidence_package(seal, output / "scrubbed_evidence")
    shutil.rmtree(journal_staging, ignore_errors=True)
    return seal | {"scrubbed_evidence": scrubbed, "access_journal": access, "fee_attestation": fee, "operational_seal": operational}


def validate_authorization_seal(
    path: str | Path,
    *,
    require_ready: bool = True,
    verify_current_budget: bool = True,
) -> dict[str, Any]:
    payload = validate_generation(path, schema=AUTHORIZATION_SEAL_SCHEMA, manifest_name="authorization_seal.json")
    _validate_sealed_root_identities(Path(payload["manifest_path"]), payload, verify_current_budget=verify_current_budget)
    if payload.get("ordered_exact_daily_key_count") != 17 or len(payload.get("ordered_exact_daily_keys") or ()) != 17:
        raise Task055HAuthorizationError("authorization_exact_key_set_invalid")
    if canonical_hash(payload["ordered_exact_daily_keys"]) != payload.get("ordered_key_root"):
        raise Task055HAuthorizationError("authorization_ordered_key_root_invalid")
    expected = [
        (code, date, transport_hash, evidence_hash)
        for code, date, transport_hash, evidence_hash in EXPECTED_ORDERED_REQUESTS
    ]
    actual = [
        (row.get("ts_code"), row.get("trade_date"), row.get("transport_hash"), row.get("evidence_use_hash"))
        for row in payload["ordered_exact_daily_keys"]
    ]
    if actual != expected or [row.get("ordinal") for row in payload["ordered_exact_daily_keys"]] != list(range(1, 18)):
        raise Task055HAuthorizationError("authorization_ordered_request_evidence_mismatch")
    if payload.get("task055g_plan_hash") != EXPECTED_G_PLAN_HASH or payload.get("frontier_root") != EXPECTED_G_FRONTIER_ROOT:
        raise Task055HAuthorizationError("authorization_parent_plan_lineage_invalid")
    plan_lineage = payload.get("task055g_plan_lineage") or {}
    for key in ("matrix_content_hash", "simulation_bundle_content_hash", "fee_schedule_content_hash", "frontier_root", "key_root"):
        if not plan_lineage.get(key):
            raise Task055HAuthorizationError(f"authorization_parent_plan_lineage_missing:{key}")
    if payload.get("resume_authorized") is not False or payload.get("resume_requires_separate_authorization") is not True:
        raise Task055HAuthorizationError("authorization_resume_boundary_invalid")
    expected_canary_plan = _canary_execution_plan(
        {"lineage": plan_lineage, "frontier_root": payload["frontier_root"], "plan_hash": payload["task055g_plan_hash"]},
        payload["canary"],
    )
    if payload.get("canary_execution_plan") != expected_canary_plan or payload.get("canary_execution_plan_hash") != expected_canary_plan["plan_hash"]:
        raise Task055HAuthorizationError("authorization_canary_execution_plan_invalid")
    network = payload.get("network_execution") or {}
    if any(int(network.get(key) or 0) != 0 for key in ("credential_read_count", "tushare_request_count", "other_network_request_count")):
        raise Task055HAuthorizationError("authorization_offline_boundary_invalid")
    if network.get("prospective_holdout_accessed") is not False:
        raise Task055HAuthorizationError("authorization_holdout_boundary_invalid")
    expected_empty_spend = canonical_hash({
        "schema_version": "task055f_append_only_network_spend_v1",
        "events": [],
        "physical_attempt_count": 0,
        "logical_transport_count": 0,
    })
    if payload.get("status") == READY_STATUS and payload.get("authorization_transport_spend_root") != expected_empty_spend:
        raise Task055HAuthorizationError("authorization_transport_spend_seed_invalid")
    if require_ready and (payload.get("status") != READY_STATUS or payload.get("engineering_blockers")):
        raise Task055HAuthorizationError("authorization_not_ready")
    return payload


def publish_scrubbed_evidence_package(seal: Mapping[str, Any], output_root: str | Path) -> dict[str, Any]:
    keys = list(seal.get("ordered_exact_daily_keys") or ())
    roots = {name: value.get("identity_hash") for name, value in (seal.get("root_identities") or {}).items()}
    semantic = {
        "schema_version": SCRUBBED_EVIDENCE_SCHEMA,
        "status": seal.get("status"),
        "authorization_seal_content_hash": seal.get("content_hash"),
        "baseline_commit": seal.get("baseline_commit"),
        "implementation_commit": seal.get("implementation_commit"),
        "task055g_report_content_hash": seal.get("task055g_report_content_hash"),
        "task055g_final_verifier_content_hash": seal.get("task055g_final_verifier_content_hash"),
        "plan_hash": seal.get("task055g_plan_hash"),
        "plan_lineage": seal.get("task055g_plan_lineage"),
        "frontier_root": seal.get("frontier_root"),
        "ordered_exact_daily_keys": keys,
        "ordered_key_root": canonical_hash(keys),
        "canary": seal.get("canary"),
        "canary_execution_plan_hash": seal.get("canary_execution_plan_hash"),
        "root_identity_hashes": roots,
        "parent_network_ledger_root": seal.get("parent_network_ledger_root"),
        "authorization_network_ledger_root": seal.get("authorization_network_ledger_root"),
        "authorization_transport_spend_root": seal.get("authorization_transport_spend_root"),
        "budgets": seal.get("budgets"),
        "fee_attestation_content_hash": seal.get("fee_attestation_content_hash"),
        "operational_seal_content_hash": seal.get("operational_seal_content_hash"),
        "artifact_sha_catalog": seal.get("artifact_sha_catalog"),
        "semantic_source_root": seal.get("semantic_source_root"),
        "engineering_blockers": seal.get("engineering_blockers"),
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    package = publish_generation(
        output_root,
        prefix="scrubbed_authorization_evidence",
        manifest_name="scrubbed_authorization_evidence.json",
        semantic=semantic,
    )
    verify_scrubbed_evidence_package(package["manifest_path"])
    return package


def verify_scrubbed_evidence_package(path: str | Path) -> dict[str, Any]:
    package_path = Path(path)
    try:
        package = validate_generation(
            package_path,
            schema=SCRUBBED_EVIDENCE_SCHEMA,
            manifest_name="scrubbed_authorization_evidence.json",
        )
    except ValueError as exc:
        if "generation_identity_invalid" not in str(exc) or not package_path.is_file():
            raise
        package = read_json(package_path)
        semantic = {key: value for key, value in package.items() if key not in {"content_hash", "generation_id"}}
        if (
            package.get("schema_version") != SCRUBBED_EVIDENCE_SCHEMA
            or canonical_hash(semantic) != package.get("content_hash")
            or not str(package.get("generation_id") or "").endswith(str(package.get("content_hash") or "")[:24])
        ):
            raise Task055HAuthorizationError("standalone_scrubbed_package_identity_invalid") from exc
        package = package | {"manifest_path": str(package_path.resolve())}
    serialized = json.dumps({key: value for key, value in package.items() if key != "manifest_path"}, sort_keys=True)
    if "/home/" in serialized or "TUSHARE_TOKEN" in serialized:
        raise Task055HAuthorizationError("scrubbed_package_sensitive_content_detected")
    keys = list(package.get("ordered_exact_daily_keys") or ())
    if len(keys) != 17 or canonical_hash(keys) != package.get("ordered_key_root"):
        raise Task055HAuthorizationError("scrubbed_package_key_root_invalid")
    actual = [(row.get("ts_code"), row.get("trade_date"), row.get("transport_hash"), row.get("evidence_use_hash")) for row in keys]
    if actual != list(EXPECTED_ORDERED_REQUESTS):
        raise Task055HAuthorizationError("scrubbed_package_ordered_request_evidence_mismatch")
    if package.get("plan_hash") != EXPECTED_G_PLAN_HASH or package.get("frontier_root") != EXPECTED_G_FRONTIER_ROOT:
        raise Task055HAuthorizationError("scrubbed_package_lineage_invalid")
    expected_canary = _canary_execution_plan(
        {
            "plan_hash": package["plan_hash"],
            "frontier_root": package["frontier_root"],
            "lineage": package.get("plan_lineage") or {},
        },
        keys[0],
    )
    if package.get("canary_execution_plan_hash") != expected_canary["plan_hash"]:
        raise Task055HAuthorizationError("scrubbed_package_canary_plan_invalid")
    semantic = {
        "schema_version": SCRUBBED_VERIFICATION_SCHEMA,
        "status": "passed",
        "package_content_hash": package["content_hash"],
        "key_count": len(keys),
        "ordered_key_root": package["ordered_key_root"],
        "artifact_catalog_root": canonical_hash(package.get("artifact_sha_catalog") or ()),
        "internal_hash_chain_valid": True,
        "server_artifact_revalidation_performed": False,
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _native_paths(
    report: Mapping[str, Any],
    g_root: Path,
    governed_root: Path,
    *,
    journal: DurableAccessJournal,
    g_relative: Path,
) -> dict[str, Any]:
    artifacts = report.get("artifacts") or {}
    causal_relative = g_relative / str(artifacts["causal_frontier"])
    causal = governed_root / causal_relative
    causal_manifest = journal.read_json(
        causal_relative,
        principal="task055h_parent_artifact_resolver",
        expected_sha256=EXPECTED_G_CAUSAL_FILE_SHA,
        declared_max_date="20260630",
        date_parser="report",
    )
    plan_partition = (causal_manifest.get("partitions") or {}).get("network_plan") or {}
    plan_relative = causal_relative.parent / str(plan_partition.get("path") or "")
    plan = governed_root / plan_relative
    plan_payload = journal.read_json(
        plan_relative,
        principal="task055h_parent_artifact_resolver",
        expected_sha256=str(plan_partition.get("sha256") or ""),
        declared_max_date="20260630",
        date_parser="plan",
    )
    access_relative = g_relative / str(artifacts["access_plan"])
    access_plan = journal.read_json(
        access_relative,
        principal="task055h_parent_artifact_resolver",
        expected_sha256=EXPECTED_G_ACCESS_PLAN_FILE_SHA,
        declared_max_date="20260630",
        date_parser="report",
    )
    bundle_rows = [
        row for row in access_plan.get("entries") or ()
        if row.get("dataset_role") == "task055a_simulation_bundle_manifest"
    ]
    if len(bundle_rows) != 1:
        raise Task055HAuthorizationError("task055g_access_plan_simulation_bundle_cardinality_invalid")
    bundle_row = bundle_rows[0]
    if bundle_row.get("expected_sha256") != EXPECTED_SIMULATION_BUNDLE_FILE_SHA:
        raise Task055HAuthorizationError("task055g_simulation_bundle_expected_sha_mismatch")
    simulation_bundle_relative = Path(str(bundle_row["relative_path"]))
    simulation_bundle = governed_root / simulation_bundle_relative
    journal.read_json(
        simulation_bundle_relative,
        principal="task055h_parent_artifact_resolver",
        expected_sha256=EXPECTED_SIMULATION_BUNDLE_FILE_SHA,
        declared_max_date=str(bundle_row.get("declared_max_date") or "20240530"),
        date_parser="report",
    )
    fee_relative = g_relative / str(artifacts["fee_schedule_v2"])
    journal.read_json(
        fee_relative,
        principal="task055h_parent_artifact_resolver",
        expected_sha256=EXPECTED_G_FEE_FILE_SHA,
        declared_max_date="20240530",
        date_parser="fee",
    )
    result = {
        "fee_schedule": governed_root / fee_relative,
        "causal_frontier": causal,
        "network_plan": plan,
        "network_state_root": g_root / str(artifacts["network_state_root"]),
        "simulation_bundle": simulation_bundle,
        "_network_plan_payload": plan_payload,
    }
    for key, path in result.items():
        if key.startswith("_"):
            continue
        if key.endswith("root"):
            if not path.is_dir():
                raise Task055HAuthorizationError(f"native_directory_missing:{key}")
        elif not path.is_file():
            raise Task055HAuthorizationError(f"native_file_missing:{key}")
    return result


def _request_evidence(row: Mapping[str, Any], *, ordinal: int) -> dict[str, Any]:
    params = row.get("params") or {}
    return {
        "ordinal": ordinal,
        "api_name": str(row.get("api_name")),
        "ts_code": str(params.get("ts_code") or row.get("ts_code")),
        "trade_date": str(params.get("trade_date") or row.get("trade_date")),
        "fields": list(row.get("fields") or ()),
        "transport_hash": str(row.get("transport_hash")),
        "evidence_use_hash": str(row.get("evidence_use_hash")),
    }


def _canary_execution_plan(parent_plan: Mapping[str, Any], canary: Mapping[str, Any] | None) -> dict[str, Any]:
    if not canary:
        raise Task055HAuthorizationError("authorization_canary_missing")
    request = {
        "stage": "L1",
        "round_id": 1,
        "api_name": canary["api_name"],
        "params": {"ts_code": canary["ts_code"], "trade_date": canary["trade_date"]},
        "fields": list(canary["fields"]),
        "ts_code": canary["ts_code"],
        "trade_date": canary["trade_date"],
        "transport_hash": canary["transport_hash"],
        "evidence_use_hash": canary["evidence_use_hash"],
    }
    lineage = dict(parent_plan.get("lineage") or {}) | {
        "parent_task055g_plan_hash": parent_plan.get("plan_hash"),
    }
    semantic = {
        "schema_version": PLAN_SCHEMA,
        "status": "sealed_single_exact_daily_canary_only",
        "stage": "L1",
        "round_id": 1,
        "frontier_root": parent_plan.get("frontier_root"),
        "parent_apply_hash": None,
        "lineage": lineage,
        "requests": [request],
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
        "must_stop_after_canary": True,
        "batch_authorized": False,
    }
    return semantic | {"plan_hash": canonical_hash(semantic)}


def _artifact_catalog(g_root: Path, paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    result = []
    for role, path in sorted(paths.items()):
        if path.is_file():
            relative = path.relative_to(g_root).as_posix() if g_root in path.parents else f"external_parent:{role}"
            result.append({"role": role, "relative_id": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    return result


def _root_identity(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    metadata = path.stat()
    return {"identity_hash": canonical_hash([str(path), metadata.st_dev, metadata.st_ino]), "device": metadata.st_dev, "inode": metadata.st_ino}


def _seed_or_validate_empty_transport_spend(root: Path) -> dict[str, Any]:
    pointer = root / "current.json"
    if pointer.is_file():
        return _read_transport_spend(root)
    return publish_generation(
        root,
        prefix="network_spend",
        manifest_name="network_spend_ledger.json",
        semantic={
            "schema_version": "task055f_append_only_network_spend_v1",
            "events": [],
            "physical_attempt_count": 0,
            "logical_transport_count": 0,
        },
    )


def _read_transport_spend(root: Path) -> dict[str, Any]:
    current = read_json(root / "current.json")
    relative = Path(str(current.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055HAuthorizationError("authorization_transport_spend_pointer_invalid")
    manifest = (root / relative).resolve()
    if root.resolve() not in manifest.parents or not manifest.is_file() or manifest.is_symlink():
        raise Task055HAuthorizationError("authorization_transport_spend_manifest_invalid")
    payload = read_json(manifest)
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash") or current.get("content_hash") != payload.get("content_hash"):
        raise Task055HAuthorizationError("authorization_transport_spend_hash_invalid")
    return payload | {"manifest_path": str(manifest)}


def _validate_sealed_root_identities(
    manifest_path: Path,
    payload: Mapping[str, Any],
    *,
    verify_current_budget: bool,
) -> None:
    if manifest_path.resolve().parents[2].name != "authorization_seal":
        raise Task055HAuthorizationError("authorization_seal_native_path_invalid")
    task_root = manifest_path.resolve().parents[3]
    canonical = payload.get("canonical_roots") or {}
    if {
        "state_relative_to_output": canonical.get("state_relative_to_output"),
        "cache_data_relative_to_output": canonical.get("cache_data_relative_to_output"),
        "transport_spend_relative_to_output": canonical.get("transport_spend_relative_to_output"),
    } != {
        "state_relative_to_output": "network_state",
        "cache_data_relative_to_output": "network_cache_data",
        "transport_spend_relative_to_output": "transport_spend",
    }:
        raise Task055HAuthorizationError("authorization_canonical_root_contract_invalid")
    expected = {
        "output": task_root,
        "state": task_root / str(canonical.get("state_relative_to_output") or ""),
        "cache": task_root / str(canonical.get("cache_data_relative_to_output") or ""),
        "transport_spend": task_root / str(canonical.get("transport_spend_relative_to_output") or ""),
    }
    identities = payload.get("root_identities") or {}
    for role, root in expected.items():
        row = identities.get(role) or {}
        if not root.is_dir() or root.is_symlink():
            raise Task055HAuthorizationError(f"authorization_root_missing_or_symlink:{role}")
        metadata = root.stat()
        actual = canonical_hash([str(root.resolve()), metadata.st_dev, metadata.st_ino])
        if (
            row.get("identity_hash") != actual
            or int(row.get("device", -1)) != metadata.st_dev
            or int(row.get("inode", -1)) != metadata.st_ino
        ):
            raise Task055HAuthorizationError(f"authorization_root_identity_mismatch:{role}")
    if not verify_current_budget:
        return
    network = verify_state_read_only(state_root=expected["state"])
    budgets = payload.get("budgets") or {}
    if (
        network.get("ledger_root") != payload.get("authorization_network_ledger_root")
        or int(network.get("unique_security_date_count") or 0) != int(budgets.get("unique_security_dates") or 0)
        or int(network.get("request_count") or 0) != int(budgets.get("logical_requests") or 0)
        or int(network.get("physical_attempt_count") or 0) != int(budgets.get("physical_attempts") or 0)
    ):
        raise Task055HAuthorizationError("authorization_network_budget_drift")
    spend = _read_transport_spend(expected["transport_spend"])
    if (
        spend.get("content_hash") != payload.get("authorization_transport_spend_root")
        or int(spend.get("physical_attempt_count") or 0) != int(budgets.get("physical_attempts") or 0)
    ):
        raise Task055HAuthorizationError("authorization_transport_budget_drift")


def _semantic_source_hashes(repository: Path) -> dict[str, str]:
    relatives = (
        "task_055_a/bundle.py",
        "task_055_a/policy.py",
        "task_055_a/run.py",
        "task_055_a/simulator.py",
        "task_055_f/causal.py",
        "task_055_f/valuation.py",
        "task_055_g/access.py",
        "task_055_g/bundle.py",
        "task_055_g/causal.py",
        "task_055_g/fees.py",
        "task_055_g/lineage.py",
        "task_055_g/network_state.py",
        "task_055_g/operational.py",
        "task_055_g/run.py",
        "task_055_g/truth.py",
        "task_055_g/verifier.py",
        "data_pipeline/ashare/cache.py",
        "data_pipeline/ashare/providers/tushare_client.py",
        "task_055_h/authorization.py",
        "task_055_h/fee.py",
        "task_055_h/independent.py",
        "task_055_h/journal.py",
        "task_055_h/network.py",
        "task_055_h/application.py",
    )
    result = {}
    for relative in relatives:
        path = repository / relative
        if not path.is_file():
            raise Task055HAuthorizationError(f"semantic_source_missing:{relative}")
        result[relative] = sha256_file(path)
    return result
