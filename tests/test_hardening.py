"""Hardening tests — error paths, edge cases, and input validation."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warmline.cli import main
from warmline.core import (
    RulebookError,
    load_leads,
    load_leads_file,
    load_rulebook,
    load_rulebook_file,
    rank,
    score_leads,
)

DEMO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demos",
    "01-basic",
)
RULES = os.path.join(DEMO, "rulebook.yaml")
LEADS = os.path.join(DEMO, "leads.csv")


# ---------------------------------------------------------------------------
# load_rulebook: missing 'value' for non-exists ops
# ---------------------------------------------------------------------------

def test_rulebook_missing_value_for_op():
    """Non-exists ops without a 'value' key must raise RulebookError."""
    with pytest.raises(RulebookError, match="missing required"):
        load_rulebook(
            "rules:\n"
            "  - field: x\n"
            "    op: eq\n"
            "    points: 5\n"
        )


def test_rulebook_exists_op_without_value_ok():
    """The 'exists' op does not require a 'value' key."""
    rb = load_rulebook(
        "rules:\n"
        "  - name: has_email\n"
        "    field: email\n"
        "    op: exists\n"
        "    points: 5\n"
    )
    assert len(rb.rules) == 1
    assert rb.rules[0].op == "exists"


# ---------------------------------------------------------------------------
# load_leads: malformed JSON items
# ---------------------------------------------------------------------------

def test_json_leads_non_dict_element_raises():
    """A JSON list containing a non-object element must raise ValueError."""
    bad_json = json.dumps([{"name": "Alice"}, "not-an-object", {"name": "Bob"}])
    with pytest.raises(ValueError, match="not an object"):
        load_leads(bad_json)


def test_json_leads_explicit_non_dict_raises():
    """With explicit fmt='json', a list containing a non-dict item raises ValueError."""
    with pytest.raises(ValueError, match="not an object"):
        load_leads('["just a string"]', fmt="json")


# ---------------------------------------------------------------------------
# load_leads: empty / header-only input
# ---------------------------------------------------------------------------

def test_empty_leads_returns_empty_list():
    """Empty / whitespace-only input returns an empty list."""
    assert load_leads("") == []
    assert load_leads("   \n  ") == []


def test_csv_header_only_returns_empty_list():
    """A CSV with only a header row and no data rows returns []."""
    assert load_leads("name,score,industry\n", fmt="csv") == []


# ---------------------------------------------------------------------------
# load_leads_file / load_rulebook_file: missing file -> FileNotFoundError
# ---------------------------------------------------------------------------

def test_load_leads_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_leads_file("/no/such/leads.csv")


def test_load_rulebook_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_rulebook_file("/no/such/rules.yaml")


# ---------------------------------------------------------------------------
# CLI: --top negative exits 1
# ---------------------------------------------------------------------------

def test_cli_top_negative_exits_1(capsys):
    rc = main(["score", "-r", RULES, "-l", LEADS, "--top", "-1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "non-negative" in err


# ---------------------------------------------------------------------------
# CLI: missing leads file exits 1
# ---------------------------------------------------------------------------

def test_cli_missing_leads_file_exits_1():
    rc = main(["score", "-r", RULES, "-l", "/no/such/leads.csv"])
    assert rc == 1


# ---------------------------------------------------------------------------
# Edge case: all leads score zero (no rules match)
# ---------------------------------------------------------------------------

def test_score_all_zero_leads_cold():
    rb = load_rulebook(
        "rules:\n"
        "  - name: rare\n"
        "    field: x\n"
        "    op: eq\n"
        "    value: unicorn\n"
        "    points: 100\n"
    )
    leads = [{"name": "A"}, {"name": "B"}]
    ranked = rank(score_leads(leads, rb))
    assert all(s.score == 0 for s in ranked)
    assert all(s.tier == "cold" for s in ranked)
    assert all(s.matched == [] for s in ranked)


# ---------------------------------------------------------------------------
# Edge case: single lead
# ---------------------------------------------------------------------------

def test_single_lead_scores_correctly():
    rb = load_rulebook(
        "rules:\n"
        "  - name: big\n"
        "    field: employees\n"
        "    op: gte\n"
        "    value: 100\n"
        "    points: 40\n"
        "tiers:\n"
        "  warm: 30\n"
    )
    result = rank(score_leads([{"employees": "200"}], rb))
    assert len(result) == 1
    assert result[0].score == 40
    assert result[0].tier == "warm"
