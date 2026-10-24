"""CLI for deterministic Cobertura accounting and computed-rate thresholds."""

import argparse
import json
import sys

from . import __version__
from .core import InputError, audit_file


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recompute and audit Cobertura line coverage")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("report")
    parser.add_argument("--min-line-rate", default="0", help="required ratio, e.g. 0.8 for 80%%")
    parser.add_argument("--finding-limit", type=int, default=100)
    parser.add_argument("--file-limit", type=int, default=50)
    parser.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--max-nodes", type=int, default=250000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit_file(
            args.report,
            min_line_rate=args.min_line_rate,
            finding_limit=args.finding_limit,
            file_limit=args.file_limit,
            max_bytes=args.max_bytes,
            max_nodes=args.max_nodes,
        )
    except (InputError, OSError) as exc:
        print(f"cobertura-audit: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2))
    else:
        summary = report["computed"]
        print(
            f"{'PASS' if report['passed'] else 'FAIL'}: "
            f"{summary['lines_covered']}/{summary['lines_valid']} lines covered; "
            f"{report['findings_count']} findings"
        )
        for finding in report["findings"]:
            print(json.dumps(finding, sort_keys=True, ensure_ascii=True))
        if report["findings_truncated"]:
            print("Findings truncated; counts cover the full report.")
    return 0 if report["passed"] else 1
