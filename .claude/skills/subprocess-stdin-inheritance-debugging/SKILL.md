---
name: subprocess-stdin-inheritance-debugging
description: Identify and fix subprocess hangs caused by inheriting TTY stdin without EOF
learned: true
pattern_type: error_resolution
learned_at: 2026-06-27T21:14:12
source_session: b4657115-2053-42db-a043-b7744eaf3369
---

## When to use
A subprocess hangs in an interactive terminal but works in CI or tests. Common with MCP servers, language servers and other stdio JSON-RPC tools, which may start serving instead of honouring `--version`.

## How it happens
`subprocess.run(cmd)` without `stdin=` or `input=` inherits the parent's stdin. A terminal never sends EOF, so a child that reads stdin waits forever.
Under pytest or CI, stdin is `/dev/null` or a closed pipe, so the child gets EOF at once and the hang never reproduces.

## Debugging technique
1. Run the exact command by hand with `</dev/null`. If it exits there but hangs without the redirect, stdin is the cause.
2. Reproduce in Python with a stdin that stays open:
   ```python
   import os, subprocess
   r_fd, w_fd = os.pipe()  # w_fd stays open, so the child never sees EOF
   subprocess.run(['npx', 'package@latest', '--version'], stdin=r_fd, timeout=15)
   ```
   A `TimeoutExpired` here confirms it.

## Solution
- Pass `stdin=subprocess.DEVNULL` unless you mean to feed input.
- Always set `timeout=` for third-party tools so a hang cannot leak into production.

In evo-cli, use `evo_cli.console.run_command(cmd, stdin=subprocess.DEVNULL, timeout=30)`. Its default `stdin=None` inherits the terminal. On timeout it raises `CommandError` (with `check=True`), and it also resolves Windows `.cmd` shims (see [[windows-subprocess-resolve-executable]]).
