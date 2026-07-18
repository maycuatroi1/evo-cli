---
name: detect-python-cli-installer
description: Detect whether a Python CLI was installed via pip, pipx, uv, or as an editable checkout
pattern_type: project_specific
learned_at: 2026-07-18T14:24:39
source_session: cb044002-10d8-43e6-b9d4-467f8a377c37
---

## When to use

When building self-updating Python CLI tools or tools that need to adapt behavior based on installation mode. Useful for `evo update`-like commands, or any tool that should know how it was installed.

## How

Check installation mode in this order:

1. **Editable checkout**: Read `{site-packages}/evo_cli-X.X.X.dist-info/direct_url.json` and look for `dir_info.editable=true` + `url=file://...`
2. **Pipx**: Check if `sys.prefix` ends with `.local/pipx/venvs/` (Linux/Mac) or `%APPDATA%\Local\pipx\venvs\` (Windows)
3. **Uv tool**: Check if `sys.prefix` contains `/uv/tools/` or `uv/tools/`
4. **Pip**: Default fallback

For editable installs, compare git HEAD against upstream (e.g., `git rev-list --left-right --count origin/main..HEAD`) instead of version numbers, since PyPI is not authoritative.

## Example

From `evo_cli/commands/update.py`:

- Read `direct_url.json` via `importlib.resources` → `json.loads()`
- Use `Path(sys.prefix)` to normalize and check for marker directories
- Fall back to package version/PyPI if no marker matches

## Key insight

Direct URL detection is the most reliable signal; sys.prefix checks are string-based fallbacks. Always verify the directory exists before assuming a mode.
