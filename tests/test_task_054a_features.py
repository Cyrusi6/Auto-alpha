from __future__ import annotations

from types import SimpleNamespace

import torch

from feature_factory.catalog import FEATURE_SET_V2, build_feature_set_manifest
from feature_factory.contracts import (
    build_feature_contract,
    build_tensor_content_fingerprint,
    intersect_candidate_feature_blockers,
)
from feature_factory.models import FeatureDefinition, FeatureSetManifest
from feature_factory.validity import build_feature_values_and_validity
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB


def _manifest(*definitions: FeatureDefinition) -> FeatureSetManifest:
    return FeatureSetManifest(
        feature_set_name="task054_test",
        feature_set_version="1",
        feature_version="task054_test",
        operator_version="ashare_ops_v1",
        feature_count=len(definitions),
        feature_definitions=[item.to_dict() for item in definitions],
        data_freeze_id=None,
        data_freeze_hash=None,
        point_in_time=True,
        corporate_action_aware=True,
        target_return_mode="adjusted_open_t1_t2",
        created_at="2026-07-14T00:00:00Z",
        content_hash="task054-test-manifest",
    )


def _definition(name: str, fields: list[str], *, lookback: int = 1, version: str = "task054_test") -> FeatureDefinition:
    contract = build_feature_contract(
        name,
        fields,
        lookback=lookback,
        transform="identity",
        feature_version=version,
    )
    return FeatureDefinition(
        feature_name=name,
        feature_version=version,
        family="test",
        source_fields=fields,
        tensor_key=name.lower(),
        transform="identity",
        lookback=lookback,
        dependency_graph=contract.to_dict(),
        effective_lookback=contract.effective_lookback,
        price_basis=contract.price_basis,
        pit_availability=contract.pit_availability,
        validity_rule=contract.validity_rule,
    )


def test_catalog_records_explicit_price_and_validity_contracts():
    manifest = build_feature_set_manifest(FEATURE_SET_V2, created_at="2026-07-14T00:00:00Z")
    rows = {row["feature_name"]: row for row in manifest.feature_definitions}

    assert rows["RET_5D"]["price_basis"] == "adjusted_close"
    assert rows["RET_5D"]["effective_lookback"] == 6
    assert rows["RET_5D"]["dependency_graph"]["dependencies"][0]["offsets"] == [0, -5]
    assert rows["VOLATILITY_5D"]["effective_lookback"] == 6
    assert rows["INTRADAY_RETURN"]["price_basis"] == "raw_intraday_ohlc"
    assert rows["GAP_RETURN"]["price_basis"] == "raw_gap_ohlc"


def test_adjusted_returns_ignore_corporate_action_jump_but_intraday_stays_raw():
    close = torch.tensor([[100.0, 50.0, 51.0]])
    open_price = torch.tensor([[99.0, 50.0, 50.0]])
    adjustment = torch.tensor([[1.0, 2.0, 2.0]])
    adjusted_close = close * adjustment
    validity = torch.ones_like(close, dtype=torch.bool)
    loader = SimpleNamespace(
        raw_data_cache={
            "close": close,
            "open": open_price,
            "adjusted_close": adjusted_close,
            "ret_1d": torch.full_like(close, 99.0),
            "adj_factor": adjustment,
            "signal_eligible_at_close": validity,
        },
        raw_validity_cache={"close": validity, "open": validity, "adj_factor": validity},
    )
    values, masks, summaries = build_feature_values_and_validity(
        loader,
        _manifest(
            _definition("RET_1D", ["adjusted_close"], lookback=2),
            _definition("INTRADAY_RETURN", ["open", "close"]),
        ),
    )

    assert masks[0, 0].tolist() == [False, True, True]
    assert torch.isclose(values[0, 0, 1], torch.tensor(0.0), atol=1e-7)
    assert torch.isclose(values[0, 0, 2], torch.log(torch.tensor(1.02)), atol=1e-6)
    assert torch.isclose(values[0, 1, 1], torch.tensor(0.0), atol=1e-7)
    assert summaries[0]["price_basis"] == "adjusted_close"
    assert summaries[1]["price_basis"] == "raw_intraday_ohlc"


def test_return_and_volatility_require_all_price_endpoints():
    close = torch.tensor([[10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]])
    valid = torch.ones_like(close, dtype=torch.bool)
    loader = SimpleNamespace(
        raw_data_cache={"close": close, "adjusted_close": close, "signal_eligible_at_close": valid},
        raw_validity_cache={"adjusted_close": valid},
    )
    _, masks, _ = build_feature_values_and_validity(
        loader,
        _manifest(
            _definition("RET_5D", ["adjusted_close"], lookback=6),
            _definition("VOLATILITY_5D", ["adjusted_close"], lookback=6),
        ),
    )

    assert masks[0, 0].tolist() == [False, False, False, False, False, True, True]
    assert masks[0, 1].tolist() == [False, False, False, False, False, True, True]

    loader.raw_validity_cache["adjusted_close"][0, 2] = False
    values, masks, _ = build_feature_values_and_validity(loader, _manifest(_definition("VOLATILITY_5D", ["adjusted_close"], lookback=6)))
    assert masks[0, 0, 5].item() is False
    assert values[0, 0, 5].item() == 0.0

    loader.raw_validity_cache["adjusted_close"].fill_(True)
    loader.raw_data_cache["adjusted_close"][0, 1] = 0.0
    values, masks, _ = build_feature_values_and_validity(loader, _manifest(_definition("RET_5D", ["adjusted_close"], lookback=6)))
    assert masks[0, 0, 6].item() is False
    assert values[0, 0, 6].item() == 0.0


def test_expanded_feature_without_explicit_dependency_validity_fails_closed():
    eligible = torch.ones((2, 3), dtype=torch.bool)
    loader = SimpleNamespace(
        raw_data_cache={
            "close": torch.ones((2, 3)),
            "moneyflow_net_ratio": torch.ones((2, 3)),
            "signal_eligible_at_close": eligible,
        },
        raw_validity_cache={"close": eligible},
    )
    definition = _definition(
        "MONEYFLOW_NET_RATIO",
        ["moneyflow"],
        version="ashare_features_v3",
    )
    values, validity, summaries = build_feature_values_and_validity(loader, _manifest(definition))

    assert not validity.any()
    assert not values.any()
    assert summaries[0]["blocker"] == "missing_validity_dependency"
    assert summaries[0]["missing_validity_dependencies"] == ["moneyflow_net_ratio"]


def test_candidate_dependencies_intersect_feature_blockers():
    result = intersect_candidate_feature_blockers(
        [
            {"factor_id": "safe", "formula_names": ["RET_1D", "CS_RANK"]},
            {"factor_id": "blocked", "formula_names": ["RET_1D", "MONEYFLOW_NET_RATIO", "ADD"]},
        ],
        [
            {"feature_name": "RET_1D", "blocker": None},
            {"feature_name": "MONEYFLOW_NET_RATIO", "blocker": "missing_validity_dependency"},
        ],
    )

    assert "safe" not in result
    assert result["blocked"] == [
        {"feature_name": "MONEYFLOW_NET_RATIO", "reason_code": "missing_validity_dependency"}
    ]


def test_vm_masks_invalid_feature_values_before_cross_sectional_rank():
    vm = StackVM()
    values = torch.zeros((3, FORMULA_VOCAB.feature_count, 1))
    validity = torch.zeros_like(values, dtype=torch.bool)
    feature = FORMULA_VOCAB.encode_name("RET_1D")
    values[:, feature, 0] = torch.tensor([1.0, 2.0, 1_000_000.0])
    validity[:, feature, 0] = torch.tensor([True, True, False])

    result = vm.execute_with_validity([feature, FORMULA_VOCAB.encode_name("CS_RANK")], values, validity)

    assert result is not None
    output, output_validity = result
    assert output[:, 0].tolist() == [0.0, 1.0, 0.0]
    assert output_validity[:, 0].tolist() == [True, True, False]


def test_tensor_content_fingerprint_binds_actual_values_validity_and_semantics():
    common = dict(
        matrix_sha256="matrix",
        freeze_sha256="freeze",
        universe_sha256="universe",
        feature_manifest_sha256="manifest",
        stock_axis_hash="stocks",
        date_axis_hash="dates",
        feature_axis_hash="features",
        target_contract_hash="target",
        time_contract_hash="time",
        semantic_source_hash="code-a",
    )
    baseline = build_tensor_content_fingerprint(values_sha256="values-a", validity_sha256="valid-a", **common)
    changed_values = build_tensor_content_fingerprint(values_sha256="values-b", validity_sha256="valid-a", **common)
    changed_code = build_tensor_content_fingerprint(
        values_sha256="values-a",
        validity_sha256="valid-a",
        **{**common, "semantic_source_hash": "code-b"},
    )

    assert baseline != changed_values
    assert baseline != changed_code
