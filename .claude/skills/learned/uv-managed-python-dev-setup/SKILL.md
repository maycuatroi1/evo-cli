---
name: uv-managed-python-dev-setup
description: Resolve externally-managed Python (uv) errors when installing evo-cli in development
pattern_type: error_resolution
learned_at: 2026-05-22T16:19:54
source_session: e10e14af-b118-4dce-9528-45f5a44b3845
---

## When to use

When installing the project on a system where Python is managed by uv, `pip install` fails with "This environment is externally managed" (PEP 668). Create an isolated venv to work around this.

## How

1. Create a venv using uv: `uv venv .venv`
2. Install the project into that venv: `uv pip install --python .venv -e .`
3. Run CLI commands via the venv: `.venv/bin/evo <command>` or `source .venv/bin/activate && evo <command>`

## Why

uv uses PEP 668 to mark system Python as externally-managed, preventing direct pip modifications. The workaround creates an isolated venv that uv manages separately, avoiding the conflict.

## Example

```bash
# On a uv-managed system:
uv venv .venv
uv pip install --python .venv -e .
.venv/bin/evo --help  # Now works
```
