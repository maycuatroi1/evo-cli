---
name: pytest-monkeypatch-iterator-state
description: Fix StopIteration when monkeypatching iterator-like functions across multiple call sites
pattern_type: error_resolution
learned: true
learned_at: 2026-08-25T08:43:24
source_session: 1f8cfb05-f85b-4b6d-9ea7-6c97a7b64894
---

## When to use
Tests fail with `StopIteration` when a mocked iterator is consumed by both test code and internal callers in the same module.

## How
Scope the monkeypatch to the specific module where the function is used, not the source module. Example:

```python
# Patch in the module that uses shutil.which, not in shutil itself
monkeypatch.setattr('evo_cli.commands.opencode.shutil.which', mock_which)
```

Not `monkeypatch.setattr('shutil.which', ...)`, which gets exhausted by internal callers like `resolve_executable()`.

## Example
In `test_opencode.py`, the test mocks `shutil.which` with a 2-element iterator. Function `resolve_executable()` also calls it internally, consuming one element. Without module-scoped patching, the test's call exhausts the iterator and raises `StopIteration`. Scope patching to `evo_cli.commands.opencode.shutil.which` to give each call site its own namespace.
