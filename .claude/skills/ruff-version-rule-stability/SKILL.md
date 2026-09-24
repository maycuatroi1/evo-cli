---
name: ruff-version-rule-stability
description: Ruff minor releases can widen the default rule set; how evo-cli pins ruff and handles an upgrade
learned: true
pattern_type: error_resolution
learned_at: 2026-08-25T08:43:24
source_session: 1f8cfb05-f85b-4b6d-9ea7-6c97a7b64894
---

## When to use
CI fails on lint after a ruff upgrade while local lint passes, or you are about to bump ruff in evo-cli.

## What happened
ruff 0.16 widened its default rules (PLW, BLE, SIM, S, DTZ, ...) and `ruff check` reported 111 new findings (da0b0eb).
The project first froze the rule set with `select`. The next commit (58608ed) went back to tracking ruff's defaults: it fixed the real findings and ignored only the deliberate ones.

## How (evo-cli policy)
- ruff is pinned to one minor version in `pyproject.toml` (`ruff>=0.16,<0.17`), so local runs and CI use the same defaults.
- The lint config tracks the defaults: `[tool.ruff.lint] extend-select = ["I"]` plus a short `ignore` list.
- Do not "fix" a failing upgrade by adding a big `select` list. `select` replaces the defaults, and listing families like `D`, `ANN` or `S` enables hundreds of new findings.
- Upgrade on purpose, in its own commit: bump the pin, run `make lint`, fix what is real, and add a code to `ignore` only when the pattern is deliberate. Say why in the commit message.
- Config goes under `[tool.ruff.lint]`. Top-level `select` under `[tool.ruff]` is deprecated.
