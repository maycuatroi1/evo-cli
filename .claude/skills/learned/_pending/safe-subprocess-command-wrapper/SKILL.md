---
name: safe-subprocess-command-wrapper
description: CLI helper to safely spawn external commands with timeout and stdin isolation
pattern_type: error_resolution
learned_at: 2026-06-27T21:14:12
source_session: b4657115-2053-42db-a043-b7744eaf3369
---

## When to use
When a codebase spawns external processes/commands and needs consistent timeout + stdin handling to prevent hangs. This is a wrapper around `subprocess.run()` that makes safe defaults mandatory.

## Pattern
Add optional `stdin` and `timeout` parameters to your subprocess wrapper. Default to sensible values:
- `stdin=subprocess.DEVNULL` (no TTY inheritance)
- `timeout=<domain-specific>` (e.g., 30s for version checks, 300s for downloads)
- Raise a custom exception (e.g., `CommandError`) on timeout, not `subprocess.TimeoutExpired`

## Example (Python)
```python
def run_command(cmd, status, stdin=subprocess.DEVNULL, timeout=None, check=True):
    """Spawn cmd; always detach stdin from terminal, set timeout.
    
    stdin: stdin strategy (default DEVNULL). Use PIPE to provide input.
    timeout: max seconds; None = no limit. On timeout, raise CommandError.
    """
    try:
        result = subprocess.run(
            cmd, stdin=stdin, capture_output=True, text=True,
            timeout=timeout, check=check
        )
        return result
    except subprocess.TimeoutExpired:
        raise CommandError(f"Command timed out after {timeout}s: {cmd}")
```

Callers pass explicit stdin/timeout:
```python
run_command(['npx', 'pkg@latest', '--version'], 
            stdin=subprocess.DEVNULL, timeout=30)
```

## Related
[[subprocess-stdin-inheritance-debugging]] — how to detect this issue
