"""Security identity, lifecycle, and active-mask derivations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
import unicodedata

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_generation,
    validate_generation,
)

from .models import ActiveSecurityMask, SecurityLifecycleRecord


def build_security_lifecycle(securities: Iterable[dict]) -> list[SecurityLifecycleRecord]:
    records: list[SecurityLifecycleRecord] = []
    for item in securities:
        ts_code = str(item.get("ts_code") or "")
        list_date = str(item.get("list_date") or "")
        if not ts_code or not list_date:
            continue
        name = str(item.get("raw_name") or item.get("name") or "")
        status = str(item.get("list_status") or "unknown").upper()
        records.append(
            SecurityLifecycleRecord(
                ts_code=ts_code,
                symbol=str(item.get("symbol") or ts_code.split(".")[0]),
                name=name,
                list_date=list_date,
                delist_date=str(item.get("delist_date")) if item.get("delist_date") not in {None, ""} else None,
                list_status=status,
                is_st=bool(item.get("is_st", False)),
                exchange=str(item.get("exchange") or "") or None,
                board=str(item.get("board") or "") or None,
                industry=str(item.get("industry") or "") or None,
                area=str(item.get("area") or "") or None,
            )
        )
    return sorted(records, key=lambda record: record.ts_code)


def build_active_security_mask(
    lifecycle: Iterable[SecurityLifecycleRecord],
    trade_dates: Iterable[str],
    min_listing_days: int = 0,
    exclude_st: bool = False,
    include_paused: bool = False,
    include_delisted_history: bool = True,
    board_filters: set[str] | None = None,
    exchange_filters: set[str] | None = None,
) -> list[ActiveSecurityMask]:
    rows: list[ActiveSecurityMask] = []
    for security in lifecycle:
        if board_filters and (security.board or "") not in board_filters:
            continue
        if exchange_filters and (security.exchange or "") not in exchange_filters:
            continue
        for trade_date in sorted(trade_dates):
            active, reason, age = _active_reason(
                security,
                trade_date,
                min_listing_days=min_listing_days,
                exclude_st=exclude_st,
                include_paused=include_paused,
                include_delisted_history=include_delisted_history,
            )
            rows.append(
                ActiveSecurityMask(
                    ts_code=security.ts_code,
                    trade_date=str(trade_date),
                    is_active=active,
                    reason=reason,
                    listing_age_days=age,
                    list_status=security.list_status,
                    is_st=security.is_st,
                )
            )
    return rows


def load_security_lifecycle(path: str | Path) -> list[SecurityLifecycleRecord]:
    target = Path(path)
    if not target.exists():
        return []
    return [SecurityLifecycleRecord(**json.loads(line)) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_active_security_mask(path: str | Path) -> list[ActiveSecurityMask]:
    target = Path(path)
    if not target.exists():
        return []
    return [ActiveSecurityMask(**json.loads(line)) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _active_reason(
    security: SecurityLifecycleRecord,
    trade_date: str,
    min_listing_days: int,
    exclude_st: bool,
    include_paused: bool,
    include_delisted_history: bool,
) -> tuple[bool, str, int]:
    age = _date_diff_days(security.list_date, trade_date)
    if age < 0:
        return False, "not_listed_yet", age
    if age < min_listing_days:
        return False, "listing_age_below_minimum", age
    if exclude_st and security.is_st:
        return False, "st_excluded", age
    if security.list_status == "P" and not include_paused:
        return False, "paused", age
    if security.delist_date and trade_date > security.delist_date:
        return False, "delisted", age
    return True, "active", age


def _date_diff_days(left: str, right: str) -> int:
    try:
        return (datetime.strptime(right, "%Y%m%d") - datetime.strptime(left, "%Y%m%d")).days
    except ValueError:
        return -999999


_IDENTITY_EVENT_TYPES = frozenset(
    {
        "security_code_change",
        "security_name_change",
        "listing",
        "delisting",
    }
)
_IDENTITY_LIFECYCLE_STATES = frozenset({"unlisted", "listed", "delisted"})
_IDENTITY_TIMINGS = frozenset({"before_open", "intraday", "after_close"})
_SECURITY_CODE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CHINESE_DATE = r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日"
_EVENT_SCHEMA_KEYS = frozenset(
    {
        "event_id",
        "event_version_id",
        "version_number",
        "supersedes_event_version_id",
        "security_id",
        "event_type",
        "known_at",
        "known_timing",
        "effective_at",
        "effective_timing",
        "payload",
        "source_evidence_hash",
        "pit_evidence_eligible",
    }
)
_IDENTITY_INTERVAL_EVIDENCE_SCHEMA = (
    "pit_security_identity_lifecycle_intervals_v1"
)
_IDENTITY_INTERVAL_MANIFEST = "security_identity_lifecycle_manifest.json"
_IDENTITY_INTERVAL_FILE = "security_identity_lifecycle_intervals.jsonl"
_IDENTITY_SECURITY_IDS_FILE = "security_identity_security_ids.jsonl"
_IDENTITY_TRADE_DATES_FILE = "security_identity_trade_dates.jsonl"
_IDENTITY_SEEDS_FILE = "security_identity_pre_span_seeds.jsonl"
_IDENTITY_EVENTS_FILE = "security_identity_event_versions.jsonl"
_IDENTITY_INTERVAL_FIELDS = frozenset(
    {
        "security_id",
        "trade_date_start",
        "trade_date_end",
        "security_code",
        "security_name",
        "lifecycle_state",
        "list_date",
        "delist_date",
        "identity_resolved",
        "identity_unique",
        "active_on_trade_date",
    }
)


def _identity_derivation_implementation_root() -> str:
    """Bind every identity semantic result to the complete derivation code."""

    return canonical_hash(
        {
            "implementation_identity": (
                "security_identity_lifecycle_derivation_v1"
            ),
            "source_module_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }
    )


def derive_security_identity_lifecycle_event_candidates(
    *, documents: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Parse verified announcement text into strict event-version candidates.

    Each document must carry its immutable source hashes, conservative known
    timing, and an already-adjudicated stable ``security_id``.  The module
    recognizes only explicit code/name changes, initial listing, and
    delisting language.  It never consults a current security master and does
    not infer missing identities or dates.  Trading halts and listing
    suspensions are routed to the control-state seam instead.
    """

    blockers: dict[str, dict[str, Any]] = {}
    governance_blockers: dict[str, dict[str, Any]] = {}

    def block(row: Mapping[str, Any]) -> None:
        value = dict(row)
        blockers.setdefault(canonical_hash(value), value)

    def governance_block(row: Mapping[str, Any]) -> None:
        value = dict(row)
        governance_blockers.setdefault(canonical_hash(value), value)

    events: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    document_rows = sorted((dict(row) for row in documents), key=canonical_hash)
    announcement_counts: dict[str, int] = {}
    for document in document_rows:
        announcement_id = str(document.get("announcement_id") or "")
        announcement_counts[announcement_id] = (
            announcement_counts.get(announcement_id, 0) + 1
        )
    for document in document_rows:
        announcement_id = str(document.get("announcement_id") or "")
        if announcement_id and announcement_counts[announcement_id] != 1:
            block(
                {
                    "announcement_id": announcement_id,
                    "code": "identity_event_duplicate_announcement_id",
                }
            )
            continue
        preflight = _identity_document_preflight(document)
        if preflight:
            for code in preflight:
                block({"announcement_id": announcement_id, "code": code})
            continue

        text = str(document["document_text"])
        compact = _identity_compact_text(text)
        security_id = str(document["security_id"])
        known_at = str(document["known_at"])
        known_timing = str(document["known_timing"])
        exchange = _identity_exchange_from_text(compact)
        document_events: list[
            tuple[str, str, str, dict[str, Any], str]
        ] = []

        code_claimed = "证券代码" in compact and "变更为" in compact
        name_claimed = "证券简称" in compact and "变更为" in compact
        code_payload: dict[str, Any] | None = None
        a_share_index: int | None = None
        if code_claimed:
            if exchange is None:
                block(
                    {
                        "announcement_id": announcement_id,
                        "code": "identity_event_exchange_unresolved",
                        "event_type": "security_code_change",
                    }
                )
            else:
                code_payload, a_share_index = _identity_code_change_payload(
                    compact, exchange
                )
                if code_payload is None:
                    block(
                        {
                            "announcement_id": announcement_id,
                            "code": "identity_event_old_new_code_unresolved",
                        }
                    )

        name_payload: dict[str, Any] | None = None
        if name_claimed:
            name_payload = _identity_name_change_payload(
                compact, a_share_index=a_share_index
            )
            if name_payload is None:
                block(
                    {
                        "announcement_id": announcement_id,
                        "code": "identity_event_old_new_name_unresolved",
                    }
                )

        if code_claimed or name_claimed:
            effective_dates = _identity_change_effective_dates(compact)
            if len(effective_dates) != 1:
                block(
                    {
                        "announcement_id": announcement_id,
                        "code": (
                            "identity_event_effective_date_missing"
                            if not effective_dates
                            else "identity_event_effective_date_ambiguous"
                        ),
                        "event_types": sorted(
                            event_type
                            for event_type, claimed in (
                                ("security_code_change", code_claimed),
                                ("security_name_change", name_claimed),
                            )
                            if claimed
                        ),
                    }
                )
            else:
                effective_at = next(iter(effective_dates))
                if code_payload is not None:
                    document_events.append(
                        (
                            "security_code_change",
                            effective_at,
                            "before_open",
                            code_payload,
                            "explicit_code_change_activation_date_v1",
                        )
                    )
                if name_payload is not None:
                    document_events.append(
                        (
                            "security_name_change",
                            effective_at,
                            "before_open",
                            name_payload,
                            "explicit_name_change_activation_date_v1",
                        )
                    )
        elif "暂停上市" in compact:
            block(
                {
                    "announcement_id": announcement_id,
                    "code": (
                        "identity_event_listing_suspension_requires_control_state"
                    ),
                }
            )
        elif "终止上市" in compact:
            effective_dates = _identity_delisting_effective_dates(
                compact, announcement_date=str(document["announcement_date"])
            )
            if len(effective_dates) != 1:
                block(
                    {
                        "announcement_id": announcement_id,
                        "code": (
                            "identity_event_effective_date_missing"
                            if not effective_dates
                            else "identity_event_effective_date_ambiguous"
                        ),
                        "event_types": ["delisting"],
                    }
                )
            else:
                document_events.append(
                    (
                        "delisting",
                        next(iter(effective_dates)),
                        "before_open",
                        {},
                        _identity_delisting_parse_rule(
                            compact,
                            effective_at=next(iter(effective_dates)),
                        ),
                    )
                )
        elif _identity_initial_listing_claimed(compact):
            effective_dates = _identity_listing_effective_dates(compact)
            if len(effective_dates) != 1:
                block(
                    {
                        "announcement_id": announcement_id,
                        "code": (
                            "identity_event_effective_date_missing"
                            if not effective_dates
                            else "identity_event_effective_date_ambiguous"
                        ),
                        "event_types": ["listing"],
                    }
                )
            else:
                document_events.append(
                    (
                        "listing",
                        next(iter(effective_dates)),
                        "before_open",
                        {},
                        "explicit_initial_listing_date_v1",
                    )
                )
        elif "停牌" in compact:
            block(
                {
                    "announcement_id": announcement_id,
                    "code": "identity_event_trading_halt_requires_control_state",
                }
            )
        else:
            block(
                {
                    "announcement_id": announcement_id,
                    "code": "identity_lifecycle_event_type_unresolved",
                }
            )

        for (
            event_type,
            effective_at,
            effective_timing,
            payload,
            parse_rule,
        ) in document_events:
            event, event_provenance = _identity_candidate_event(
                document=document,
                security_id=security_id,
                event_type=event_type,
                known_at=known_at,
                known_timing=known_timing,
                effective_at=effective_at,
                effective_timing=effective_timing,
                payload=payload,
                parse_rule=parse_rule,
            )
            events.append(event)
            provenance.append(event_provenance)
        if document_events:
            governance_block(
                {
                    "announcement_id": announcement_id,
                    "code": "identity_event_independent_admission_required",
                }
            )
            if (
                document.get("source_governed_evidence_eligible")
                is not True
            ):
                governance_block(
                    {
                        "announcement_id": announcement_id,
                        "code": "identity_event_source_not_governed",
                    }
                )

    events.sort(
        key=lambda event: (
            str(event["security_id"]),
            str(event["effective_at"]),
            str(event["event_type"]),
            str(event["event_version_id"]),
        )
    )
    provenance.sort(key=lambda row: str(row["event_version_id"]))
    blocker_rows = sorted(blockers.values(), key=canonical_hash)
    governance_blocker_rows = sorted(
        governance_blockers.values(), key=canonical_hash
    )
    semantic = {
        "schema_version": "security_identity_lifecycle_event_candidates_v1",
        "derivation_implementation_root": (
            _identity_derivation_implementation_root()
        ),
        "documents_input_root": canonical_hash(document_rows),
        "events_root": canonical_hash(events),
        "provenance_root": canonical_hash(provenance),
        "blockers": blocker_rows,
        "governance_blockers": governance_blocker_rows,
        "document_count": len(documents),
        "event_candidate_count": len(events),
        "semantic_derivation_complete": not blocker_rows,
        "current_security_master_consulted": False,
        "historical_identity_inferred_from_current_code": False,
        "source_text_retained_in_result": False,
        "event_candidates_require_independent_admission": True,
        "data_admission_eligible": False,
        "independent_admission_verdict_required": True,
    }
    return semantic | {
        "events": events,
        "provenance": provenance,
        "content_hash": canonical_hash(semantic),
    }


def _identity_document_preflight(document: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    text = document.get("document_text")
    announcement_date = _identity_exact_date(
        document.get("announcement_date")
    )
    known_at = _identity_exact_date(document.get("known_at"))
    if not str(document.get("announcement_id") or ""):
        failures.append("identity_event_announcement_id_missing")
    if announcement_date is None:
        failures.append("identity_event_announcement_date_invalid")
    if known_at is None:
        failures.append("identity_event_known_at_invalid")
    elif announcement_date is not None and known_at < announcement_date:
        failures.append("identity_event_known_before_announcement_invalid")
    if str(document.get("known_timing") or "") not in _IDENTITY_TIMINGS:
        failures.append("identity_event_known_timing_invalid")
    if document.get("source_document_verified") is not True:
        failures.append("identity_event_source_document_unverified")
    if document.get("document_text_verified") is not True:
        failures.append("identity_event_document_text_unverified")
    if type(text) is not str or not text.strip():
        failures.append("identity_event_document_text_missing")
    expected_text_hash = str(document.get("source_text_sha256") or "")
    if type(text) is str and (
        _HEX_64.fullmatch(expected_text_hash) is None
        or hashlib.sha256(text.encode()).hexdigest() != expected_text_hash
    ):
        failures.append("identity_event_source_text_hash_mismatch")
    if _HEX_64.fullmatch(
        str(document.get("source_document_sha256") or "")
    ) is None:
        failures.append("identity_event_source_document_hash_invalid")
    if _HEX_64.fullmatch(
        str(document.get("text_extractor_implementation_root") or "")
    ) is None:
        failures.append("identity_event_text_extractor_root_invalid")
    if not str(document.get("security_id") or ""):
        failures.append("identity_event_stable_security_id_missing")
    if _HEX_64.fullmatch(
        str(document.get("stable_identity_evidence_hash") or "")
    ) is None:
        failures.append("identity_event_stable_identity_evidence_invalid")
    version_numbers = document.get("version_number_by_event_type")
    supersedes = document.get("supersedes_event_version_id_by_event_type")
    if version_numbers is not None and (
        not isinstance(version_numbers, Mapping)
        or any(
            key not in _IDENTITY_EVENT_TYPES
            or type(value) is not int
            or value < 1
            for key, value in version_numbers.items()
        )
    ):
        failures.append("identity_event_revision_metadata_invalid")
    if supersedes is not None and (
        not isinstance(supersedes, Mapping)
        or any(
            key not in _IDENTITY_EVENT_TYPES
            or (value is not None and not str(value))
            for key, value in supersedes.items()
        )
    ):
        failures.append("identity_event_revision_metadata_invalid")
    return sorted(set(failures))


def _identity_candidate_event(
    *,
    document: Mapping[str, Any],
    security_id: str,
    event_type: str,
    known_at: str,
    known_timing: str,
    effective_at: str,
    effective_timing: str,
    payload: dict[str, Any],
    parse_rule: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_id = canonical_hash(
        {
            "security_id": security_id,
            "event_type": event_type,
            "effective_at": effective_at,
        }
    )
    version_numbers = document.get("version_number_by_event_type")
    supersedes = document.get("supersedes_event_version_id_by_event_type")
    version_number = (
        version_numbers.get(event_type, 1)
        if isinstance(version_numbers, Mapping)
        else 1
    )
    supersedes_version_id = (
        supersedes.get(event_type)
        if isinstance(supersedes, Mapping)
        else None
    )
    source_evidence_hash = canonical_hash(
        {
            "announcement_id": document["announcement_id"],
            "announcement_date": document["announcement_date"],
            "known_at": known_at,
            "known_timing": known_timing,
            "source_document_sha256": document["source_document_sha256"],
            "source_text_sha256": document["source_text_sha256"],
            "text_extractor_implementation_root": document[
                "text_extractor_implementation_root"
            ],
            "stable_identity_evidence_hash": document[
                "stable_identity_evidence_hash"
            ],
            "derivation_implementation_root": (
                _identity_derivation_implementation_root()
            ),
        }
    )
    event_version_id = canonical_hash(
        {
            "event_id": event_id,
            "version_number": version_number,
            "announcement_id": document["announcement_id"],
            "source_evidence_hash": source_evidence_hash,
            "payload": payload,
        }
    )
    event = {
        "event_id": event_id,
        "event_version_id": event_version_id,
        "version_number": version_number,
        "supersedes_event_version_id": supersedes_version_id,
        "security_id": security_id,
        "event_type": event_type,
        "known_at": known_at,
        "known_timing": known_timing,
        "effective_at": effective_at,
        "effective_timing": effective_timing,
        "payload": payload,
        "source_evidence_hash": source_evidence_hash,
        "pit_evidence_eligible": False,
    }
    if set(event) != _EVENT_SCHEMA_KEYS or not _identity_event_valid(
        event | {"pit_evidence_eligible": True}
    ):
        raise ValueError("security_identity_event_candidate_schema_invalid")
    provenance = {
        "event_id": event_id,
        "event_version_id": event_version_id,
        "announcement_id": document["announcement_id"],
        "announcement_date": document["announcement_date"],
        "source_document_sha256": document["source_document_sha256"],
        "source_text_sha256": document["source_text_sha256"],
        "text_extractor_implementation_root": document[
            "text_extractor_implementation_root"
        ],
        "known_at": known_at,
        "known_timing": known_timing,
        "stable_identity_evidence_hash": document[
            "stable_identity_evidence_hash"
        ],
        "parse_rule": parse_rule,
        "derivation_implementation_root": (
            _identity_derivation_implementation_root()
        ),
        "pit_evidence_eligible": event["pit_evidence_eligible"],
    }
    return event, provenance


def _identity_compact_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", normalized)


def _identity_exchange_from_text(text: str) -> str | None:
    exchanges = {
        exchange
        for marker, exchange in (
            ("深圳证券交易所", "SZ"),
            ("上海证券交易所", "SH"),
            ("北京证券交易所", "BJ"),
        )
        if marker in text
    }
    return next(iter(exchanges)) if len(exchanges) == 1 else None


def _identity_code_change_payload(
    text: str, exchange: str
) -> tuple[dict[str, str] | None, int | None]:
    pattern = re.compile(
        r'(?:原)?证券代码(?:由)?[:：]?[“"]?'
        r'([0-9]{6}(?:[/、,，][0-9]{6})*)[”"]?变更为[“"]?'
        r'([0-9]{6}(?:[/、,，][0-9]{6})*)[”"]?'
    )
    candidates: set[tuple[str, str, int]] = set()
    for old_group, new_group in pattern.findall(text):
        old_codes = re.split(r"[/、,，]", old_group)
        new_codes = re.split(r"[/、,，]", new_group)
        old_a = [
            (index, code)
            for index, code in enumerate(old_codes)
            if _identity_a_share_code(code, exchange)
        ]
        new_a = [
            (index, code)
            for index, code in enumerate(new_codes)
            if _identity_a_share_code(code, exchange)
        ]
        if (
            len(old_a) == 1
            and len(new_a) == 1
            and old_a[0][0] == new_a[0][0]
            and old_a[0][1] != new_a[0][1]
        ):
            candidates.add((old_a[0][1], new_a[0][1], old_a[0][0]))
    if len(candidates) != 1:
        return None, None
    old_code, new_code, index = next(iter(candidates))
    return (
        {
            "old_security_code": f"{old_code}.{exchange}",
            "new_security_code": f"{new_code}.{exchange}",
        },
        index,
    )


def _identity_name_change_payload(
    text: str, *, a_share_index: int | None
) -> dict[str, str] | None:
    pattern = re.compile(
        r'(?:原)?证券简称(?:由)?[:：]?[“"]([^”"]{1,80})[”"]'
        r'变更为[“"]([^”"]{1,80})[”"]'
    )
    candidates: set[tuple[str, str]] = set()
    for old_group, new_group in pattern.findall(text):
        old_names = re.split(r"[/、]", old_group)
        new_names = re.split(r"[/、]", new_group)
        if a_share_index is None:
            if len(old_names) != 1 or len(new_names) != 1:
                continue
            index = 0
        elif a_share_index >= len(old_names) or a_share_index >= len(new_names):
            continue
        else:
            index = a_share_index
        old_name = old_names[index].strip()
        new_name = new_names[index].strip()
        if old_name and new_name and old_name != new_name:
            candidates.add((old_name, new_name))
    if len(candidates) != 1:
        return None
    old_name, new_name = next(iter(candidates))
    return {
        "old_security_name": old_name,
        "new_security_name": new_name,
    }


def _identity_change_effective_dates(text: str) -> set[str]:
    patterns = (
        re.compile(
            r"(?:本次)?变更后的证券简称(?:和|及)证券代码启用日期"
            r"(?:为|[:：])?" + _CHINESE_DATE
        ),
        re.compile(r"自" + _CHINESE_DATE + r"开市起(?:以|启用)变更后"),
    )
    return _identity_dates_from_patterns(text, patterns)


def _identity_delisting_effective_dates(
    text: str, *, announcement_date: str
) -> set[str]:
    patterns = (
        re.compile(r"股票于" + _CHINESE_DATE + r"终止上市"),
        re.compile(r"终止上市日期(?:为|[:：])?" + _CHINESE_DATE),
    )
    values = _identity_dates_from_patterns(text, patterns)
    partial = re.compile(
        r"股票于([0-9]{1,2})月([0-9]{1,2})日终止上市"
    ).findall(text)
    year = announcement_date[:4]
    for month, day in partial:
        value = f"{int(year):04d}{int(month):02d}{int(day):02d}"
        if _identity_exact_date(value) is not None and value >= announcement_date:
            values.add(value)
    return values


def _identity_delisting_parse_rule(text: str, *, effective_at: str) -> str:
    year = int(effective_at[:4])
    month = int(effective_at[4:6])
    day = int(effective_at[6:8])
    if re.search(
        rf"股票于0*{year}年0*{month}月0*{day}日终止上市", text
    ):
        return "explicit_delisting_full_date_v1"
    return "explicit_delisting_month_day_bound_to_announcement_year_v1"


def _identity_listing_effective_dates(text: str) -> set[str]:
    patterns = (
        re.compile(r"(?:股票)?上市(?:交易)?日期(?:为|[:：])?" + _CHINESE_DATE),
        re.compile(r"股票将于" + _CHINESE_DATE + r"[^。]{0,80}上市交易"),
    )
    return _identity_dates_from_patterns(text, patterns)


def _identity_dates_from_patterns(
    text: str, patterns: Sequence[re.Pattern[str]]
) -> set[str]:
    values: set[str] = set()
    for pattern in patterns:
        for year, month, day in pattern.findall(text):
            value = f"{int(year):04d}{int(month):02d}{int(day):02d}"
            if _identity_exact_date(value) is not None:
                values.add(value)
    return values


def _identity_initial_listing_claimed(text: str) -> bool:
    return "首次公开发行" in text and (
        "上市公告书" in text or "上市交易" in text
    )


def _identity_a_share_code(code: str, exchange: str) -> bool:
    return bool(
        len(code) == 6
        and code.isdigit()
        and (
            (exchange == "SZ" and code[0] in {"0", "3"})
            or (exchange == "SH" and code[0] == "6")
            or (exchange == "BJ" and code[0] in {"4", "8"})
        )
    )


def derive_security_identity_lifecycle_timeline(
    *,
    security_ids: Sequence[str],
    trade_dates: Sequence[str],
    pre_span_seeds: Sequence[Mapping[str, Any]],
    event_versions: Sequence[Mapping[str, Any]],
    materialize_daily_rows: bool = True,
) -> dict[str, Any]:
    """Resolve stable identities, aliases, and lifecycle without backfilling.

    The interface accepts only evidence-bearing pre-span seeds and versioned
    events.  A current security master is intentionally not an input.  Every
    transition waits until both its economic effective time and source-known
    time are observable, and any missing identity, invalid revision chain, or
    alias collision makes the affected rows unusable.  This derivation never
    grants data admission; an independent admission verdict must bind its
    content hash to the governed source freeze.
    """

    subjects = [str(value) for value in security_ids]
    if (
        not subjects
        or any(not value for value in subjects)
        or len(subjects) != len(set(subjects))
    ):
        raise ValueError("security_identity_population_invalid")
    subjects = sorted(subjects)
    dates = [str(value) for value in trade_dates]
    if (
        not dates
        or any(_identity_exact_date(value) is None for value in dates)
        or len(dates) != len(set(dates))
    ):
        raise ValueError("security_identity_trade_dates_invalid")
    dates = sorted(dates)

    blockers: dict[str, dict[str, Any]] = {}

    def block(row: Mapping[str, Any]) -> None:
        value = dict(row)
        blockers.setdefault(canonical_hash(value), value)

    seed_groups: dict[str, list[Mapping[str, Any]]] = {}
    for seed in pre_span_seeds:
        security_id = str(seed.get("security_id") or "")
        if security_id not in subjects:
            block(
                {
                    "code": "security_identity_seed_subject_outside_population",
                    "security_id": security_id,
                }
            )
            continue
        seed_groups.setdefault(security_id, []).append(seed)

    seeds: dict[str, Mapping[str, Any]] = {}
    poisoned_subjects: set[str] = set()
    for security_id in subjects:
        candidates = seed_groups.get(security_id, [])
        if not candidates:
            poisoned_subjects.add(security_id)
            block(
                {
                    "code": "security_identity_pre_span_seed_missing",
                    "security_id": security_id,
                }
            )
        elif len(candidates) != 1 or not _identity_seed_valid(
            candidates[0], security_id=security_id, first_trade_date=dates[0]
        ):
            poisoned_subjects.add(security_id)
            block(
                {
                    "code": "security_identity_pre_span_seed_invalid",
                    "security_id": security_id,
                }
            )
        else:
            seeds[security_id] = candidates[0]

    event_groups: dict[str, dict[str, list[dict[str, Any]]]] = {
        security_id: {} for security_id in subjects
    }
    seen_version_ids: set[str] = set()
    for source_event in event_versions:
        event = dict(source_event)
        security_id = str(event.get("security_id") or "")
        event_id = str(event.get("event_id") or "")
        version_id = str(event.get("event_version_id") or "")
        if security_id not in subjects:
            block(
                {
                    "code": "security_identity_event_subject_outside_population",
                    "event_version_id": version_id,
                    "security_id": security_id,
                }
            )
            continue
        if version_id in seen_version_ids or not _identity_event_valid(event):
            poisoned_subjects.add(security_id)
            block(
                {
                    "code": "security_identity_event_version_invalid",
                    "event_version_id": version_id,
                    "security_id": security_id,
                }
            )
            continue
        seen_version_ids.add(version_id)
        event["application_date"] = max(
            _identity_transition_date(
                str(event["known_at"]), str(event["known_timing"]), dates
            ),
            _identity_transition_date(
                str(event["effective_at"]),
                str(event["effective_timing"]),
                dates,
            ),
        )
        event_groups[security_id].setdefault(event_id, []).append(event)

    for security_id, families in event_groups.items():
        for event_id, versions in families.items():
            versions.sort(key=lambda row: int(row["version_number"]))
            if not _identity_revision_chain_valid(versions):
                poisoned_subjects.add(security_id)
                block(
                    {
                        "code": "security_identity_event_revision_chain_invalid",
                        "event_id": event_id,
                        "security_id": security_id,
                    }
                )

    rows: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    current_intervals: dict[str, dict[str, Any]] = {}
    collision_dates: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    row_stream = hashlib.sha256()
    for trade_date in dates:
        date_rows: list[dict[str, Any]] = []
        for security_id in subjects:
            if security_id in poisoned_subjects:
                date_rows.append(
                    _identity_unknown_row(security_id, trade_date)
                )
                continue
            seed = seeds[security_id]
            families = event_groups[security_id]
            selected = [
                max(
                    (
                        event
                        for event in versions
                        if str(event["application_date"]) <= trade_date
                    ),
                    key=lambda event: int(event["version_number"]),
                    default=None,
                )
                for versions in families.values()
            ]
            applied = [event for event in selected if event is not None]
            applied.sort(key=_identity_event_order)
            row = _derive_identity_row(
                security_id=security_id,
                trade_date=trade_date,
                seed=seed,
                events=applied,
                block=block,
            )
            date_rows.append(row)
        _invalidate_identity_code_collisions_for_date(
            date_rows,
            trade_date=trade_date,
            collision_dates=collision_dates,
        )
        for row in date_rows:
            row_stream.update(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            _append_identity_interval(
                intervals,
                current_intervals,
                row,
            )
        if materialize_daily_rows:
            rows.extend(date_rows)
    _emit_identity_collision_blockers(
        collision_dates,
        dates=dates,
        block=block,
    )
    intervals.sort(
        key=lambda row: (
            str(row["security_id"]),
            str(row["trade_date_start"]),
        )
    )
    if materialize_daily_rows:
        rows.sort(
            key=lambda row: (
                str(row["security_id"]),
                str(row["trade_date"]),
            )
        )
    blocker_rows = sorted(blockers.values(), key=canonical_hash)
    semantic = {
        "schema_version": "pit_security_identity_lifecycle_timeline_v2",
        "derivation_implementation_root": (
            _identity_derivation_implementation_root()
        ),
        "security_ids_root": canonical_hash(subjects),
        "trade_dates_root": canonical_hash(dates),
        "pre_span_seeds_input_root": canonical_hash(
            _canonical_identity_inputs(pre_span_seeds)
        ),
        "event_versions_input_root": canonical_hash(
            _canonical_identity_inputs(event_versions)
        ),
        "daily_row_count": len(subjects) * len(dates),
        "rows_root": row_stream.hexdigest(),
        "rows_root_semantics": "sha256_canonical_jsonl_trade_date_security_id_v1",
        "intervals_root": canonical_hash(intervals),
        "blockers": blocker_rows,
        "derivation_complete": not blocker_rows,
        "identity_coverage_complete": not blocker_rows,
        "survivorship_backfill_used": False,
        "current_state_backfill_used": False,
        "data_admission_eligible": False,
        "independent_admission_verdict_required": True,
    }
    return semantic | {
        "rows": rows,
        "intervals": intervals,
        "daily_rows_materialized": materialize_daily_rows,
        "security_ids_axis": subjects,
        "trade_dates_axis": dates,
        "pre_span_seeds_inputs": _canonical_identity_inputs(pre_span_seeds),
        "event_versions_inputs": _canonical_identity_inputs(event_versions),
        "content_hash": canonical_hash(semantic),
    }


def publish_security_identity_lifecycle_intervals(
    timeline: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish the bounded interval form used by full market-data builders."""

    supplied = dict(timeline)
    intervals = supplied.pop("intervals", None)
    rows = supplied.pop("rows", None)
    daily_rows_materialized = supplied.pop(
        "daily_rows_materialized", None
    )
    security_ids = supplied.pop("security_ids_axis", None)
    trade_dates = supplied.pop("trade_dates_axis", None)
    pre_span_seeds = supplied.pop("pre_span_seeds_inputs", None)
    event_versions = supplied.pop("event_versions_inputs", None)
    derivation_hash = str(supplied.pop("content_hash", ""))
    if (
        supplied.get("schema_version")
        != "pit_security_identity_lifecycle_timeline_v2"
        or supplied.get("derivation_implementation_root")
        != _identity_derivation_implementation_root()
        or not isinstance(intervals, list)
        or not isinstance(rows, list)
        or type(daily_rows_materialized) is not bool
        or not isinstance(security_ids, list)
        or not isinstance(trade_dates, list)
        or not isinstance(pre_span_seeds, list)
        or not isinstance(event_versions, list)
        or any(not isinstance(row, Mapping) for row in pre_span_seeds)
        or any(not isinstance(row, Mapping) for row in event_versions)
        or supplied.get("data_admission_eligible") is not False
    ):
        raise ValueError("security_identity_lifecycle_timeline_invalid")

    replay = derive_security_identity_lifecycle_timeline(
        security_ids=security_ids,
        trade_dates=trade_dates,
        pre_span_seeds=pre_span_seeds,
        event_versions=event_versions,
        materialize_daily_rows=False,
    )
    replay_semantic = _identity_timeline_semantic(replay)
    if (
        supplied != replay_semantic
        or derivation_hash != replay["content_hash"]
        or intervals != replay["intervals"]
        or security_ids != replay["security_ids_axis"]
        or trade_dates != replay["trade_dates_axis"]
        or pre_span_seeds != replay["pre_span_seeds_inputs"]
        or event_versions != replay["event_versions_inputs"]
        or daily_rows_materialized is not False
        or rows != []
    ):
        raise ValueError("security_identity_lifecycle_timeline_invalid")

    file_rows = {
        _IDENTITY_INTERVAL_FILE: intervals,
        _IDENTITY_SECURITY_IDS_FILE: [
            {"security_id": value} for value in security_ids
        ],
        _IDENTITY_TRADE_DATES_FILE: [
            {"trade_date": value} for value in trade_dates
        ],
        _IDENTITY_SEEDS_FILE: pre_span_seeds,
        _IDENTITY_EVENTS_FILE: event_versions,
    }
    extra_files = {
        name: _identity_canonical_jsonl_bytes(values)
        for name, values in file_rows.items()
    }
    semantic = replay_semantic | {
        "schema_version": _IDENTITY_INTERVAL_EVIDENCE_SCHEMA,
        "derivation_schema_version": replay_semantic["schema_version"],
        "derivation_content_hash": derivation_hash,
        "interval_count": len(intervals),
        "published_files": {
            name: {
                "record_count": len(file_rows[name]),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for name, payload in sorted(extra_files.items())
        },
    }
    return publish_generation(
        output_root,
        prefix="security_identity_lifecycle",
        manifest_name=_IDENTITY_INTERVAL_MANIFEST,
        semantic=semantic,
        extra_files=extra_files,
    )


def validate_security_identity_lifecycle_intervals(
    path: str | Path,
) -> dict[str, Any]:
    """Validate the interval file closure without expanding daily rows."""

    manifest = validate_generation(
        path,
        schema=_IDENTITY_INTERVAL_EVIDENCE_SCHEMA,
        manifest_name=_IDENTITY_INTERVAL_MANIFEST,
    )
    root = Path(str(manifest["manifest_path"])).parent
    evidence_files = {
        _IDENTITY_INTERVAL_FILE,
        _IDENTITY_SECURITY_IDS_FILE,
        _IDENTITY_TRADE_DATES_FILE,
        _IDENTITY_SEEDS_FILE,
        _IDENTITY_EVENTS_FILE,
    }
    observed_files = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    expected_files = {_IDENTITY_INTERVAL_MANIFEST, *evidence_files}
    invalid = bool(
        observed_files != expected_files
        or any(item.is_symlink() for item in root.rglob("*"))
        or manifest.get("data_admission_eligible") is not False
        or manifest.get("independent_admission_verdict_required") is not True
        or manifest.get("derivation_implementation_root")
        != _identity_derivation_implementation_root()
    )
    try:
        files = {
            name: _identity_read_canonical_jsonl(root / name)
            for name in sorted(evidence_files)
        }
        intervals = files[_IDENTITY_INTERVAL_FILE]
        security_id_rows = files[_IDENTITY_SECURITY_IDS_FILE]
        trade_date_rows = files[_IDENTITY_TRADE_DATES_FILE]
        pre_span_seeds = files[_IDENTITY_SEEDS_FILE]
        event_versions = files[_IDENTITY_EVENTS_FILE]
        security_ids = _identity_axis_values(
            security_id_rows, field="security_id"
        )
        trade_dates = _identity_axis_values(
            trade_date_rows, field="trade_date"
        )
        if (
            any(set(row) != _IDENTITY_INTERVAL_FIELDS for row in intervals)
            or pre_span_seeds != _canonical_identity_inputs(pre_span_seeds)
            or event_versions != _canonical_identity_inputs(event_versions)
        ):
            invalid = True
        replay = derive_security_identity_lifecycle_timeline(
            security_ids=security_ids,
            trade_dates=trade_dates,
            pre_span_seeds=pre_span_seeds,
            event_versions=event_versions,
            materialize_daily_rows=False,
        )
        expected_semantic = _identity_interval_publication_semantic(
            replay,
            {
                name: (root / name).read_bytes()
                for name in sorted(evidence_files)
            },
            files,
        )
        observed_semantic = {
            key: value
            for key, value in manifest.items()
            if key not in {"content_hash", "generation_id", "manifest_path"}
        }
        invalid = bool(
            invalid
            or intervals != replay["intervals"]
            or observed_semantic != expected_semantic
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        invalid = True
        intervals = []
    if invalid:
        raise ValueError("security_identity_lifecycle_interval_evidence_invalid")
    return manifest | {"intervals": intervals}


def _identity_timeline_semantic(timeline: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "content_hash",
        "daily_rows_materialized",
        "event_versions_inputs",
        "intervals",
        "pre_span_seeds_inputs",
        "rows",
        "security_ids_axis",
        "trade_dates_axis",
    }
    return {key: value for key, value in timeline.items() if key not in excluded}


def _identity_canonical_jsonl_bytes(
    rows: Sequence[Mapping[str, Any]],
) -> bytes:
    return b"".join(
        json.dumps(
            dict(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )


def _identity_read_canonical_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("security_identity_lifecycle_evidence_file_invalid")
    payload = path.read_bytes()
    if not payload:
        return []
    if not payload.endswith(b"\n"):
        raise ValueError("security_identity_lifecycle_jsonl_invalid")
    rows: list[dict[str, Any]] = []
    for raw_line in payload.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            raise ValueError("security_identity_lifecycle_jsonl_invalid")
        row = json.loads(raw_line[:-1].decode("utf-8"))
        if not isinstance(row, dict):
            raise ValueError(
                "security_identity_lifecycle_interval_object_required"
            )
        if _identity_canonical_jsonl_bytes([row]) != raw_line:
            raise ValueError("security_identity_lifecycle_jsonl_noncanonical")
        rows.append(row)
    return rows


def _identity_axis_values(
    rows: Sequence[Mapping[str, Any]], *, field: str
) -> list[str]:
    if any(set(row) != {field} or type(row[field]) is not str for row in rows):
        raise ValueError("security_identity_lifecycle_axis_invalid")
    values = [str(row[field]) for row in rows]
    if not values or values != sorted(values) or len(values) != len(set(values)):
        raise ValueError("security_identity_lifecycle_axis_invalid")
    return values


def _identity_interval_publication_semantic(
    replay: Mapping[str, Any],
    file_payloads: Mapping[str, bytes],
    file_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    replay_semantic = _identity_timeline_semantic(replay)
    return replay_semantic | {
        "schema_version": _IDENTITY_INTERVAL_EVIDENCE_SCHEMA,
        "derivation_schema_version": replay_semantic["schema_version"],
        "derivation_content_hash": replay["content_hash"],
        "interval_count": len(replay["intervals"]),
        "published_files": {
            name: {
                "record_count": len(file_rows[name]),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for name, payload in sorted(file_payloads.items())
        },
    }


def _identity_seed_valid(
    row: Mapping[str, Any], *, security_id: str, first_trade_date: str
) -> bool:
    as_of_date = _identity_exact_date(row.get("as_of_date"))
    list_date = _identity_exact_date(row.get("list_date"))
    delist_value = row.get("delist_date")
    delist_date = (
        None if delist_value in {None, ""} else _identity_exact_date(delist_value)
    )
    state = str(row.get("lifecycle_state") or "")
    lifecycle_valid = bool(
        state in _IDENTITY_LIFECYCLE_STATES
        and list_date is not None
        and (
            (state == "unlisted" and list_date > str(as_of_date))
            or (
                state == "listed"
                and list_date <= str(as_of_date)
                and delist_date is None
            )
            or (
                state == "delisted"
                and delist_date is not None
                and list_date <= delist_date <= str(as_of_date)
            )
        )
    )
    return bool(
        str(row.get("security_id") or "") == security_id
        and str(row.get("seed_version_id") or "")
        and as_of_date is not None
        and as_of_date < first_trade_date
        and _SECURITY_CODE.fullmatch(str(row.get("security_code") or ""))
        and str(row.get("security_name") or "").strip()
        and lifecycle_valid
        and _HEX_64.fullmatch(
            str(row.get("stable_identity_evidence_hash") or "")
        )
        and _HEX_64.fullmatch(str(row.get("source_evidence_hash") or ""))
        and row.get("pit_evidence_eligible") is True
    )


def _identity_event_valid(row: Mapping[str, Any]) -> bool:
    event_type = str(row.get("event_type") or "")
    payload = row.get("payload")
    version_number = row.get("version_number")
    if not (
        str(row.get("event_id") or "")
        and str(row.get("event_version_id") or "")
        and event_type in _IDENTITY_EVENT_TYPES
        and type(version_number) is int
        and version_number >= 1
        and _identity_exact_date(row.get("known_at")) is not None
        and _identity_exact_date(row.get("effective_at")) is not None
        and str(row.get("known_timing") or "") in _IDENTITY_TIMINGS
        and str(row.get("effective_timing") or "") in _IDENTITY_TIMINGS
        and isinstance(payload, Mapping)
        and _HEX_64.fullmatch(str(row.get("source_evidence_hash") or ""))
        and row.get("pit_evidence_eligible") is True
    ):
        return False
    if event_type == "security_code_change":
        return bool(
            set(payload) == {"old_security_code", "new_security_code"}
            and _SECURITY_CODE.fullmatch(
                str(payload.get("old_security_code") or "")
            )
            and _SECURITY_CODE.fullmatch(
                str(payload.get("new_security_code") or "")
            )
            and payload["old_security_code"] != payload["new_security_code"]
        )
    if event_type == "security_name_change":
        return bool(
            set(payload) == {"old_security_name", "new_security_name"}
            and str(payload.get("old_security_name") or "").strip()
            and str(payload.get("new_security_name") or "").strip()
            and payload["old_security_name"] != payload["new_security_name"]
        )
    return not payload


def _identity_revision_chain_valid(versions: Sequence[Mapping[str, Any]]) -> bool:
    if not versions:
        return False
    family_identity = {
        (
            str(row.get("security_id") or ""),
            str(row.get("event_type") or ""),
            str(row.get("effective_at") or ""),
            str(row.get("effective_timing") or ""),
        )
        for row in versions
    }
    if len(family_identity) != 1:
        return False
    if [row.get("version_number") for row in versions] != list(
        range(1, len(versions) + 1)
    ):
        return False
    for index, version in enumerate(versions):
        expected = (
            None if index == 0 else versions[index - 1]["event_version_id"]
        )
        if version.get("supersedes_event_version_id") != expected:
            return False
        if index and (
            str(version["known_at"]),
            _identity_timing_order(str(version["known_timing"])),
        ) < (
            str(versions[index - 1]["known_at"]),
            _identity_timing_order(str(versions[index - 1]["known_timing"])),
        ):
            return False
    return True


def _derive_identity_row(
    *,
    security_id: str,
    trade_date: str,
    seed: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    block: Callable[[Mapping[str, Any]], None],
) -> dict[str, Any]:
    security_code: str | None = str(seed["security_code"])
    security_name: str | None = str(seed["security_name"])
    lifecycle_state = str(seed["lifecycle_state"])
    list_date: str | None = str(seed["list_date"])
    delist_date: str | None = (
        str(seed["delist_date"])
        if seed.get("delist_date") not in {None, ""}
        else None
    )
    identity_resolved = True

    conflicts = _identity_same_time_conflicts(events)
    for conflict in conflicts:
        block(
            {
                "code": "security_identity_same_effective_time_conflict",
                "effective_at": conflict["effective_at"],
                "event_version_ids": conflict["event_version_ids"],
                "field": conflict["field"],
                "security_id": security_id,
            }
        )
    if conflicts:
        identity_resolved = False
    else:
        for event in events:
            payload = event["payload"]
            event_type = str(event["event_type"])
            mismatch: tuple[str, Any, Any] | None = None
            if event_type == "security_code_change":
                expected = str(payload["old_security_code"])
                if security_code != expected:
                    mismatch = ("security_code", expected, security_code)
                else:
                    security_code = str(payload["new_security_code"])
            elif event_type == "security_name_change":
                expected = str(payload["old_security_name"])
                if security_name != expected:
                    mismatch = ("security_name", expected, security_name)
                else:
                    security_name = str(payload["new_security_name"])
            elif event_type == "listing":
                if lifecycle_state != "unlisted":
                    mismatch = ("lifecycle_state", "unlisted", lifecycle_state)
                else:
                    lifecycle_state = "listed"
                    list_date = str(event["effective_at"])
                    delist_date = None
            elif lifecycle_state != "listed":
                mismatch = ("lifecycle_state", "listed", lifecycle_state)
            else:
                lifecycle_state = "delisted"
                delist_date = str(event["effective_at"])
            if mismatch is not None:
                field, expected, observed = mismatch
                block(
                    {
                        "code": "security_identity_event_chain_mismatch",
                        "event_version_id": event["event_version_id"],
                        "expected": expected,
                        "field": field,
                        "observed": observed,
                        "security_id": security_id,
                    }
                )
                identity_resolved = False
                break

    if not identity_resolved:
        security_code = None
        security_name = None
        lifecycle_state = "unknown"
        list_date = None
        delist_date = None
    active = bool(identity_resolved and lifecycle_state == "listed")
    return {
        "security_id": security_id,
        "trade_date": trade_date,
        "security_code": security_code,
        "security_name": security_name,
        "lifecycle_state": lifecycle_state,
        "list_date": list_date,
        "delist_date": delist_date,
        "identity_resolved": identity_resolved,
        "identity_unique": identity_resolved,
        "active_on_trade_date": active,
        "seed_version_id": seed["seed_version_id"],
        "applied_event_version_ids": sorted(
            str(event["event_version_id"]) for event in events
        ),
    }


def _identity_unknown_row(security_id: str, trade_date: str) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "trade_date": trade_date,
        "security_code": None,
        "security_name": None,
        "lifecycle_state": "unknown",
        "list_date": None,
        "delist_date": None,
        "identity_resolved": False,
        "identity_unique": False,
        "active_on_trade_date": False,
        "seed_version_id": None,
        "applied_event_version_ids": [],
    }


def _identity_same_time_conflicts(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_moment_and_field: dict[
        tuple[str, str, str], list[Mapping[str, Any]]
    ] = {}
    for event in events:
        field = {
            "security_code_change": "security_code",
            "security_name_change": "security_name",
            "listing": "lifecycle_state",
            "delisting": "lifecycle_state",
        }[str(event["event_type"])]
        key = (str(event["effective_at"]), str(event["effective_timing"]), field)
        by_moment_and_field.setdefault(key, []).append(event)
    return [
        {
            "effective_at": effective_at,
            "field": field,
            "event_version_ids": sorted(
                str(event["event_version_id"]) for event in matches
            ),
        }
        for (effective_at, _timing, field), matches in by_moment_and_field.items()
        if len(matches) > 1
    ]


def _invalidate_identity_code_collisions_for_date(
    rows: Sequence[dict[str, Any]],
    *,
    trade_date: str,
    collision_dates: dict[tuple[str, tuple[str, ...]], list[str]],
) -> None:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (
            row["identity_resolved"] is True
            and row["active_on_trade_date"] is True
        ):
            by_code.setdefault(str(row["security_code"]), []).append(row)
    for security_code, matches in by_code.items():
        if len(matches) < 2:
            continue
        security_ids = tuple(
            sorted(str(row["security_id"]) for row in matches)
        )
        collision_dates.setdefault(
            (security_code, security_ids), []
        ).append(trade_date)
        for row in matches:
            row.update(
                {
                    "security_code": None,
                    "security_name": None,
                    "lifecycle_state": "unknown",
                    "list_date": None,
                    "delist_date": None,
                    "identity_resolved": False,
                    "identity_unique": False,
                    "active_on_trade_date": False,
                }
            )


def _emit_identity_collision_blockers(
    collision_dates: Mapping[tuple[str, tuple[str, ...]], Sequence[str]],
    *,
    dates: Sequence[str],
    block: Callable[[Mapping[str, Any]], None],
) -> None:
    date_positions = {trade_date: index for index, trade_date in enumerate(dates)}
    for (security_code, security_ids), values in collision_dates.items():
        ordered = sorted(values, key=date_positions.__getitem__)
        start = previous = ordered[0]
        for trade_date in [*ordered[1:], ""]:
            if (
                trade_date
                and date_positions[trade_date] == date_positions[previous] + 1
            ):
                previous = trade_date
                continue
            block(
                {
                    "code": "security_identity_concurrent_code_collision",
                    "security_code": security_code,
                    "security_ids": list(security_ids),
                    "trade_date_end": previous,
                    "trade_date_start": start,
                }
            )
            if trade_date:
                start = previous = trade_date


def _append_identity_interval(
    intervals: list[dict[str, Any]],
    current_by_subject: dict[str, dict[str, Any]],
    row: Mapping[str, Any],
) -> None:
    fields = (
        "security_code",
        "security_name",
        "lifecycle_state",
        "list_date",
        "delist_date",
        "identity_resolved",
        "identity_unique",
        "active_on_trade_date",
    )
    security_id = str(row["security_id"])
    current = current_by_subject.get(security_id)
    if current is not None and all(
        current[field] == row[field] for field in fields
    ):
        current["trade_date_end"] = row["trade_date"]
        return
    interval = {
        "security_id": security_id,
        "trade_date_start": row["trade_date"],
        "trade_date_end": row["trade_date"],
        **{field: row[field] for field in fields},
    }
    intervals.append(interval)
    current_by_subject[security_id] = interval


def _identity_event_order(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["effective_at"]),
        _identity_timing_order(str(row["effective_timing"])),
        str(row["event_id"]),
        int(row["version_number"]),
    )


def _identity_timing_order(timing: str) -> int:
    return {"before_open": 0, "intraday": 1, "after_close": 2}[timing]


def _identity_transition_date(
    event_date: str, timing: str, trade_dates: Sequence[str]
) -> str:
    delayed = timing in {"intraday", "after_close"}
    candidates = [
        trade_date
        for trade_date in trade_dates
        if (trade_date > event_date if delayed else trade_date >= event_date)
    ]
    return candidates[0] if candidates else "99999999"


def _identity_exact_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _canonical_identity_inputs(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=canonical_hash)
