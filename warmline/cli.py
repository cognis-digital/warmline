"""WARMLINE CLI — score and rank leads from a YAML rulebook.

Examples:
    # Rank leads, human-readable table
    warmline score --rules rulebook.yaml --leads leads.csv

    # Emit ranked queue as JSON for piping into CI / a CRM sync
    warmline score -r rulebook.yaml -l leads.json --format json > queue.json

    # Gate: fail (exit 2) if no lead reaches the 'hot' tier
    warmline score -r rulebook.yaml -l leads.csv --min-tier hot

Exit codes:
    0  success
    1  usage / parsing error
    2  gate failure (--min-tier / --min-score not met by any lead)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import TOOL_NAME, TOOL_VERSION
from .core import (
    RulebookError,
    load_leads_file,
    load_rulebook_file,
    rank,
    score_leads,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Git-versioned lead scoring: score & rank leads "
        "from a YAML rulebook.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    sub = parser.add_subparsers(dest="command")

    score = sub.add_parser(
        "score",
        help="score leads and print a ranked queue",
        description="Score each lead against the rulebook and rank them.",
    )
    score.add_argument(
        "-r", "--rules", required=True, help="path to YAML rulebook"
    )
    score.add_argument(
        "-l",
        "--leads",
        required=True,
        help="path to leads file (.csv, .json, or .yaml)",
    )
    score.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    score.add_argument(
        "--top", type=int, default=0, help="only show the top N leads"
    )
    score.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="exit 2 if no lead scores at least this much",
    )
    score.add_argument(
        "--min-tier",
        default=None,
        help="exit 2 if no lead reaches this tier",
    )
    return parser


def _format_table(scored, tiers: dict) -> str:
    if not scored:
        return "(no leads)"
    rows = [("#", "NAME", "SCORE", "TIER", "MATCHED")]
    for i, s in enumerate(scored, 1):
        rows.append(
            (
                str(i),
                s.name,
                f"{s.score:g}",
                s.tier,
                ", ".join(s.matched) or "-",
            )
        )
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    lines = []
    for ri, r in enumerate(rows):
        line = "  ".join(r[c].ljust(widths[c]) for c in range(len(r)))
        lines.append(line.rstrip())
        if ri == 0:
            lines.append("  ".join("-" * widths[c] for c in range(len(r))))
    return "\n".join(lines)


def _format_json(scored, rb) -> str:
    payload = {
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "count": len(scored),
        "tiers": rb.tiers,
        "queue": [
            {
                "rank": i,
                "name": s.name,
                "score": s.score,
                "tier": s.tier,
                "matched": s.matched,
                "lead": s.lead,
            }
            for i, s in enumerate(scored, 1)
        ],
    }
    return json.dumps(payload, indent=2)


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "score":
        parser.print_help()
        return 0

    try:
        rb = load_rulebook_file(args.rules)
        leads = load_leads_file(args.leads)
    except FileNotFoundError as exc:
        print(f"{TOOL_NAME}: file not found: {exc.filename}", file=sys.stderr)
        return 1
    except RulebookError as exc:
        print(f"{TOOL_NAME}: rulebook error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"{TOOL_NAME}: could not parse leads: {exc}", file=sys.stderr)
        return 1

    scored = rank(score_leads(leads, rb))
    if args.top and args.top > 0:
        scored = scored[: args.top]

    if args.format == "json":
        print(_format_json(scored, rb))
    else:
        print(_format_table(scored, rb.tiers))

    # CI gates
    if args.min_score is not None:
        if not any(s.score >= args.min_score for s in scored):
            print(
                f"{TOOL_NAME}: gate failed — no lead scored >= "
                f"{args.min_score:g}",
                file=sys.stderr,
            )
            return 2
    if args.min_tier is not None:
        want = args.min_tier.strip().lower()
        threshold = rb.tiers.get(want)
        if threshold is None:
            print(
                f"{TOOL_NAME}: gate failed — unknown tier '{args.min_tier}' "
                f"(known: {', '.join(rb.tiers) or 'none'})",
                file=sys.stderr,
            )
            return 2
        if not any(s.score >= threshold for s in scored):
            print(
                f"{TOOL_NAME}: gate failed — no lead reached tier "
                f"'{want}' (>= {threshold:g})",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
