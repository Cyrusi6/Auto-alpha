"""Evidence-derived Task 053-A readiness semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


CERTIFICATION_BLOCKERS = (
    "suspension_timing_semantics_uncertified",
    "constituent_publication_timing_unknown",
    "no_future_untouched_holdout",
    "selection_data_reused",
    "vendor_historical_revision_risk",
)


def derive_task053_readiness(stages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    source = _stage(stages, "governed_source")
    freeze = _stage(stages, "immutable_freeze")
    universe = _stage(stages, "historical_universe")
    matrix = _stage(stages, "strict_matrix")
    tensor = _stage(stages, "v3_tensor")
    firewall = _stage(stages, "research_firewall")
    replay = _stage(stages, "four_gpu_replay")

    engineering_blockers: list[str] = []
    for name, payload in (
        ("governed_source", source),
        ("immutable_freeze", freeze),
        ("historical_universe", universe),
        ("strict_matrix", matrix),
        ("v3_tensor", tensor),
        ("research_firewall", firewall),
    ):
        if not _proved(payload):
            engineering_blockers.extend(_blockers(name, payload))
    candidate_blockers = sorted(set(str(item) for item in tensor.get("candidate_blockers", []) if str(item)))
    quality_warnings = sorted(
        set(str(item) for payload in stages.values() for item in payload.get("quality_warnings", []) if str(item))
    )
    retrospective_ready = not engineering_blockers and not candidate_blockers
    replay_complete = retrospective_ready and _proved(replay)
    status = "engineering_replay_completed_certification_blocked" if replay_complete else "engineering_chain_built_replay_blocked"
    return {
        "status": status,
        "governed_source_ready": _proved(source),
        "conservative_tradability_policy_ready": bool(source.get("conservative_tradability_policy_ready", False)),
        "immutable_freeze_ready": _proved(freeze),
        "engineering_universe_proxy_ready": _proved(universe),
        "strict_matrix_built": bool(matrix.get("built", False)),
        "strict_matrix_replay_safe": _proved(matrix),
        "v3_tensor_ready": _proved(tensor),
        "research_firewall_ready": _proved(firewall),
        "retrospective_replay_ready": retrospective_ready,
        "four_gpu_replay_completed": replay_complete,
        "engineering_blockers": sorted(set(engineering_blockers)),
        "candidate_blockers": candidate_blockers,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "quality_warnings": quality_warnings,
        "untouched_holdout_ready": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_queue_count": 0,
        "portfolio_queue_count": 0,
        "paper_queue_count": 0,
        "live_queue_count": 0,
        "stages": {key: dict(value) for key, value in stages.items()},
    }


def _stage(stages: Mapping[str, Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return stages.get(name) or {}


def _proved(payload: Mapping[str, Any]) -> bool:
    if payload.get("ready") is not True:
        return False
    paths = [Path(str(path)) for path in payload.get("proof_paths", []) if str(path)]
    return bool(paths) and all(path.is_file() for path in paths)


def _blockers(name: str, payload: Mapping[str, Any]) -> list[str]:
    explicit = [str(item) for item in payload.get("blockers", []) if str(item)]
    return explicit or [f"{name}_proof_incomplete"]
