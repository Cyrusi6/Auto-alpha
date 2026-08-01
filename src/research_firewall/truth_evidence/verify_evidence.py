"""CLI verifier for scrubbed Task 054-A evidence packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evidence import verify_scrubbed_evidence_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="scrubbed Task 054 evidence JSON")
    args = parser.parse_args(argv)
    result = verify_scrubbed_evidence_package(Path(args.package))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
