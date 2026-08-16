from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import run_provider_probe
from auto_alpha.data.ingestion.pipeline.ashare.run_provider_probe import (
    ArchivedProbeReplayTransport,
    BaostockProbeTransport,
)
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import (
    ProviderProbeContract,
    ProviderProbeObservation,
    ProviderProbeRequest,
    run_provider_capability_probe,
    validate_provider_capability_probe,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, sha256_file


SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "bulk_backfill_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "live_trading_authorized",
)


class FakeProbeTransport:
    def __init__(self, observations: dict[str, ProviderProbeObservation]) -> None:
        self._observations = observations
        self.calls: list[tuple[str, float]] = []

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        self.calls.append((request.request_id, timeout_seconds))
        return self._observations[request.request_id]


def _contract(
    output_root: Path,
    *,
    allowed_hosts: tuple[str, ...] = ("probe.example.test",),
    max_requests: int = 3,
) -> ProviderProbeContract:
    return ProviderProbeContract(
        probe_id="free_domestic_sources_2012_2019_v1",
        output_root=str(output_root),
        allowed_hosts=allowed_hosts,
        max_requests=max_requests,
        timeout_seconds=3.0,
    )


def _request(
    request_id: str,
    *,
    host: str = "probe.example.test",
    disposition: str = "provider_cannot_prove",
) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id=request_id,
        provider="offline_fixture",
        method="GET",
        url=f"https://{host}/bounded/{request_id}",
        disposition=disposition,
        evidence_semantics="raw_transport_payload",
        expected_terminal_states=("positive", "empty", "error"),
    )


def _successful_transport(*request_ids: str) -> FakeProbeTransport:
    return FakeProbeTransport(
        {
            request_id: ProviderProbeObservation(
                terminal_state="positive",
                raw_payload=json.dumps(
                    {"request_id": request_id, "rows": [{"value": 1}]},
                    sort_keys=True,
                ).encode(),
                row_count=1,
                status_code=200,
            )
            for request_id in request_ids
        }
    )


@pytest.mark.parametrize(
    ("contract", "requests", "error_pattern"),
    [
        (
            lambda root: _contract(root),
            lambda: [_request("outside-host", host="not-allowed.example.test")],
            "host_not_allowed|host.*allow",
        ),
        (
            lambda root: _contract(root, max_requests=1),
            lambda: [_request("first"), _request("second")],
            "budget|request_limit|max_requests",
        ),
    ],
)
def test_probe_contract_rejects_host_and_budget_violations_before_transport_or_write(
    tmp_path: Path,
    contract,
    requests,
    error_pattern: str,
) -> None:
    output_root = tmp_path / "provider_probe"
    transport = _successful_transport("outside-host", "first", "second")

    with pytest.raises(ValueError, match=error_pattern):
        run_provider_capability_probe(
            contract(output_root),
            requests(),
            transport=transport,
        )

    assert transport.calls == []
    assert not output_root.exists()


def test_probe_archives_positive_empty_and_error_as_terminal_evidence(tmp_path: Path) -> None:
    observations = {
        "positive": ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=b'{"rows":[{"code":"600000"}]}',
            row_count=1,
            status_code=200,
        ),
        "empty": ProviderProbeObservation(
            terminal_state="empty",
            raw_payload=b'{"rows":[]}',
            row_count=0,
            status_code=200,
        ),
        "error": ProviderProbeObservation(
            terminal_state="error",
            raw_payload=b'{"code":"upstream_timeout"}',
            row_count=None,
            status_code=503,
            error_code="upstream_timeout",
        ),
    }
    transport = FakeProbeTransport(observations)

    manifest = run_provider_capability_probe(
        _contract(tmp_path / "provider_probe"),
        [_request(state) for state in observations],
        transport=transport,
    )
    validated = validate_provider_capability_probe(manifest["manifest_path"])

    assert [request_id for request_id, _timeout in transport.calls] == list(observations)
    assert manifest["mode"] == "bounded_provider_probe"
    assert validated["generation_id"] == manifest["generation_id"]
    assert manifest["request_count"] == 3
    assert manifest["terminal_counts"] == {"empty": 1, "error": 1, "positive": 1}

    manifest_path = Path(manifest["manifest_path"])
    raw_evidence = manifest["raw_evidence"]
    raw_path = manifest_path.parent / raw_evidence["path"]
    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    assert raw_evidence["record_count"] == len(records) == 3
    assert raw_evidence["sha256"] == sha256_file(raw_path)
    assert [record["terminal_state"] for record in records] == [
        "positive",
        "empty",
        "error",
    ]
    assert {record["disposition"] for record in records} == {
        "provider_cannot_prove"
    }
    assert {record["evidence_semantics"] for record in records} == {
        "raw_transport_payload"
    }
    assert base64.b64decode(records[0]["raw_payload_base64"]) == observations[
        "positive"
    ].raw_payload
    for record in records:
        assert record["raw_payload_sha256"] == _sha256_bytes(
            base64.b64decode(record["raw_payload_base64"])
        )


def test_probe_validation_detects_raw_payload_tampering(tmp_path: Path) -> None:
    manifest = run_provider_capability_probe(
        _contract(tmp_path / "provider_probe", max_requests=1),
        [_request("one")],
        transport=_successful_transport("one"),
    )
    raw_path = Path(manifest["manifest_path"]).parent / manifest["raw_evidence"]["path"]
    raw_path.chmod(0o640)
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered\n")

    with pytest.raises(ValueError, match="raw|evidence|hash|record_count"):
        validate_provider_capability_probe(manifest["manifest_path"])


def test_probe_safety_flags_are_fixed_false_and_content_valid_forgery_is_rejected(
    tmp_path: Path,
) -> None:
    manifest = run_provider_capability_probe(
        _contract(tmp_path / "provider_probe", max_requests=1),
        [_request("one")],
        transport=_successful_transport("one"),
    )
    assert set(manifest["safety"]) == set(SAFETY_FLAGS)
    assert all(manifest["safety"][flag] is False for flag in SAFETY_FLAGS)

    source_manifest_path = Path(manifest["manifest_path"])
    forged_payload = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    forged_payload["safety"]["data_admission_eligible"] = True
    semantic = {
        key: value
        for key, value in forged_payload.items()
        if key not in {"content_hash", "generation_id"}
    }
    forged_hash = canonical_hash(semantic)
    original_suffix = str(forged_payload["content_hash"])[:24]
    prefix = str(forged_payload["generation_id"]).removesuffix(original_suffix).rstrip("_")
    forged_generation_id = f"{prefix}_{forged_hash[:24]}"
    forged_payload["content_hash"] = forged_hash
    forged_payload["generation_id"] = forged_generation_id

    forged_generation = tmp_path / "forged" / "generations" / forged_generation_id
    forged_generation.mkdir(parents=True)
    shutil.copy2(
        source_manifest_path.parent / manifest["raw_evidence"]["path"],
        forged_generation / manifest["raw_evidence"]["path"],
    )
    forged_manifest_path = forged_generation / source_manifest_path.name
    forged_manifest_path.write_text(
        json.dumps(forged_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="safety|admission|authorization|non_admissible"):
        validate_provider_capability_probe(forged_manifest_path)


def test_probe_writes_only_its_dedicated_output_root(tmp_path: Path) -> None:
    governed_roots = [
        tmp_path / "canonical",
        tmp_path / "source_freeze",
        tmp_path / "research",
    ]
    for root in governed_roots:
        root.mkdir()
        (root / "sentinel").write_text(root.name, encoding="utf-8")
    before = {root: _tree_bytes(root) for root in governed_roots}

    output_root = tmp_path / "provider_probes" / "bounded"
    manifest = run_provider_capability_probe(
        _contract(output_root, max_requests=1),
        [_request("one")],
        transport=_successful_transport("one"),
    )

    assert Path(manifest["manifest_path"]).is_relative_to(output_root)
    assert {root: _tree_bytes(root) for root in governed_roots} == before


def test_identical_probe_plan_reuses_verified_generation_without_transport(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path / "provider_probe", max_requests=1)
    requests = [_request("one")]
    first_transport = _successful_transport("one")
    first = run_provider_capability_probe(
        contract,
        requests,
        transport=first_transport,
    )

    def forbidden_transport(
        _request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        raise AssertionError("a verified cache hit must not call transport")

    second = run_provider_capability_probe(
        contract,
        requests,
        transport=forbidden_transport,
    )

    assert len(first_transport.calls) == 1
    assert second["cache_hit"] is True
    assert second["generation_id"] == first["generation_id"]


def test_corrupt_current_generation_blocks_before_transport(tmp_path: Path) -> None:
    contract = _contract(tmp_path / "provider_probe", max_requests=1)
    requests = [_request("one")]
    first = run_provider_capability_probe(
        contract,
        requests,
        transport=_successful_transport("one"),
    )
    raw_path = Path(first["manifest_path"]).parent / first["raw_evidence"]["path"]
    raw_path.chmod(0o640)
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered\n")
    calls: list[str] = []

    def tracking_transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        calls.append(request.request_id)
        return _successful_transport(request.request_id)._observations[request.request_id]

    with pytest.raises(ValueError, match="existing_evidence_invalid"):
        run_provider_capability_probe(
            contract,
            requests,
            transport=tracking_transport,
        )

    assert calls == []


def test_provider_probe_cli_requires_explicit_network_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "provider_probe"

    result = run_provider_probe.main(
        ["--provider", "cninfo", "--output-root", str(output)]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "provider_probe_network_authority_missing"
    assert not output.exists()


def test_locked_probe_plan_preview_is_finite_and_non_admissible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run_provider_probe.main(
        ["--provider", "all", "--plan-only", "--pretty"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["mode"] == "bounded_provider_probe"
    assert payload["data_admission_eligible"] is False
    assert payload["request_count"] == 111
    assert payload["request_plan_hash"] == (
        "9ab5fe199ba4ac036f6413b99ad45287858cd1c178102b57dcd104eb878b1328"
    )
    assert all(
        request.required_checks
        for request in run_provider_probe.build_locked_probe_requests("all")
    )


def test_archived_replay_cannot_upgrade_handoff_disposition(tmp_path: Path) -> None:
    source_request = _request("one", disposition="provider_cannot_prove")
    source = run_provider_capability_probe(
        _contract(tmp_path / "source", max_requests=1),
        [source_request],
        transport=_successful_transport("one"),
    )
    replay = ArchivedProbeReplayTransport(
        source["manifest_path"], require_current_implementation=False
    )
    upgraded = _request("one", disposition="bounded_backfill")

    with pytest.raises(ValueError, match="disposition_upgrade_or_change"):
        replay(upgraded, 3.0)


def test_archived_replay_cannot_remove_required_checks(tmp_path: Path) -> None:
    source_request = ProviderProbeRequest(
        request_id="one",
        provider="offline_fixture",
        endpoint="locked_endpoint",
        method="GET",
        url="https://probe.example.test/bounded/one",
        disposition="provider_cannot_prove",
        required_checks=("locked_check",),
    )
    source = run_provider_capability_probe(
        _contract(tmp_path / "source", max_requests=1),
        [source_request],
        transport=FakeProbeTransport(
            {
                "one": ProviderProbeObservation(
                    terminal_state="positive",
                    raw_payload=b'{"rows":[1]}',
                    row_count=1,
                    checks={"locked_check": True},
                )
            }
        ),
    )
    replay = ArchivedProbeReplayTransport(
        source["manifest_path"], require_current_implementation=False
    )
    weakened = ProviderProbeRequest(
        request_id="one",
        provider="offline_fixture",
        endpoint="locked_endpoint",
        method="GET",
        url="https://probe.example.test/bounded/one",
        disposition="provider_cannot_prove",
    )

    with pytest.raises(ValueError, match="required_checks_changed"):
        replay(weakened, 3.0)


def test_archived_waf_replay_only_allows_conservative_cannot_prove(
    tmp_path: Path,
) -> None:
    body = "<html>访问被阻断</html>".encode()
    raw = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "status_code": 403,
            "body_base64": base64.b64encode(body).decode("ascii"),
        },
        sort_keys=True,
    ).encode()
    source_request = ProviderProbeRequest(
        request_id="detail",
        provider="csindex",
        endpoint="announcement_detail",
        method="GET",
        url="https://probe.example.test/detail?id=1",
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        metadata={"case": "csindex_detail", "tokens": ["sample"]},
    )
    source = run_provider_capability_probe(
        _contract(tmp_path / "source", max_requests=1),
        [source_request],
        transport=FakeProbeTransport(
            {
                "detail": ProviderProbeObservation(
                    terminal_state="error",
                    raw_payload=raw,
                    row_count=None,
                    status_code=403,
                    error_code="http_status:403",
                    checks={"http_success": False},
                )
            }
        ),
    )
    replay = ArchivedProbeReplayTransport(
        source["manifest_path"], require_current_implementation=False
    )
    downgraded = ProviderProbeRequest(
        request_id="detail",
        provider="csindex",
        endpoint="announcement_detail",
        method="GET",
        url="https://probe.example.test/detail?id=1",
        disposition="provider_cannot_prove",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive", "error"),
        metadata={
            "case": "csindex_detail",
            "tokens": ["sample"],
            "accepted_terminal_waf": True,
        },
    )

    observation = replay(downgraded, 3.0)

    assert observation.terminal_state == "error"
    assert observation.checks == {
        "waf_terminal_response_archived": True,
        "no_retry_or_coverage_claim": True,
        "detail_or_waf_evidence_captured": True,
    }


def test_interrupted_probe_restores_state_without_repeating_completed_request(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path / "provider_probe", max_requests=2)
    requests = [_request("page_one"), _request("page_two")]
    first_calls: list[str] = []

    def interrupted(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        first_calls.append(request.request_id)
        if request.request_id == "page_two":
            raise KeyboardInterrupt("simulated process interruption")
        return ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=b'{"page":1}',
            row_count=1,
            status_code=200,
            diagnostics={"state_seed": "page_one"},
        )

    with pytest.raises(KeyboardInterrupt, match="simulated process interruption"):
        run_provider_capability_probe(contract, requests, transport=interrupted)

    class ResumedTransport:
        def __init__(self) -> None:
            self.restored: list[str] = []
            self.calls: list[str] = []

        def restore(
            self,
            request: ProviderProbeRequest,
            record: dict[str, object],
        ) -> None:
            self.restored.append(request.request_id)
            assert record["diagnostics"] == {"state_seed": "page_one"}

        def __call__(
            self,
            request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            self.calls.append(request.request_id)
            assert self.restored == ["page_one"]
            return ProviderProbeObservation(
                terminal_state="positive",
                raw_payload=b'{"page":2}',
                row_count=1,
                status_code=200,
                checks={"state_restored": True},
            )

    resumed = ResumedTransport()
    result = run_provider_capability_probe(contract, requests, transport=resumed)

    assert first_calls == ["page_one", "page_two"]
    assert resumed.restored == ["page_one"]
    assert resumed.calls == ["page_two"]
    assert result["status"] == "succeeded"
    validate_provider_capability_probe(result["manifest_path"])


def test_blocked_matching_generation_is_not_cached(tmp_path: Path) -> None:
    contract = _contract(tmp_path / "provider_probe", max_requests=1)
    request = _request("one")
    first = run_provider_capability_probe(
        contract,
        [request],
        transport=FakeProbeTransport(
            {
                "one": ProviderProbeObservation(
                    terminal_state="positive",
                    raw_payload=b'{"rows":[1]}',
                    row_count=1,
                    checks={"provider_semantics": False},
                )
            }
        ),
    )
    retry_transport = _successful_transport("one")

    second = run_provider_capability_probe(
        contract,
        [request],
        transport=retry_transport,
    )

    assert first["status"] == "blocked"
    assert retry_transport.calls == [("one", 3.0)]
    assert second["status"] == "succeeded"
    assert second["generation_id"] != first["generation_id"]


def test_probe_output_symlink_is_rejected_before_transport(tmp_path: Path) -> None:
    governed = tmp_path / "canonical"
    governed.mkdir()
    output_alias = tmp_path / "provider_probe_alias"
    output_alias.symlink_to(governed, target_is_directory=True)
    transport = _successful_transport("one")

    with pytest.raises(ValueError, match="output_root_symlink_forbidden"):
        run_provider_capability_probe(
            _contract(output_alias, max_requests=1),
            [_request("one")],
            transport=transport,
        )

    assert transport.calls == []
    assert not tuple(governed.iterdir())


def test_baostock_parser_stops_at_locked_page_budget() -> None:
    class EndlessResult:
        def __init__(self) -> None:
            self.data = [["value"]] * 2000
            self.cur_page_num = 1
            self.per_page_count = 2000
            self.error_code = "0"
            self.cur_row_num = 0
            self.next_calls = 0

        def next(self) -> bool:
            self.next_calls += 1
            self.cur_page_num += 1
            self.data = [["value"]] * 2000
            return True

    result = EndlessResult()

    with pytest.raises(ValueError, match="page_budget_exceeded"):
        BaostockProbeTransport()._collect(result)

    assert result.next_calls == 3


def test_probe_activity_wire_budget_is_enforced_and_recovery_does_not_retry(
    tmp_path: Path,
) -> None:
    contract = ProviderProbeContract(
        probe_id="wire_budget",
        output_root=tmp_path / "provider_probe",
        allowed_hosts=("probe.example.test",),
        max_requests=1,
        timeout_seconds=3.0,
        max_wire_exchanges=1,
    )
    calls: list[str] = []

    def over_budget(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        calls.append(request.request_id)
        return ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=b'{"rows":[1]}',
            row_count=1,
            transport_exchange_count=2,
        )

    with pytest.raises(ValueError, match="wire_exchange_budget_exceeded"):
        run_provider_capability_probe(
            contract,
            [_request("one")],
            transport=over_budget,
        )
    with pytest.raises(ValueError, match="wire_exchange_budget_exceeded"):
        run_provider_capability_probe(
            contract,
            [_request("one")],
            transport=over_budget,
        )

    assert calls == ["one"]


def _tree_bytes(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
