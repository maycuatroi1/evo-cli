---
name: subprocess-stdin-inheritance-debugging
description: Identify and fix subprocess hangs caused by inheriting TTY stdin without EOF
pattern_type: error_resolution
learned_at: 2026-06-27T21:14:12
source_session: b4657115-2053-42db-a043-b7744eaf3369
---

## When to use
When a subprocess hangs in interactive terminal but works in CI/tests/non-TTY environments. Especially common with MCP servers, language servers, or tools that communicate via JSON-RPC or similar protocols that read stdin.

## How it happens
A child process spawned with `subprocess.run(..., input=None)` inherits the parent's stdin. In a TTY, stdin never sends EOF — the server/tool that expects input (even if it advertises `--version`) will wait indefinitely.

In CI/tests where stdin is a pipe, the child gets EOF immediately, so the hang doesn't reproduce.

## Debugging technique
1. Confirm the tool supports `--version`: `npm view <package> version`
2. Reproduce the hang by simulating TTY stdin:
   ```python
   import os, subprocess, time
   r_fd, w_fd = os.pipe()  # keep w_fd open → child's stdin never closes
   t = time.time()
   subprocess.run(['npx', 'package@latest', '--version'], stdin=r_fd, timeout=15)
   ```
3. If it hangs to timeout, child is waiting on stdin despite receiving a flag that should exit immediately.

## Solution
When spawning external processes:
- **Always** pass `stdin=subprocess.DEVNULL` unless you explicitly intend to pipe input.
- **Always** set `timeout=<seconds>` as a safety net.
- Use `check=False` if a tool may exit non-zero on error; handle the rc yourself.

```python
subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=300, check=False)
```

With stdin closed, tools waiting for input receive EOF and exit; with timeout, no hang leaks into production.

## Related
[[safe-subprocess-command-wrapper]] — higher-level wrapper that enforces stdin/timeout defaults
