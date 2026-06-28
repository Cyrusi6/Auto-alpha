"""Multiple testing and selection-bias diagnostics."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from factor_store import LocalFactorStore

from .models import MultipleTestingSummary


def analyze_multiple_testing(
    factor_store: LocalFactorStore | None = None,
    alpha_factory_report_path: str | Path | None = None,
    alpha_candidates_path: str | Path | None = None,
    alpha_shortlist_path: str | Path | None = None,
    alpha_full_eval_summary_path: str | Path | None = None,
    formula_search_result_path: str | Path | None = None,
    batch_eval_result_path: str | Path | None = None,
) -> tuple[MultipleTestingSummary, list[dict[str, Any]]]:
    records = []
    records.extend(_records_from_jsonl(alpha_candidates_path, source="alpha_candidate"))
    records.extend(_records_from_jsonl(alpha_shortlist_path, source="alpha_shortlist", selected=True))
    records.extend(_records_from_search(formula_search_result_path))
    records.extend(_records_from_batch_eval(batch_eval_result_path))
    records.extend(_records_from_store(factor_store))
    report = _read_json(alpha_factory_report_path)
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    full_eval = _read_json(alpha_full_eval_summary_path)
    total_trials_hint = int(summary.get("total_trials", 0) or summary.get("candidates_generated", 0) or 0)
    scores = [_score(record) for record in records if _score(record) is not None]
    hashes = [str(record.get("formula_hash")) for record in records if record.get("formula_hash")]
    selected = [record for record in records if record.get("selected") or record.get("status") in {"approved", "production_candidate", "certified"}]
    sources: dict[str, int] = {}
    for record in records:
        source = str(record.get("source", "unknown"))
        sources[source] = sources.get(source, 0) + 1
    unique_count = len(set(hashes))
    effective = max(1, unique_count)
    best = max(scores) if scores else 0.0
    med = float(median(scores)) if scores else 0.0
    std = float(pstdev(scores)) if len(scores) > 1 else 0.0
    score_z = (best - float(mean(scores))) / std if std > 1e-12 and scores else 0.0
    penalty = math.sqrt(2.0 * math.log(max(effective, 1))) * 0.01
    total_trials = max(total_trials_hint, len(records), int(full_eval.get("evaluated_trial_count", 0) or 0))
    approx_rows = []
    for rank, record in enumerate(sorted(records, key=lambda item: _score(item) or -1e9, reverse=True), start=1):
        t_stat = float(record.get("rank_ic_t_stat", record.get("t_stat", score_z)) or 0.0)
        p_value = math.erfc(abs(t_stat) / math.sqrt(2.0))
        approx_rows.append(
            {
                "rank": rank,
                "formula_hash": record.get("formula_hash"),
                "source": record.get("source"),
                "score": _score(record) or 0.0,
                "approx_p_value": p_value,
                "bh_threshold": 0.05 * rank / max(len(records), 1),
            }
        )
    return (
        MultipleTestingSummary(
            total_trials=total_trials,
            valid_trials=len(records),
            evaluated_trials=len(scores),
            selected_trials=len(selected),
            unique_formula_hash_count=unique_count,
            unique_feature_adjusted_formula_count=unique_count,
            source_trial_distribution=sources,
            effective_trial_count=effective,
            best_score=float(best),
            median_score=float(med),
            score_zscore=float(score_z),
            multiple_testing_penalty=float(penalty),
            selection_bias_warning=total_trials > max(10, len(selected) * 3),
            approximate=True,
        ),
        approx_rows,
    )


def _records_from_store(store: LocalFactorStore | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    rows = []
    for record in store.load_factors():
        rows.append(
            {
                "formula_hash": record.formula_hash,
                "score": (record.metrics or {}).get("score", 0.0),
                "status": record.status,
                "source": "factor_store",
            }
        )
    return rows


def _records_from_jsonl(path: str | Path | None, source: str, selected: bool = False) -> list[dict[str, Any]]:
    rows = []
    for record in _read_jsonl(path):
        rows.append(dict(record) | {"source": record.get("source", source), "selected": selected})
    return rows


def _records_from_search(path: str | Path | None) -> list[dict[str, Any]]:
    payload = _read_json(path)
    rows = []
    for item in payload.get("best_candidates", []) if isinstance(payload.get("best_candidates"), list) else []:
        rows.append(dict(item) | {"source": item.get("source", "formula_search"), "selected": True})
    for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
        rows.append(dict(item) | {"source": "formula_search"})
    return rows


def _records_from_batch_eval(path: str | Path | None) -> list[dict[str, Any]]:
    payload = _read_json(path)
    rows = []
    for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
        rows.append(dict(item) | {"source": "formula_batch_eval"})
    return rows


def _score(record: dict[str, Any]) -> float | None:
    for key in ("final_score", "score", "proxy_score"):
        if record.get(key) is not None:
            try:
                value = float(record[key])
                return value if math.isfinite(value) else None
            except (TypeError, ValueError):
                return None
    metrics = record.get("metrics")
    if isinstance(metrics, dict) and metrics.get("score") is not None:
        try:
            return float(metrics["score"])
        except (TypeError, ValueError):
            return None
    return None


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _read_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
