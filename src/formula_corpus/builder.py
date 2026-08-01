"""Build reusable formula corpora from local research artifacts."""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from factor_store import LocalFactorStore, stable_formula_hash
from formula_search.generator import generate_seed_formulas
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research.candidates import default_candidates

from .models import (
    FormulaCorpusBuildResult,
    FormulaCorpusConfig,
    FormulaCorpusRecord,
    FormulaCorpusStats,
    FormulaPreferencePair,
    FormulaSequenceRecord,
)


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"
STATUS_PRIORITY = {
    "production_candidate": 5,
    "approved": 4,
    "candidate": 3,
    "skipped_existing": 3,
    "rejected": 2,
    "error": 1,
    "invalid": 0,
}


def build_formula_corpus(config: FormulaCorpusConfig) -> FormulaCorpusBuildResult:
    created_at = _utc_now()
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    records_by_hash: dict[str, FormulaCorpusRecord] = {}

    for payload in _iter_sources(config, warnings):
        record = _record_from_payload(payload, warnings)
        if record is None:
            continue
        previous = records_by_hash.get(record.formula_hash)
        records_by_hash[record.formula_hash] = record if previous is None else _merge_records(previous, record)

    records = sorted(records_by_hash.values(), key=lambda item: (not item.valid, -item.score, item.formula_hash))
    if config.max_records is not None:
        records = records[: max(0, int(config.max_records))]
    split_by_hash = _assign_splits(records, config)
    sequences = build_formula_sequences(records, split_by_hash)
    preferences = build_formula_preferences(records, split_by_hash, config)
    stats = _build_stats(records, sequences, preferences)

    corpus_path = output_dir / "formula_corpus.jsonl"
    sequences_path = output_dir / "formula_sequences.jsonl"
    preferences_path = output_dir / "formula_preferences.jsonl"
    stats_path = output_dir / "formula_corpus_stats.json"
    result_path = output_dir / "formula_corpus_build_result.json"
    report_path = output_dir / "formula_corpus_report.md"

    write_jsonl_artifact(corpus_path, [record.to_dict() for record in records], "formula_corpus", "formula_corpus")
    write_jsonl_artifact(
        sequences_path,
        [sequence.to_dict() for sequence in sequences],
        "formula_sequences",
        "formula_corpus",
    )
    write_jsonl_artifact(
        preferences_path,
        [pair.to_dict() for pair in preferences],
        "formula_preferences",
        "formula_corpus",
    )
    write_json_artifact(stats_path, stats.to_dict(), "formula_corpus_stats", "formula_corpus")

    result = FormulaCorpusBuildResult(
        created_at=created_at,
        config=config.to_dict(),
        stats=stats.to_dict(),
        paths={
            "formula_corpus_path": str(corpus_path),
            "formula_sequences_path": str(sequences_path),
            "formula_preferences_path": str(preferences_path),
            "formula_corpus_stats_path": str(stats_path),
            "formula_corpus_report_path": str(report_path),
            "formula_corpus_build_result_path": str(result_path),
        },
        warnings=warnings,
    )
    write_json_artifact(result_path, result.to_dict(), "formula_corpus_build_result", "formula_corpus")
    report_path.write_text(_render_report(result, records, preferences), encoding="utf-8")
    return result


def build_formula_sequences(
    records: list[FormulaCorpusRecord],
    split_by_hash: dict[str, str],
) -> list[FormulaSequenceRecord]:
    sequences: list[FormulaSequenceRecord] = []
    for record in records:
        if not record.valid or len(record.formula_tokens) < 2:
            continue
        split = split_by_hash.get(record.formula_hash, "train")
        weight = _record_weight(record)
        for position in range(1, len(record.formula_tokens)):
            sequences.append(
                FormulaSequenceRecord(
                    formula_hash=record.formula_hash,
                    split=split,
                    prefix_tokens=record.formula_tokens[:position],
                    target_token=int(record.formula_tokens[position]),
                    position=position,
                    weight=weight,
                )
            )
    return sequences


def build_formula_preferences(
    records: list[FormulaCorpusRecord],
    split_by_hash: dict[str, str],
    config: FormulaCorpusConfig,
) -> list[FormulaPreferencePair]:
    valid = [record for record in records if record.valid]
    ranked = sorted(valid, key=_preference_rank, reverse=True)
    pairs: list[FormulaPreferencePair] = []
    for preferred in ranked:
        for rejected in reversed(ranked):
            if preferred.formula_hash == rejected.formula_hash:
                continue
            if _preference_rank(preferred) <= _preference_rank(rejected):
                continue
            score_gap = preferred.score - rejected.score
            if score_gap < config.preference_min_score_gap:
                continue
            pair_id = f"pref_{preferred.formula_hash[:10]}_{rejected.formula_hash[:10]}"
            pairs.append(
                FormulaPreferencePair(
                    pair_id=pair_id,
                    split=split_by_hash.get(preferred.formula_hash, "train"),
                    preferred_hash=preferred.formula_hash,
                    rejected_hash=rejected.formula_hash,
                    preferred_tokens=preferred.formula_tokens,
                    rejected_tokens=rejected.formula_tokens,
                    preferred_score=float(preferred.score),
                    rejected_score=float(rejected.score),
                    reason="status_score_rank",
                )
            )
            if len(pairs) >= max(0, config.max_preference_pairs):
                return pairs
    return pairs


def load_formula_corpus(path: str | Path) -> list[FormulaCorpusRecord]:
    records = []
    for payload in _read_jsonl(Path(path)):
        records.append(FormulaCorpusRecord(**payload))
    return records


def _iter_sources(config: FormulaCorpusConfig, warnings: list[str]) -> Iterable[dict[str, Any]]:
    if config.include_defaults:
        for candidate in default_candidates():
            yield {"source": "default", "candidate": candidate.to_dict()}
    if config.include_seed:
        for candidate in generate_seed_formulas():
            yield {"source": "seed", "search_candidate": candidate.to_dict()}
    if config.include_factor_store and config.factor_store_dir:
        store = LocalFactorStore(config.factor_store_dir)
        for record in store.load_factors():
            yield {"source": "factor_store", "factor_record": record.__dict__}
    for path in _artifact_paths(config, warnings):
        yield from _payloads_from_artifact(path, warnings)


def _artifact_paths(config: FormulaCorpusConfig, warnings: list[str]) -> list[Path]:
    paths: list[Path] = []
    for directory in config.artifact_dirs:
        root = Path(directory)
        if not root.exists():
            warnings.append(f"artifact_dir_missing:{root}")
            continue
        for pattern in (
            "search_candidates.jsonl",
            "search_result.json",
            "batch_results.jsonl",
            "batch_result.json",
            "neural_search_result.json",
        ):
            paths.extend(root.rglob(pattern))
    for catalog_path in config.artifact_catalog_paths:
        catalog = _read_json(Path(catalog_path))
        entries = catalog.get("entries", []) if isinstance(catalog, dict) else []
        base = Path(catalog_path).parent
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            candidate = Path(str(entry["path"]))
            if not candidate.is_absolute():
                candidate = base / candidate
            if candidate.name in {
                "search_candidates.jsonl",
                "search_result.json",
                "batch_results.jsonl",
                "batch_result.json",
                "neural_search_result.json",
            }:
                paths.append(candidate)
    return sorted(set(paths))


def _payloads_from_artifact(path: Path, warnings: list[str]) -> Iterable[dict[str, Any]]:
    if not path.exists():
        warnings.append(f"artifact_missing:{path}")
        return
    if path.suffix == ".jsonl":
        for payload in _read_jsonl(path):
            yield {"source": path.name, "artifact_path": str(path), "payload": payload}
        return
    payload = _read_json(path)
    if not payload:
        return
    if path.name == "search_result.json":
        for item in payload.get("best_candidates", []) or []:
            yield {"source": path.name, "artifact_path": str(path), "payload": item}
    elif path.name == "batch_result.json":
        for item in payload.get("results", []) or []:
            yield {"source": path.name, "artifact_path": str(path), "payload": item}
    elif path.name == "neural_search_result.json":
        for item in payload.get("best_formulas", []) or []:
            yield {"source": path.name, "artifact_path": str(path), "payload": item}


def _record_from_payload(payload: dict[str, Any], warnings: list[str]) -> FormulaCorpusRecord | None:
    source = str(payload.get("source") or "unknown")
    raw = payload.get("candidate") or payload.get("search_candidate") or payload.get("factor_record") or payload.get("payload") or payload
    if not isinstance(raw, dict):
        return None
    candidate = raw.get("candidate") if isinstance(raw.get("candidate"), dict) else raw
    try:
        tokens = _extract_tokens(candidate)
    except (TypeError, ValueError) as exc:
        warnings.append(f"formula_tokens_invalid:{source}:{exc}")
        return None
    if tokens is None:
        warnings.append(f"formula_tokens_missing:{source}")
        return None
    vm = StackVM()
    try:
        names = _extract_names(candidate, tokens)
    except (IndexError, ValueError) as exc:
        warnings.append(f"formula_names_invalid:{source}:{exc}")
        return None
    valid, reason = vm.validate_with_reason(tokens)
    canonical = vm.canonical_formula(tokens)
    formula_hash = str(
        candidate.get("formula_hash")
        or raw.get("formula_hash")
        or stable_formula_hash(tokens, canonical, FEATURE_VERSION, OPERATOR_VERSION)
    )
    status = str(candidate.get("status") or raw.get("status") or "candidate")
    metrics = _extract_metrics(candidate, raw)
    score = _score_from_payload(candidate, raw, metrics)
    factor_id = candidate.get("factor_id") or raw.get("factor_id")
    source_name = str(candidate.get("source") or source)
    metadata = _metadata_from_payload(candidate, raw, payload)
    return FormulaCorpusRecord(
        formula_hash=formula_hash,
        formula_tokens=tokens,
        formula_names=names,
        canonical_formula=canonical,
        valid=bool(valid),
        validation_reason=reason,
        complexity=int(candidate.get("complexity") or metadata.get("formula_complexity") or vm.formula_complexity(tokens)),
        lookback=int(candidate.get("lookback") or metadata.get("formula_lookback") or vm.formula_lookback(tokens)),
        status=status if valid else "invalid",
        score=float(score),
        sources=[source_name],
        factor_ids=[str(factor_id)] if factor_id else [],
        metrics=metrics,
        metadata=metadata,
    )


def _extract_tokens(payload: dict[str, Any]) -> list[int] | None:
    tokens = payload.get("formula_tokens") or payload.get("tokens")
    if tokens is None and isinstance(payload.get("formula"), list):
        formula = payload.get("formula")
        if all(isinstance(item, str) for item in formula):
            return [FORMULA_VOCAB.encode_name(str(item)) for item in formula]
    if tokens is None:
        return None
    return [int(token) for token in tokens]


def _extract_names(payload: dict[str, Any], tokens: list[int]) -> list[str]:
    names = payload.get("formula_names") or payload.get("formula") or payload.get("names")
    if isinstance(names, list) and all(isinstance(item, str) for item in names):
        return [str(item) for item in names]
    return FORMULA_VOCAB.decode_tokens(tokens)


def _extract_metrics(candidate: dict[str, Any], raw: dict[str, Any]) -> dict[str, float] | None:
    metrics = candidate.get("metrics") or raw.get("metrics")
    if not isinstance(metrics, dict):
        split_metrics = raw.get("metrics_by_split")
        if isinstance(split_metrics, dict):
            metrics = split_metrics.get("all")
    if not isinstance(metrics, dict):
        return None
    result = {}
    for key, value in metrics.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _score_from_payload(candidate: dict[str, Any], raw: dict[str, Any], metrics: dict[str, float] | None) -> float:
    for payload in (candidate, raw):
        if "score" in payload:
            try:
                return float(payload.get("score") or 0.0)
            except (TypeError, ValueError):
                pass
    if metrics:
        return float(metrics.get("score", 0.0) or 0.0)
    reward = candidate.get("reward") or raw.get("reward")
    try:
        return float(reward or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _metadata_from_payload(candidate: dict[str, Any], raw: dict[str, Any], wrapper: dict[str, Any]) -> dict[str, Any]:
    metadata = {}
    for payload in (candidate.get("metadata"), raw.get("metadata")):
        if isinstance(payload, dict):
            metadata.update(payload)
    for key in ("generation", "parent_hashes", "search_id", "batch_id", "artifact_path"):
        value = candidate.get(key, raw.get(key, wrapper.get(key)))
        if value is not None:
            metadata[key] = value
    return metadata


def _merge_records(left: FormulaCorpusRecord, right: FormulaCorpusRecord) -> FormulaCorpusRecord:
    keep = left
    other = right
    if _preference_rank(right) > _preference_rank(left):
        keep, other = right, left
    metadata = dict(other.metadata)
    metadata.update(keep.metadata)
    source_refs = sorted(set(left.sources + right.sources))
    factor_ids = sorted(set(left.factor_ids + right.factor_ids))
    return FormulaCorpusRecord(
        formula_hash=keep.formula_hash,
        formula_tokens=keep.formula_tokens,
        formula_names=keep.formula_names,
        canonical_formula=keep.canonical_formula,
        valid=left.valid or right.valid,
        validation_reason=keep.validation_reason if keep.valid else other.validation_reason,
        complexity=max(left.complexity, right.complexity),
        lookback=max(left.lookback, right.lookback),
        status=keep.status,
        score=max(left.score, right.score),
        sources=source_refs,
        factor_ids=factor_ids,
        metrics=keep.metrics or other.metrics,
        metadata=metadata,
    )


def _assign_splits(records: list[FormulaCorpusRecord], config: FormulaCorpusConfig) -> dict[str, str]:
    valid_hashes = [record.formula_hash for record in records if record.valid]
    rng = random.Random(config.seed)
    rng.shuffle(valid_hashes)
    n = len(valid_hashes)
    train_cut = int(n * config.train_ratio)
    valid_cut = train_cut + int(n * config.valid_ratio)
    split = {}
    for idx, formula_hash in enumerate(valid_hashes):
        if idx < train_cut:
            split[formula_hash] = "train"
        elif idx < valid_cut:
            split[formula_hash] = "valid"
        else:
            split[formula_hash] = "test"
    return split


def _build_stats(
    records: list[FormulaCorpusRecord],
    sequences: list[FormulaSequenceRecord],
    preferences: list[FormulaPreferencePair],
) -> FormulaCorpusStats:
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
        for source in record.sources:
            source_counts[source] = source_counts.get(source, 0) + 1
    valid = [record for record in records if record.valid]
    return FormulaCorpusStats(
        total_records=len(records),
        valid_records=len(valid),
        invalid_records=len(records) - len(valid),
        sequence_records=len(sequences),
        preference_pairs=len(preferences),
        status_counts=status_counts,
        source_counts=source_counts,
        max_complexity=max((record.complexity for record in records), default=0),
        max_lookback=max((record.lookback for record in records), default=0),
        avg_score=float(sum(record.score for record in valid) / len(valid)) if valid else 0.0,
    )


def _record_weight(record: FormulaCorpusRecord) -> float:
    return 1.0 + max(0.0, float(record.score))


def _preference_rank(record: FormulaCorpusRecord) -> tuple[int, float]:
    return (STATUS_PRIORITY.get(record.status, 0), float(record.score))


def _render_report(
    result: FormulaCorpusBuildResult,
    records: list[FormulaCorpusRecord],
    preferences: list[FormulaPreferencePair],
) -> str:
    stats = result.stats
    lines = [
        "# Formula Corpus Report",
        "",
        f"- created_at: `{result.created_at}`",
        f"- total_records: {stats.get('total_records', 0)}",
        f"- valid_records: {stats.get('valid_records', 0)}",
        f"- sequence_records: {stats.get('sequence_records', 0)}",
        f"- preference_pairs: {len(preferences)}",
        "",
        "## Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for status, count in sorted((stats.get("status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## Top Formulas", "", "| formula | status | score | source |", "| --- | --- | ---: | --- |"])
    for record in sorted(records, key=_preference_rank, reverse=True)[:20]:
        lines.append(
            f"| `{' '.join(record.formula_names)}` | {record.status} | {record.score:.6f} | {', '.join(record.sources)} |"
        )
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings[:50])
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
