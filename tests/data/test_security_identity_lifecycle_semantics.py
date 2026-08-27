import hashlib

from auto_alpha.data.pit.engine import (
    derive_security_identity_lifecycle_event_candidates,
    derive_security_identity_lifecycle_timeline,
)


def _document(text: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "announcement_id": "1205690369",
        "announcement_date": "20181226",
        "known_at": "20181226",
        "known_timing": "after_close",
        "source_document_sha256": "a" * 64,
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text_extractor_implementation_root": "d" * 64,
        "document_text": text,
        "source_document_verified": True,
        "document_text_verified": True,
        "security_id": "issuer-cm-port",
        "stable_identity_evidence_hash": "b" * 64,
        "source_governed_evidence_eligible": True,
    }
    row.update(overrides)
    return row


def _code_and_name_text() -> str:
    return """
    证券代码：000022/200022 证券简称：深赤湾 A/深赤湾 B
    招商局港口集团股份有限公司
    关于公司股票复牌暨变更证券简称和证券代码实施的公告
    公司证券简称由“深赤湾 A/深赤湾 B”变更为“招商港口/招港 B”，
    公司证券代码由“000022/200022”变更为“001872/201872”。
    变更后的证券简称和证券代码启用日期：2018 年 12 月 26 日
    公司依照《深圳证券交易所股票上市规则》办理。
    """


def test_verified_text_produces_strict_code_and_name_event_versions() -> None:
    result = derive_security_identity_lifecycle_event_candidates(
        documents=[_document(_code_and_name_text())]
    )

    assert result["blockers"] == []
    assert result["semantic_derivation_complete"] is True
    assert result["data_admission_eligible"] is False
    assert len(result["derivation_implementation_root"]) == 64
    assert result["current_security_master_consulted"] is False
    assert [event["event_type"] for event in result["events"]] == [
        "security_code_change",
        "security_name_change",
    ]
    code, name = result["events"]
    expected_keys = {
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
    assert set(code) == expected_keys
    assert code["security_id"] == "issuer-cm-port"
    assert code["known_at"] == "20181226"
    assert code["known_timing"] == "after_close"
    assert code["effective_at"] == "20181226"
    assert code["effective_timing"] == "before_open"
    assert code["payload"] == {
        "old_security_code": "000022.SZ",
        "new_security_code": "001872.SZ",
    }
    assert name["payload"] == {
        "old_security_name": "深赤湾A",
        "new_security_name": "招商港口",
    }
    assert all(event["pit_evidence_eligible"] is False for event in result["events"])
    assert result["governance_blockers"] == [
        {
            "announcement_id": "1205690369",
            "code": "identity_event_independent_admission_required",
        }
    ]
    assert result["provenance"][0]["announcement_id"] == "1205690369"
    assert result["provenance"][0]["source_document_sha256"] == "a" * 64
    assert result["provenance"][0]["text_extractor_implementation_root"] == (
        "d" * 64
    )
    assert result["provenance"][0]["derivation_implementation_root"] == (
        result["derivation_implementation_root"]
    )


def test_event_candidates_feed_timeline_without_backfilling_announcement_day() -> None:
    candidates = derive_security_identity_lifecycle_event_candidates(
        documents=[_document(_code_and_name_text())]
    )
    timeline = derive_security_identity_lifecycle_timeline(
        security_ids=["issuer-cm-port"],
        trade_dates=["20181226", "20181227", "20181228"],
        pre_span_seeds=[
            {
                "seed_version_id": "cm-port-seed-v1",
                "security_id": "issuer-cm-port",
                "as_of_date": "20181225",
                "security_code": "000022.SZ",
                "security_name": "深赤湾A",
                "lifecycle_state": "listed",
                "list_date": "19930505",
                "delist_date": None,
                "stable_identity_evidence_hash": "b" * 64,
                "source_evidence_hash": "c" * 64,
                "pit_evidence_eligible": True,
            }
        ],
        event_versions=[
            event | {"pit_evidence_eligible": True}
            for event in candidates["events"]
        ],
    )

    assert [row["security_code"] for row in timeline["rows"]] == [
        "000022.SZ",
        "001872.SZ",
        "001872.SZ",
    ]
    assert [row["security_name"] for row in timeline["rows"]] == [
        "深赤湾A",
        "招商港口",
        "招商港口",
    ]


def test_delisting_requires_explicit_effective_date_in_document_text() -> None:
    text = """
    证券代码：600680 900930
    上海普天邮通科技股份有限公司关于股票终止上市的公告
    上海证券交易所决定对公司 A 股和 B 股股票予以终止上市。
    公司股票于 5 月 24 日终止上市。
    """
    result = derive_security_identity_lifecycle_event_candidates(
        documents=[
            _document(
                text,
                announcement_id="1206282885",
                announcement_date="20190518",
                known_at="20190518",
                security_id="issuer-shanghai-potevio",
            )
        ]
    )

    assert result["blockers"] == []
    assert len(result["events"]) == 1
    assert result["events"][0]["event_type"] == "delisting"
    assert result["events"][0]["effective_at"] == "20190524"
    assert result["events"][0]["effective_timing"] == "before_open"
    assert result["events"][0]["payload"] == {}
    assert result["provenance"][0]["parse_rule"] == (
        "explicit_delisting_month_day_bound_to_announcement_year_v1"
    )


def test_initial_listing_is_supported_only_with_an_explicit_date() -> None:
    text = """
    某公司首次公开发行股票上市公告书
    股票上市交易日期：2020 年 1 月 6 日
    公司股票将在深圳证券交易所上市交易。
    """
    result = derive_security_identity_lifecycle_event_candidates(
        documents=[
            _document(
                text,
                announcement_id="listing-1",
                announcement_date="20200103",
                known_at="20200103",
                security_id="issuer-new-listing",
            )
        ]
    )

    assert result["blockers"] == []
    assert result["events"][0]["event_type"] == "listing"
    assert result["events"][0]["effective_at"] == "20200106"


def test_missing_stable_identity_blocks_instead_of_using_a_current_code() -> None:
    document = _document(_code_and_name_text())
    document["security_id"] = ""

    result = derive_security_identity_lifecycle_event_candidates(
        documents=[document]
    )

    assert result["events"] == []
    assert result["blockers"] == [
        {
            "announcement_id": "1205690369",
            "code": "identity_event_stable_security_id_missing",
        }
    ]
    assert result["current_security_master_consulted"] is False


def test_unverified_text_or_hash_mismatch_blocks_before_semantic_parsing() -> None:
    unverified = _document(
        _code_and_name_text(), document_text_verified=False
    )
    mismatched = _document(
        _code_and_name_text(),
        announcement_id="other",
        source_text_sha256="f" * 64,
    )

    result = derive_security_identity_lifecycle_event_candidates(
        documents=[unverified, mismatched]
    )

    assert result["events"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "identity_event_document_text_unverified",
        "identity_event_source_text_hash_mismatch",
    }


def test_known_date_cannot_precede_signed_announcement_date() -> None:
    result = derive_security_identity_lifecycle_event_candidates(
        documents=[
            _document(
                _code_and_name_text(),
                announcement_date="20181226",
                known_at="20181225",
            )
        ]
    )

    assert result["events"] == []
    assert result["blockers"] == [
        {
            "announcement_id": "1205690369",
            "code": "identity_event_known_before_announcement_invalid",
        }
    ]


def test_pause_and_trading_suspension_are_explicitly_routed_out() -> None:
    pause = """
    上海普天邮通科技股份有限公司股票暂停上市公告
    暂停上市起始日：2018 年 5 月 29 日
    上海证券交易所决定暂停公司股票上市。
    """
    trading_halt = """
    上海普天邮通科技股份有限公司关于公司股票停牌的公告
    公司股票自 2018 年 5 月 2 日起停牌。
    """

    result = derive_security_identity_lifecycle_event_candidates(
        documents=[
            _document(pause, announcement_id="1204983113"),
            _document(trading_halt, announcement_id="1204831387"),
        ]
    )

    assert result["events"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "identity_event_listing_suspension_requires_control_state",
        "identity_event_trading_halt_requires_control_state",
    }


def test_parsed_but_unadmitted_source_retains_candidates_and_blocker() -> None:
    result = derive_security_identity_lifecycle_event_candidates(
        documents=[
            _document(
                _code_and_name_text(),
                source_governed_evidence_eligible=False,
            )
        ]
    )

    assert len(result["events"]) == 2
    assert all(
        event["pit_evidence_eligible"] is False for event in result["events"]
    )
    assert result["blockers"] == []
    assert {row["code"] for row in result["governance_blockers"]} == {
        "identity_event_independent_admission_required",
        "identity_event_source_not_governed",
    }
    assert result["semantic_derivation_complete"] is True


def test_event_candidate_identity_is_independent_of_document_order() -> None:
    code = _document(_code_and_name_text())
    delisting_text = """
    关于股票终止上市的公告
    上海证券交易所决定公司股票于 2019 年 5 月 24 日终止上市。
    """
    delisting = _document(
        delisting_text,
        announcement_id="1206282885",
        announcement_date="20190518",
        known_at="20190518",
        security_id="issuer-shanghai-potevio",
    )

    forward = derive_security_identity_lifecycle_event_candidates(
        documents=[code, delisting]
    )
    reverse = derive_security_identity_lifecycle_event_candidates(
        documents=[delisting, code]
    )

    assert forward["events"] == reverse["events"]
    assert forward["provenance"] == reverse["provenance"]
    assert forward["content_hash"] == reverse["content_hash"]


def test_duplicate_documents_and_invalid_revision_metadata_fail_closed() -> None:
    duplicate = _document(_code_and_name_text())
    invalid_revision = _document(
        _code_and_name_text(),
        announcement_id="revision-invalid",
        version_number_by_event_type={"security_code_change": 0},
    )

    result = derive_security_identity_lifecycle_event_candidates(
        documents=[duplicate, duplicate, invalid_revision]
    )

    assert result["events"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "identity_event_duplicate_announcement_id",
        "identity_event_revision_metadata_invalid",
    }
