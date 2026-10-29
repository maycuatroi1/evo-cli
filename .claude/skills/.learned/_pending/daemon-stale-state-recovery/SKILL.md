---
name: daemon-stale-state-recovery
description: Detect and recover from background worker processes that died unexpectedly but left stale state files
pattern_type: error_resolution
learned: true
learned_at: 2026-10-01T14:42:48
source_session: ed390609-7eb2-4879-bee5-cc868749c7b8
---

## When to use

Your CLI tool spawns and manages background workers/daemons (e.g., long-running connections, services) and persists their state to files. A worker dies unexpectedly without cleanup, leaving the system in an inconsistent state: the state file says RUNNING/CONNECTED but the process is gone and socket communication hangs.

## Problem

The parent process gets stuck because:
1. State file still shows old state (e.g., CONNECTED) even though worker is dead
2. Socket/IPC communication to the worker hangs or times out
3. Lock files may or may not exist (depends on signal type: SIGKILL orphans locks, SIGTERM might release them)
4. No way to know whether the process is dead vs unreachable

## Solution: Reaping Check

When socket communication fails but state indicates the worker should be alive, run a reap check before giving up:

1. **Check if lock is held** — If lock file exists but `fcntl.flock()` succeeds immediately, no one holds it → worker is dead
2. **Check running processes** — Search `/proc` or `ps` for processes using the config file or matching the expected pattern
3. **Decide based on findings**:
   - No lock held + no running processes → worker is definitely dead; clean up state files and write final state with error context ("Worker exited unexpectedly")
   - Lock still held or process running → worker is stuck; keep state as UNKNOWN and suggest `sudo kill <PID>` in error message

## Implementation Pattern

```python
def _reap(name, profile_path):
    """Detect if a dead worker left stale state. Return (is_dead, error_msg)."""
    lock_path = profile_path.with_suffix('.lock')
    try:
        with open(lock_path, 'r+b') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquired = no one holds it = worker is dead
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return True, "No OpenVPN process was left running."
    except (BlockingIOError, FileNotFoundError):
        # Lock held or missing = check for running processes
        ps_output = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
        if str(profile_path) in ps_output:
            return False, f"Process still running; try: sudo kill <PID>"
        return True, "No OpenVPN process was left running."
```

## Benefits

- Distinguishes truly-dead workers (safe to clean up) from stuck/unreachable ones (need manual intervention)
- Provides actionable error messages instead of generic timeouts
- Prevents indefinite hangs and infinite retry loops
- Allows automatic recovery (cleanup) in common case, manual recovery in edge case

## See also

evo_cli/openvpn.py:_reap() and Worker.request() for a full implementation in a multi-profile OpenVPN context.
