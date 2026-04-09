# Demo 01 — Basic lead scoring

This demo scores a small batch of sales leads against a git-versioned
YAML rulebook and prints a ranked queue.

## Files

- `rulebook.yaml` — the scoring rules and tier thresholds.
- `leads.csv` — five inbound leads to score.

## Rulebook summary

| Rule              | Condition                          | Points |
|-------------------|------------------------------------|-------:|
| enterprise        | `employees >= 500`                 |     30 |
| midmarket         | `employees >= 100`                 |     15 |
| target_industry   | `industry in [saas, fintech]`      |     20 |
| recent_engagement | `last_touch_days <= 14`            |     15 |
| has_budget        | `budget >= 50000`                  |     20 |
| free_email        | `email contains gmail.com`         |    -10 |

Tiers: `hot >= 70`, `warm >= 40`, `cold` otherwise.

## Run it

```bash
# Human-readable ranked table
python -m warmline score -r demos/01-basic/rulebook.yaml \
    -l demos/01-basic/leads.csv

# JSON queue for piping into CI / CRM sync
python -m warmline score -r demos/01-basic/rulebook.yaml \
    -l demos/01-basic/leads.csv --format json

# CI gate: nonzero exit if nobody is 'hot'
python -m warmline score -r demos/01-basic/rulebook.yaml \
    -l demos/01-basic/leads.csv --min-tier hot
```

## Expected result

The top of the ranked queue is **Globex** (enterprise + target industry +
budget + recent engagement = 85 points, tier `hot`). **Initech** lands in
`warm`, and the gmail-only solo lead **Pied Piper** is penalized into
`cold`. The `--min-tier hot` gate passes (exit 0) because Globex is hot.

Approximate scores:

| Lead         | Score | Tier |
|--------------|------:|------|
| Globex       |    85 | hot  |
| Initech      |    50 | warm |
| Umbrella     |    45 | warm |
| Hooli        |    35 | cold |
| Pied Piper   |     5 | cold |
