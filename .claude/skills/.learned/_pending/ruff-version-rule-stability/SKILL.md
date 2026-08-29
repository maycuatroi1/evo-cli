---
name: ruff-version-rule-stability
description: Pin ruff rule set to prevent version updates from auto-enabling new rules
pattern_type: error_resolution
learned: true
learned_at: 2026-08-25T08:43:24
source_session: 1f8cfb05-f85b-4b6d-9ea7-6c97a7b64894
---

## When to use
After upgrading ruff, CI fails with new rule violations that weren't present before; local linting with the older version passes with no errors.

## How
Explicitly list rules in `pyproject.toml` using `select = [...]`. This locks the rule set and prevents ruff from enabling new rules in future versions.

```toml
[tool.ruff]
select = ['E', 'W', 'F', 'C90', 'I', 'N', 'D', 'UP', 'YTT', 'ANN', 'S', 'BLE', 'B', 'A', 'C4', 'DTZ', 'T20', 'EM', 'ISC', 'G', 'INP', 'PIE', 'T', 'PT', 'Q', 'RET', 'SIM', ...]
```

## Example
Ruff 0.16 added new default rule categories (PLW, BLE, SIM, S, DTZ). Existing code passing on ruff 0.14 suddenly showed 111 violations on 0.16. Pinning `select` prevented this breakage and guards against future version changes.
