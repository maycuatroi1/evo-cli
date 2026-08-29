---
name: makefile-oneshell-failure-masking
description: Fix .ONESHELL masking early command failures when later commands succeed
pattern_type: error_resolution
learned: true
learned_at: 2026-08-25T08:43:24
source_session: 1f8cfb05-f85b-4b6d-9ea7-6c97a7b64894
---

## When to use
A Makefile recipe with multiple commands passes on some platforms but fails on others; or early commands fail silently while later ones pass.

## How
Add `.SHELLFLAGS := -ec` to the Makefile. This enables `set -e` (exit on error) globally, so the recipe fails if any command fails.

```makefile
.SHELLFLAGS := -ec
.ONESHELL:

lint:
	ruff check .
	ruff format --check .
```

## Example
In evo-cli's `make lint`, if `ruff check` fails but `ruff format` passes, the recipe would exit 0 on Linux (where `.ONESHELL:` is supported) but exit 1 on macOS (where make 3.81 predates `.ONESHELL:` and runs each line separately). Adding `.SHELLFLAGS := -ec` makes behavior consistent across platforms by ensuring the first failure stops execution.
