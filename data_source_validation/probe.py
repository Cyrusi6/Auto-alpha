"""Provider readiness probes for local and Tushare-backed data sources."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import (
    TushareApiError,
    TushareHttpClient,
    TushareNetworkError,
    TusharePermissionError,
    TushareRateLimitError,
    TushareSchemaError,
)

from .contracts import DATASET_CONTRACTS, contracts_for_datasets
from .fake_tushare import FakeTushareHttpClient
from .models import ApiProbeResult, ProviderDiagnosticCode, ProviderReadinessStatus


def probe_provider(
    config: AShareDataConfig,
    allow_network: bool = False,
    fake_scenario: str | None = None,
    max_requests: int = 20,
    datasets: Iterable[str] | None = None,
) -> list[ApiProbeResult]:
    """Probe provider readiness without leaking credentials."""

    if config.provider == "tushare" and fake_scenario is None and allow_network:
        return [
            ApiProbeResult(
                dataset="provider",
                api_name="tushare",
                status=ProviderReadinessStatus.ERROR,
                diagnostic_code=ProviderDiagnosticCode.network_disabled,
                message="superseded_by_task055j",
                requested_fields=[],
                response_fields=[],
                records=0,
                credential_present=False,
                credential_source_type="not_read",
                network_allowed=False,
            )
        ]

    selected_contracts = list(contracts_for_datasets(list(datasets) if datasets is not None else None).values())
    if config.provider == "sample":
        return [
            ApiProbeResult(
                dataset="sample",
                api_name="local_sample",
                status=ProviderReadinessStatus.OK,
                diagnostic_code=None,
                message="sample provider is local and offline",
                requested_fields=[],
                response_fields=[],
                records=0,
                credential_present=False,
                network_allowed=False,
            )
        ]

    if config.provider != "tushare":
        return [
            ApiProbeResult(
                dataset="provider",
                api_name=config.provider,
                status=ProviderReadinessStatus.WARNING,
                diagnostic_code=ProviderDiagnosticCode.unexpected_exception,
                message=f"unsupported provider for smoke probe: {config.provider}",
                requested_fields=[],
                response_fields=[],
                records=0,
                credential_present=bool(config.tushare_token),
                credential_source_type="environment_or_credential_file" if config.tushare_token else "none",
                network_allowed=allow_network,
            )
        ]

    token_present = bool(config.tushare_token)
    if fake_scenario is None and not allow_network:
        return [
            ApiProbeResult(
                dataset="provider",
                api_name="tushare",
                status=ProviderReadinessStatus.SKIPPED,
                diagnostic_code=ProviderDiagnosticCode.network_disabled,
                message="network probe skipped; pass --allow-network to probe real Tushare",
                requested_fields=[],
                response_fields=[],
                records=0,
                credential_present=token_present,
                credential_source_type="environment_or_credential_file" if token_present else "none",
                network_allowed=False,
            )
        ]

    if fake_scenario is None and allow_network and not token_present:
        return [
            ApiProbeResult(
                dataset="provider",
                api_name="tushare",
                status=ProviderReadinessStatus.ERROR,
                diagnostic_code=ProviderDiagnosticCode.missing_token,
                message="TUSHARE_TOKEN is required for online Tushare smoke",
                requested_fields=[],
                response_fields=[],
                records=0,
                credential_present=False,
                network_allowed=True,
            )
        ]

    client = FakeTushareHttpClient(fake_scenario) if fake_scenario else TushareHttpClient(config)
    probe_config = replace(config, tushare_token=config.tushare_token or "fake-token-redacted")
    results: list[ApiProbeResult] = []
    for contract in selected_contracts[: max(0, max_requests)]:
        params = _probe_params(probe_config, contract.dataset)
        requested_fields = list(contract.request_fields)
        try:
            envelope = client.post_with_metadata(
                contract.api_name,
                params=params,
                fields=",".join(requested_fields),
            )
            missing = sorted(set(requested_fields) - set(envelope.response_fields))
            if missing:
                status = ProviderReadinessStatus.WARNING
                code = ProviderDiagnosticCode.missing_fields
                message = f"missing response fields: {', '.join(missing)}"
            elif envelope.item_count == 0:
                status = ProviderReadinessStatus.WARNING
                code = ProviderDiagnosticCode.empty_response
                message = "Tushare returned no rows for the probe request"
            else:
                status = ProviderReadinessStatus.OK
                code = None
                message = "probe succeeded"
            results.append(
                ApiProbeResult(
                    dataset=contract.dataset,
                    api_name=contract.api_name,
                    status=status,
                    diagnostic_code=code,
                    message=message,
                    requested_fields=requested_fields,
                    response_fields=list(envelope.response_fields),
                    missing_fields=missing,
                    records=envelope.item_count,
                    credential_present=bool(probe_config.tushare_token),
                    credential_source_type="synthetic_fixture" if fake_scenario else "environment_or_credential_file",
                    network_allowed=allow_network and fake_scenario is None,
                    duration_seconds=envelope.duration_seconds,
                )
            )
        except Exception as exc:  # converted to structured diagnostics for CLI/report users
            results.append(
                ApiProbeResult(
                    dataset=contract.dataset,
                    api_name=contract.api_name,
                    status=ProviderReadinessStatus.ERROR,
                    diagnostic_code=diagnostic_code_from_exception(exc),
                    message=_safe_message(exc),
                    requested_fields=requested_fields,
                    response_fields=[],
                    records=0,
                    credential_present=bool(probe_config.tushare_token),
                    credential_source_type="synthetic_fixture" if fake_scenario else "environment_or_credential_file",
                    network_allowed=allow_network and fake_scenario is None,
                )
            )
    return results


def _probe_params(config: AShareDataConfig, dataset: str) -> dict[str, str]:
    if dataset == "securities":
        return {"list_status": "L"}
    if dataset == "trade_calendar":
        return {"exchange": "SSE", "start_date": config.start_date, "end_date": config.end_date or config.start_date}
    params = {"start_date": config.start_date, "end_date": config.end_date or config.start_date}
    if dataset == "index_members":
        params["index_code"] = config.index_codes[0]
    return params


def diagnostic_code_from_exception(exc: Exception) -> str:
    if isinstance(exc, TushareRateLimitError):
        return ProviderDiagnosticCode.rate_limited
    if isinstance(exc, TusharePermissionError):
        lowered = str(exc).lower()
        if "token" in lowered or "invalid" in lowered:
            return ProviderDiagnosticCode.invalid_token
        return ProviderDiagnosticCode.permission_denied
    if isinstance(exc, TushareSchemaError):
        return ProviderDiagnosticCode.malformed_payload
    if isinstance(exc, TushareNetworkError):
        lowered = str(exc).lower()
        return ProviderDiagnosticCode.timeout if "timed out" in lowered or "timeout" in lowered else ProviderDiagnosticCode.network_error
    if isinstance(exc, ValueError) and "TUSHARE_TOKEN" in str(exc):
        return ProviderDiagnosticCode.missing_token
    if isinstance(exc, TushareApiError):
        return ProviderDiagnosticCode.unexpected_exception
    return ProviderDiagnosticCode.unexpected_exception


def _safe_message(exc: Exception) -> str:
    return str(exc).replace("\n", " ")
