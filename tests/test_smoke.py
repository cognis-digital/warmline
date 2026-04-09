"""Smoke tests for WARMLINE — import core, run on the demo, assert behavior."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warmline import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    load_leads,
    load_leads_file,
    load_rulebook,
    load_rulebook_file,
    rank,
    score_lead,
    score_leads,
)
from warmline.cli import main  # noqa: E402
from warmline.core import RulebookError, parse_simple_yaml  # noqa: E402

DEMO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demos",
    "01-basic",
)
RULES = os.path.join(DEMO, "rulebook.yaml")
LEADS = os.path.join(DEMO, "leads.csv")


def test_metadata():
    assert TOOL_NAME == "warmline"
    assert TOOL_VERSION


def test_yaml_parser_basics():
    data = parse_simple_yaml(
        "tiers:\n  hot: 70\nrules:\n  - name: a\n    field: x\n"
        "    op: gte\n    value: [a, b]\n    points: 5\n"
    )
    assert data["tiers"]["hot"] == 70
    assert data["rules"][0]["value"] == ["a", "b"]
    assert data["rules"][0]["points"] == 5


def test_load_rulebook_validates():
    rb = load_rulebook_file(RULES)
    assert len(rb.rules) == 6
    assert rb.tiers["hot"] == 70
    assert rb.tier_for(85) == "hot"
    assert rb.tier_for(45) == "warm"
    assert rb.tier_for(5) == "cold"


def test_bad_rulebook_raises():
    with pytest.raises(RulebookError):
        load_rulebook("rules:\n  - field: x\n    op: nope\n    points: 1\n")
    with pytest.raises(RulebookError):
        load_rulebook("tiers:\n  hot: 1\n")  # no rules


def test_demo_scoring_and_ranking():
    rb = load_rulebook_file(RULES)
    leads = load_leads_file(LEADS)
    assert len(leads) == 5
    ranked = rank(score_leads(leads, rb))

    # Globex should top the queue and be hot.
    top = ranked[0]
    assert top.name == "Globex"
    assert top.tier == "hot"
    # enterprise(30)+target_industry(20)+recent(15)+budget(20) = 85
    assert top.score == 85
    assert "enterprise" in top.matched

    # Scores must be sorted descending.
    scores = [s.score for s in ranked]
    assert scores == sorted(scores, reverse=True)

    by_name = {s.name: s for s in ranked}
    # gmail penalty pushes the solo founder to cold.
    assert by_name["Pied Piper"].tier == "cold"
    assert by_name["Pied Piper"].score == 5  # recent(15) - free_email(10)


def test_operators_numeric_and_in():
    rb = load_rulebook(
        "rules:\n"
        "  - name: big\n    field: n\n    op: gt\n    value: 10\n"
        "    points: 5\n"
        "  - name: tagged\n    field: tag\n    op: in\n    value: [a, b]\n"
        "    points: 3\n"
    )
    hi = score_lead({"n": "42", "tag": "A"}, rb)  # string coercion + case
    assert hi.score == 8
    lo = score_lead({"n": "3", "tag": "z"}, rb)
    assert lo.score == 0


def test_load_leads_json_and_yaml():
    rb = load_rulebook_file(RULES)
    js = '[{"name": "X", "industry": "saas", "last_touch_days": 1}]'
    leads = load_leads(js)
    assert leads[0]["name"] == "X"
    s = score_lead(leads[0], rb)
    assert s.score == 35  # target_industry(20) + recent(15)


def test_cli_json_output(capsys):
    rc = main(["score", "-r", RULES, "-l", LEADS, "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    import json

    payload = json.loads(out)
    assert payload["tool"] == "warmline"
    assert payload["count"] == 5
    assert payload["queue"][0]["name"] == "Globex"
    assert payload["queue"][0]["rank"] == 1


def test_cli_table_and_top(capsys):
    rc = main(["score", "-r", RULES, "-l", LEADS, "--top", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Globex" in out
    assert "Pied Piper" not in out  # truncated by --top 2


def test_cli_gate_min_tier_pass_and_fail(capsys):
    # Globex is hot -> gate passes.
    assert main(["score", "-r", RULES, "-l", LEADS, "--min-tier", "hot"]) == 0
    # No lead reaches a fictional 100+ score via min-score gate.
    rc = main(["score", "-r", RULES, "-l", LEADS, "--min-score", "999"])
    assert rc == 2


def test_cli_unknown_tier_gate():
    rc = main(["score", "-r", RULES, "-l", LEADS, "--min-tier", "nope"])
    assert rc == 2


def test_cli_missing_file():
    rc = main(["score", "-r", "/no/such/rules.yaml", "-l", LEADS])
    assert rc == 1
