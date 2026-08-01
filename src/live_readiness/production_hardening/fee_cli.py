"""Production CLI for the Task 055-G Fee Schedule v2 DAG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .fees import (
    FeeWorkflowError,
    acquire_fee_documents,
    build_fee_plan,
    extract_fee_rules,
    independent_verify_fee_schedule,
    publish_fee_document_verification,
    publish_fee_schedule_v2,
    validate_fee_document_acquisition,
    validate_fee_document_verification,
    validate_fee_plan,
    validate_fee_rule_extraction,
    validate_fee_schedule_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task 055-G governed Fee Schedule v2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("fee-plan")
    plan.add_argument("--config", required=True)
    plan.add_argument("--output-root", required=True)

    acquire = subparsers.add_parser("fee-document-acquire")
    acquire.add_argument("--plan", required=True)
    acquire.add_argument("--output-root", required=True)
    acquire.add_argument("--allow-network", action="store_true")

    verify = subparsers.add_parser("fee-document-verify")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--acquisition", required=True)
    verify.add_argument("--output-root", required=True)

    extract = subparsers.add_parser("fee-rule-extract")
    extract.add_argument("--plan", required=True)
    extract.add_argument("--acquisition", required=True)
    extract.add_argument("--document-verification", required=True)
    extract.add_argument("--output-root", required=True)

    publish = subparsers.add_parser("fee-publish")
    publish.add_argument("--plan", required=True)
    publish.add_argument("--acquisition", required=True)
    publish.add_argument("--document-verification", required=True)
    publish.add_argument("--extraction", required=True)
    publish.add_argument("--output-root", required=True)

    independent = subparsers.add_parser("fee-independent-verify")
    independent.add_argument("--schedule", required=True)
    independent.add_argument("--output-root", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    _test_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fee-plan":
            config = _read_json(Path(args.config))
            result = build_fee_plan(
                output_root=args.output_root,
                policy_seal=config["policy_seal"],
                simulation_start=config["simulation_start"],
                simulation_end=config["simulation_end"],
                documents=config["documents"],
                extractors=config["extractors"],
            )
            validate_fee_plan(result["manifest_path"])
        elif args.command == "fee-document-acquire":
            result = acquire_fee_documents(
                plan=args.plan,
                output_root=args.output_root,
                allow_network=bool(args.allow_network),
                fetcher=_test_fetcher,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
            validate_fee_document_acquisition(
                result["manifest_path"],
                plan=args.plan,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
        elif args.command == "fee-document-verify":
            result = publish_fee_document_verification(
                output_root=args.output_root,
                plan=args.plan,
                acquisition=args.acquisition,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
            validate_fee_document_verification(
                result["manifest_path"],
                plan=args.plan,
                acquisition=args.acquisition,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
        elif args.command == "fee-rule-extract":
            result = extract_fee_rules(
                output_root=args.output_root,
                plan=args.plan,
                acquisition=args.acquisition,
                document_verification=args.document_verification,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
            validate_fee_rule_extraction(
                result["manifest_path"],
                plan=args.plan,
                acquisition=args.acquisition,
                document_verification=args.document_verification,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
        elif args.command == "fee-publish":
            result = publish_fee_schedule_v2(
                output_root=args.output_root,
                plan=args.plan,
                acquisition=args.acquisition,
                document_verification=args.document_verification,
                extraction=args.extraction,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
            validate_fee_schedule_v2(
                result["manifest_path"],
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
        else:
            result = independent_verify_fee_schedule(
                schedule=args.schedule,
                output_root=args.output_root,
                allow_synthetic_test_fixture=_test_fetcher is not None,
            )
        print(json.dumps(_safe_summary(result), sort_keys=True, ensure_ascii=False))
        return 0
    except (FeeWorkflowError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True, ensure_ascii=False))
        return 2


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FeeWorkflowError("fee_cli_config_object_required")
    return payload


def _safe_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "schema_version": result.get("schema_version"),
        "content_hash": result.get("content_hash"),
        "generation_id": result.get("generation_id"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
