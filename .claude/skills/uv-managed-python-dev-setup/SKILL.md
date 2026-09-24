---
name: uv-managed-python-dev-setup
description: Resolve externally-managed Python (uv) errors when installing evo-cli in development
learned: true
pattern_type: error_resolution
learned_at: 2026-05-22T16:19:54
source_session: e10e14af-b118-4dce-9528-45f5a44b3845
---

## When to use
`pip install -e .[test]` (what `make install` runs) fails with "This environment is externally managed" (PEP 668). Pythons installed by uv carry that marker, so pip refuses to modify them.

## How
Install into a project venv instead:

```bash
uv venv --seed .venv
uv pip install --python .venv -e '.[test]'
.venv/bin/python -m evo_cli --help
.venv/bin/python -m pytest -q
```

`--seed` puts pip into the venv. The Makefile's `ENV_PREFIX` switches `make lint` / `make test` to `.venv/bin/` only when `.venv/bin/pip` exists, and a plain `uv venv` has no pip.

## Gotchas
- The `evo` on PATH is a separate, non-editable install, so repo edits do not reach it. Run `python -m evo_cli` (or `.venv/bin/evo`) to test changes.
- Prefer `uv pip install` over `uv sync`: `uv.lock` is out of date, and `uv sync` rewrites it (`uv lock --check` fails). Update the lock on purpose, in its own commit.
- Do not work around PEP 668 with `--break-system-packages`.
