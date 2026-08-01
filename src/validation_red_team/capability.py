"""One-shot capability registry for the Validation Red-Team principal."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import attach_artifact_metadata

from .candidate_pool import validate_candidate_pool_manifest
from .contracts import validate_holdout_policy
from .io import HoldoutContractError, atomic_json, checked_regular_file, read_json, sha256_file, stable_hash
from .view import validate_sealed_holdout_view


class HoldoutCapabilityRegistry:
    """Append-only one-shot authorization for a frozen pool and sealed view."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if self.root.is_symlink():
            raise HoldoutContractError("capability_registry_symlink_forbidden")
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "holdout_capability_ledger.jsonl"
        self.lock_path = self.root / "holdout_capability.lock"

    def issue(
        self,
        *,
        candidate_pool_manifest_path: str | Path,
        holdout_view_manifest_path: str | Path,
        holdout_policy_path: str | Path,
        red_team_output_root: str | Path,
        principal: str = "validation_red_team",
    ) -> tuple[Path, dict[str, Any]]:
        if principal != "validation_red_team":
            raise HoldoutContractError("holdout_capability_principal_forbidden")
        candidate = validate_candidate_pool_manifest(candidate_pool_manifest_path, revalidate_sources=True)
        view = validate_sealed_holdout_view(holdout_view_manifest_path, open_payloads=False)
        policy, policy_payload = validate_holdout_policy(holdout_policy_path)
        if view.get("candidate_pool_root") != candidate.get("content_hash"):
            raise HoldoutContractError("holdout_view_candidate_pool_lineage_mismatch")
        profile = view.get("profile") or {}
        expected_profile = policy.profile
        if int(view.get("label_horizon") or 0) != expected_profile.holding_period_days:
            raise HoldoutContractError("holdout_label_horizon_profile_mismatch")
        actual_key = stable_hash(
            {
                "universe_name": profile.get("universe_name"),
                "holding_period_days": int(profile.get("holding_period_days") or 0),
                "neutralization_method": profile.get("neutralization_method"),
                "rebalance_frequency": profile.get("rebalance_frequency"),
            }
        )
        if actual_key != expected_profile.calibration_key:
            raise HoldoutContractError("holdout_policy_calibration_profile_mismatch")
        output_root = Path(red_team_output_root).resolve()
        candidate_manifest = Path(candidate_pool_manifest_path).resolve()
        candidate_root = (
            candidate_manifest.parents[2]
            if candidate_manifest.parent.parent.name == "generations"
            else candidate_manifest.parent
        )
        view_root = Path(holdout_view_manifest_path).resolve().parent
        if self.root.is_relative_to(candidate_root) or self.root.is_relative_to(view_root):
            raise HoldoutContractError("capability_registry_not_isolated")
        if (
            output_root.is_symlink()
            or output_root.is_relative_to(candidate_root)
            or output_root.is_relative_to(view_root)
        ):
            raise HoldoutContractError("red_team_output_root_not_isolated")
        with self._locked():
            events = self._read_ledger()
            pair = (candidate["content_hash"], view["content_hash"])
            if any(event.get("holdout_view_root") == view["content_hash"] for event in events):
                raise HoldoutContractError("holdout_view_already_registered")
            nonce = secrets.token_hex(32)
            capability_id = stable_hash({"pair": pair, "nonce": nonce, "policy_hash": policy.policy_hash})
            core = {
                "status": "issued",
                "capability_id": capability_id,
                "principal": principal,
                "allowed_operation": "single_sealed_holdout_evaluation",
                "max_consumptions": 1,
                "candidate_pool_manifest_path": str(Path(candidate_pool_manifest_path).resolve()),
                "candidate_pool_root": candidate["content_hash"],
                "holdout_view_manifest_path": str(Path(holdout_view_manifest_path).resolve()),
                "holdout_view_root": view["content_hash"],
                "holdout_policy_path": str(Path(holdout_policy_path).resolve()),
                "holdout_policy_hash": policy.policy_hash,
                "policy_manifest_sha256": sha256_file(holdout_policy_path),
                "red_team_output_root": str(output_root),
                "registry_root": str(self.root),
                "search_agent_capability": False,
                "feedback_to_search_forbidden": True,
                "formula_mutation_after_holdout_forbidden": True,
                "failed_formula_next_evidence": "next_generation_holdout_or_shadow_observation",
            }
            content_hash = stable_hash(core)
            payload = attach_artifact_metadata(
                {**core, "content_hash": content_hash},
                "sealed_holdout_capability",
                "validation_red_team",
            )
            capability_path = self.root / "capabilities" / capability_id / "holdout_capability.json"
            atomic_json(capability_path, payload)
            self._append_event(
                events,
                {
                    "event": "issued",
                    "capability_id": capability_id,
                    "capability_sha256": sha256_file(capability_path),
                    "candidate_pool_root": pair[0],
                    "holdout_view_root": pair[1],
                    "holdout_policy_hash": policy_payload["policy_hash"],
                },
            )
        return capability_path, read_json(capability_path, artifact_type="sealed_holdout_capability")

    def validate(self, capability_path: str | Path, reviewed_hash: str) -> dict[str, Any]:
        path = checked_regular_file(capability_path)
        if not path.is_relative_to(self.root / "capabilities"):
            raise HoldoutContractError("capability_outside_canonical_registry")
        if sha256_file(path) != reviewed_hash:
            raise HoldoutContractError("reviewed_capability_hash_mismatch")
        payload = read_json(path, artifact_type="sealed_holdout_capability")
        core = {key: value for key, value in payload.items() if key not in {"content_hash", "artifact_type", "schema_version", "producer", "created_at", "artifact_metadata"}}
        if payload.get("content_hash") != stable_hash(core):
            raise HoldoutContractError("capability_content_hash_mismatch")
        if payload.get("status") != "issued" or payload.get("principal") != "validation_red_team":
            raise HoldoutContractError("capability_not_issued_to_red_team")
        if payload.get("max_consumptions") != 1 or payload.get("search_agent_capability") is not False:
            raise HoldoutContractError("capability_boundary_invalid")
        events = self._read_ledger()
        issued = [event for event in events if event.get("event") == "issued" and event.get("capability_id") == payload.get("capability_id")]
        if len(issued) != 1 or issued[0].get("capability_sha256") != reviewed_hash:
            raise HoldoutContractError("capability_issue_ledger_mismatch")
        return payload

    def begin(self, capability: dict[str, Any]) -> None:
        with self._locked():
            events = self._read_ledger()
            capability_id = capability["capability_id"]
            if any(event.get("capability_id") == capability_id and event.get("event") in {"consumption_started", "completed", "blocked"} for event in events):
                raise HoldoutContractError("holdout_capability_already_consumed")
            self._append_event(
                events,
                {
                    "event": "consumption_started",
                    "capability_id": capability_id,
                    "candidate_pool_root": capability["candidate_pool_root"],
                    "holdout_view_root": capability["holdout_view_root"],
                },
            )

    def finish(self, capability: dict[str, Any], *, status: str, result_manifest_path: str | Path | None, blocker: str | None = None) -> None:
        if status not in {"completed", "blocked"}:
            raise HoldoutContractError("invalid_capability_terminal_status")
        with self._locked():
            events = self._read_ledger()
            capability_id = capability["capability_id"]
            started = [event for event in events if event.get("event") == "consumption_started" and event.get("capability_id") == capability_id]
            terminal = [event for event in events if event.get("event") in {"completed", "blocked"} and event.get("capability_id") == capability_id]
            if len(started) != 1 or terminal:
                raise HoldoutContractError("capability_terminal_sequence_invalid")
            self._append_event(
                events,
                {
                    "event": status,
                    "capability_id": capability_id,
                    "result_manifest_path": str(Path(result_manifest_path).resolve()) if result_manifest_path else None,
                    "result_manifest_sha256": sha256_file(result_manifest_path) if result_manifest_path else None,
                    "blocker": blocker,
                },
            )

    def ledger_root(self) -> str:
        events = self._read_ledger()
        return str(events[-1]["event_hash"]) if events else stable_hash([])

    def _read_ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        events = [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        previous = "0" * 64
        for ordinal, event in enumerate(events):
            expected = stable_hash({key: value for key, value in event.items() if key != "event_hash"})
            if event.get("ordinal") != ordinal or event.get("previous_event_hash") != previous or event.get("event_hash") != expected:
                raise HoldoutContractError("capability_ledger_hash_chain_invalid")
            previous = event["event_hash"]
        return events

    def _append_event(self, events: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        payload = {
            **event,
            "ordinal": len(events),
            "previous_event_hash": events[-1]["event_hash"] if events else "0" * 64,
            "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        payload["event_hash"] = stable_hash(payload)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload

    def _locked(self):
        return _RegistryLock(self.lock_path)


class _RegistryLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, traceback):
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
