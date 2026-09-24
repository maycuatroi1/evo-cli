---
name: pytest-monkeypatch-iterator-state
description: Fix StopIteration when a stateful fake (iter / side_effect list) for a shared function like shutil.which is consumed by unrelated callers
learned: true
pattern_type: error_resolution
learned_at: 2026-08-25T08:43:24
source_session: 1f8cfb05-f85b-4b6d-9ea7-6c97a7b64894
---

## When to use
A test fakes a widely used function with a sequence of answers, e.g. `which` returning "missing" then "installed". It then fails with `StopIteration`, or asserts on the wrong state, after the code under test starts calling that function from more places.

## Why
`monkeypatch.setattr("pkg.mod.shutil.which", fake)` does not scope the patch to `pkg.mod`. `pkg.mod.shutil` is the `shutil` module itself, so every caller in the process sees the fake. In evo-cli, `run_command` calls `resolve_executable`, which calls `shutil.which(cmd[0])`. Each probe for `npm` or `sudo` therefore takes a value meant for the `opencode` lookup.

## How
Key the state on the argument, so only the lookups the test cares about advance it:

```python
states = iter([None, "/usr/bin/opencode"])  # missing before install, present after

def which(name):
    return next(states) if name == "opencode" else f"/usr/bin/{name}"

monkeypatch.setattr("evo_cli.commands.opencode.shutil.which", which)
```

A patch really is scoped to one module only when that module did `from shutil import which`; then patch `pkg.mod.which`.
