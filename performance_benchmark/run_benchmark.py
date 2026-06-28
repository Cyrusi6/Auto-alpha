"""CLI for local performance benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
import types

from .runner import run_benchmark


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run lightweight local A-share performance benchmarks.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_benchmark(
        data_dir=args.data_dir,
        matrix_cache_dir=args.matrix_cache_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if all(item.success or item.error == "skipped" for item in result.items) else 1


if __name__ == "__main__":
    raise SystemExit(main())


_parent = sys.modules.get(__package__)
if _parent is not None:  # Keep ``from performance_benchmark import run_benchmark`` returning the function.
    setattr(_parent, "run_benchmark", run_benchmark)


class _CallableBenchmarkModule(types.ModuleType):
    def __call__(self, *args, **kwargs):
        return run_benchmark(*args, **kwargs)


sys.modules[__name__].__class__ = _CallableBenchmarkModule
