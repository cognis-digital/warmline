"""WARMLINE core engine — lead scoring from a YAML rulebook.

No third-party imports. The YAML parser is a small, well-scoped subset
parser sufficient for rulebooks and lead files (mappings, lists of
mappings, scalars, comments). For richer YAML, leads may also be supplied
as JSON or CSV via the loader helpers.

A rulebook looks like:

    tiers:
      hot: 70
      warm: 40
    rules:
      - name: enterprise
        field: employees
        op: gte
        value: 500
        points: 30
      - name: target_industry
        field: industry
        op: in
        value: [saas, fintech]
        points: 20
      - name: engaged
        field: last_touch_days
        op: lte
        value: 14
        points: 15

Each matching rule adds its points to a lead's score. Leads are then
ranked descending and bucketed into tiers.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


class RulebookError(ValueError):
    """Raised when a rulebook is malformed or invalid."""


# Supported comparison operators. Each takes (lead_value, rule_value).
def _to_number(x: Any):
    """Best-effort numeric coercion; returns None if not numeric."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d*\.\d+", s):
            return float(s)
    return None


def _norm(x: Any) -> str:
    return str(x).strip().lower()


def _op_eq(a, b) -> bool:
    return _norm(a) == _norm(b)


def _op_ne(a, b) -> bool:
    return _norm(a) != _norm(b)


def _cmp_numeric(a, b, fn) -> bool:
    na, nb = _to_number(a), _to_number(b)
    if na is None or nb is None:
        return False
    return fn(na, nb)


def _op_gte(a, b):
    return _cmp_numeric(a, b, lambda x, y: x >= y)


def _op_gt(a, b):
    return _cmp_numeric(a, b, lambda x, y: x > y)


def _op_lte(a, b):
    return _cmp_numeric(a, b, lambda x, y: x <= y)


def _op_lt(a, b):
    return _cmp_numeric(a, b, lambda x, y: x < y)


def _op_in(a, b) -> bool:
    if not isinstance(b, (list, tuple)):
        b = [b]
    return any(_norm(a) == _norm(item) for item in b)


def _op_not_in(a, b) -> bool:
    return not _op_in(a, b)


def _op_contains(a, b) -> bool:
    return _norm(b) in _norm(a)


def _op_exists(a, b) -> bool:
    present = a is not None and _norm(a) != ""
    want = b if isinstance(b, bool) else _norm(b) in ("true", "1", "yes")
    return present == want


OPERATORS = {
    "eq": _op_eq,
    "ne": _op_ne,
    "gte": _op_gte,
    "gt": _op_gt,
    "lte": _op_lte,
    "lt": _op_lt,
    "in": _op_in,
    "not_in": _op_not_in,
    "contains": _op_contains,
    "exists": _op_exists,
}


@dataclass
class Rule:
    name: str
    field: str
    op: str
    value: Any
    points: float

    def matches(self, lead: dict) -> bool:
        fn = OPERATORS[self.op]
        return bool(fn(lead.get(self.field), self.value))


@dataclass
class Rulebook:
    rules: list[Rule] = field(default_factory=list)
    # tier name -> minimum score threshold
    tiers: dict[str, float] = field(default_factory=dict)

    def tier_for(self, score: float) -> str:
        """Return the highest tier whose threshold the score meets."""
        best_name = "cold"
        best_threshold = float("-inf")
        for name, threshold in self.tiers.items():
            if score >= threshold and threshold > best_threshold:
                best_name, best_threshold = name, threshold
        return best_name


@dataclass
class ScoredLead:
    lead: dict
    score: float
    tier: str
    matched: list[str]

    @property
    def name(self) -> str:
        for key in ("name", "company", "id", "email"):
            if self.lead.get(key):
                return str(self.lead[key])
        return "<unnamed>"

    def to_row(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "tier": self.tier,
            "matched": ";".join(self.matched),
        }


# --------------------------------------------------------------------------
# Minimal YAML subset parser (mappings, lists, scalars, inline [..] lists).
# --------------------------------------------------------------------------
def _parse_scalar(token: str) -> Any:
    t = token.strip()
    if t == "":
        return ""
    if (t.startswith('"') and t.endswith('"')) or (
        t.startswith("'") and t.endswith("'")
    ):
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none"):
        return None
    num = _to_number(t)
    if num is not None:
        return num
    return t


def _parse_inline_list(token: str) -> list:
    inner = token.strip()[1:-1].strip()
    if not inner:
        return []
    return [_parse_scalar(p) for p in _split_top_level(inner)]


def _split_top_level(s: str) -> list[str]:
    """Split on commas that are not inside quotes."""
    parts, buf, quote = [], [], None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts]


def _parse_value_token(token: str) -> Any:
    t = token.strip()
    if t.startswith("[") and t.endswith("]"):
        return _parse_inline_list(t)
    return _parse_scalar(t)


def _strip_comment(line: str) -> str:
    """Remove an unquoted trailing/whole-line comment."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def parse_simple_yaml(text: str) -> Any:
    """Parse a useful subset of YAML into Python objects.

    Supports nested mappings, lists of mappings/scalars (block style with
    '- '), inline lists '[a, b]', comments, and basic scalar typing.
    """
    raw_lines = text.splitlines()
    lines = []
    for ln in raw_lines:
        stripped = _strip_comment(ln)
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))

    pos = 0

    def parse_block(min_indent: int):
        nonlocal pos
        if pos >= len(lines):
            return None
        indent, content = lines[pos]
        if content.startswith("- "):
            return parse_list(indent)
        return parse_map(indent)

    def parse_list(indent: int) -> list:
        nonlocal pos
        items = []
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent != indent or not content.startswith("- "):
                break
            rest = content[2:].strip()
            if ":" in rest and not (rest.startswith("[")):
                # First key of a mapping item lives on the dash line.
                lines[pos] = (indent + 2, rest)
                items.append(parse_map(indent + 2))
            else:
                items.append(_parse_value_token(rest))
                pos += 1
        return items

    def parse_map(indent: int) -> dict:
        nonlocal pos
        result: dict = {}
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                break
            if content.startswith("- "):
                break
            if ":" not in content:
                pos += 1
                continue
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            pos += 1
            if val == "":
                # Nested block follows (map or list) at greater indent.
                if pos < len(lines) and lines[pos][0] > indent:
                    result[key] = parse_block(indent + 1)
                else:
                    result[key] = None
            else:
                result[key] = _parse_value_token(val)
        return result

    return parse_block(0)


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def load_rulebook(text: str) -> Rulebook:
    """Parse a YAML rulebook string into a Rulebook."""
    data = parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise RulebookError("rulebook must be a mapping with a 'rules' key")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RulebookError("rulebook must define a non-empty 'rules' list")

    rules: list[Rule] = []
    for i, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            raise RulebookError(f"rule #{i + 1} is not a mapping")
        missing = [k for k in ("field", "op", "points") if k not in r]
        if missing:
            raise RulebookError(
                f"rule #{i + 1} missing required keys: {', '.join(missing)}"
            )
        op = str(r["op"]).strip()
        if op not in OPERATORS:
            raise RulebookError(
                f"rule #{i + 1} has unknown op '{op}'. "
                f"valid: {', '.join(sorted(OPERATORS))}"
            )
        pts = _to_number(r["points"])
        if pts is None:
            raise RulebookError(f"rule #{i + 1} 'points' must be numeric")
        rules.append(
            Rule(
                name=str(r.get("name") or r["field"]),
                field=str(r["field"]),
                op=op,
                value=r.get("value"),
                points=float(pts),
            )
        )

    tiers: dict[str, float] = {}
    raw_tiers = data.get("tiers") or {}
    if isinstance(raw_tiers, dict):
        for name, threshold in raw_tiers.items():
            num = _to_number(threshold)
            if num is None:
                raise RulebookError(f"tier '{name}' threshold must be numeric")
            tiers[str(name)] = float(num)
    return Rulebook(rules=rules, tiers=tiers)


def load_rulebook_file(path: str) -> Rulebook:
    with open(path, "r", encoding="utf-8") as fh:
        return load_rulebook(fh.read())


def _coerce_lead_values(row: dict) -> dict:
    """Normalize keys (str) and keep values as-is; CSV values stay strings."""
    return {str(k).strip(): v for k, v in row.items()}


def load_leads(text: str, fmt: str | None = None) -> list[dict]:
    """Load leads from text. Format auto-detected unless given.

    Supports JSON (list of objects), CSV (header row), and YAML
    (a list of mappings, or a mapping with a 'leads' list).
    """
    text = text.strip()
    if not text:
        return []
    if fmt is None:
        if text[0] in "[{":
            fmt = "json"
        elif text.lstrip().startswith("-") or "leads:" in text.splitlines()[0]:
            fmt = "yaml"
        elif "," in text.splitlines()[0]:
            fmt = "csv"
        else:
            fmt = "yaml"

    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("leads", [])
        if not isinstance(data, list):
            raise ValueError("JSON leads must be a list of objects")
        return [_coerce_lead_values(d) for d in data if isinstance(d, dict)]

    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        return [_coerce_lead_values(row) for row in reader]

    # yaml
    data = parse_simple_yaml(text)
    if isinstance(data, dict):
        data = data.get("leads", [])
    if not isinstance(data, list):
        raise ValueError("YAML leads must be a list of mappings")
    return [_coerce_lead_values(d) for d in data if isinstance(d, dict)]


def load_leads_file(path: str) -> list[dict]:
    fmt = None
    low = path.lower()
    if low.endswith(".json"):
        fmt = "json"
    elif low.endswith(".csv"):
        fmt = "csv"
    elif low.endswith((".yaml", ".yml")):
        fmt = "yaml"
    with open(path, "r", encoding="utf-8") as fh:
        return load_leads(fh.read(), fmt=fmt)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def score_lead(lead: dict, rb: Rulebook) -> ScoredLead:
    """Score a single lead against the rulebook."""
    total = 0.0
    matched: list[str] = []
    for rule in rb.rules:
        if rule.matches(lead):
            total += rule.points
            matched.append(rule.name)
    tier = rb.tier_for(total)
    return ScoredLead(lead=lead, score=total, tier=tier, matched=matched)


def score_leads(leads: Iterable[dict], rb: Rulebook) -> list[ScoredLead]:
    return [score_lead(lead, rb) for lead in leads]


def rank(scored: Iterable[ScoredLead]) -> list[ScoredLead]:
    """Return scored leads sorted by score descending, name ascending."""
    return sorted(scored, key=lambda s: (-s.score, s.name.lower()))
