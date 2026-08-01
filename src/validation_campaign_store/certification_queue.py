"""Build factor certification queues from validation leaderboards."""

from __future__ import annotations

from .models import FactorCertificationQueueRecord
from .registry import LocalValidationCampaignStore


def build_certification_queue(
    store_dir: str,
    *,
    top_k: int = 20,
    certification_policy_profile: str = "sample_lenient_certification",
) -> list[FactorCertificationQueueRecord]:
    store = LocalValidationCampaignStore(store_dir)
    leaderboard = [
        row for row in store.load_leaderboard()
        if row.get("certification_ready") is True
        and ((row.get("metadata") or {}).get("source_result") or {}).get("selection_data_reused") is not True
        and ((row.get("metadata") or {}).get("source_result") or {}).get("untouched_holdout") is True
    ]
    queue: list[FactorCertificationQueueRecord] = []
    for idx, row in enumerate(leaderboard[: max(0, top_k)]):
        metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        candidate = metadata.get("candidate", {}) if isinstance(metadata.get("candidate"), dict) else {}
        source_result = metadata.get("source_result", {}) if isinstance(metadata.get("source_result"), dict) else {}
        paths = source_result.get("paths", {}) if isinstance(source_result.get("paths"), dict) else {}
        queue.append(
            FactorCertificationQueueRecord(
                queue_id=f"certq_{int(row.get('rank', idx + 1)):04d}_{row.get('factor_id')}",
                validation_candidate_id=str(row.get("validation_candidate_id")),
                factor_id=str(row.get("factor_id")),
                priority=idx + 1,
                certification_policy_profile=certification_policy_profile,
                validation_result_path=str(paths.get("validation_lab_report_path") or paths.get("factor_validation_summary_path") or ""),
                factor_store_dir=str(candidate.get("factor_store_dir") or ""),
                status="queued",
                reason=str(row.get("reason") or "certification ready"),
                metadata={"leaderboard": row, "validation_artifacts": paths, "candidate": candidate},
            )
        )
    store.write_certification_queue(queue)
    return queue
