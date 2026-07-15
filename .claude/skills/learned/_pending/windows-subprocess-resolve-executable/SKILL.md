---
name: windows-subprocess-resolve-executable
description: Fix WinError 2 when subprocess.run cannot find .cmd/.bat executables on Windows (npx, npm, pip-installed CLIs)
pattern_type: error_resolution
learned_at: 2026-06-29T05:35:46
source_session: e9fd9f95-e02d-4bf3-8721-7430871ad3ba
---

## When to use

When `subprocess.run` on Windows throws `FileNotFoundError: [WinError 2] The system cannot find the file specified` for commands like `npx`, `npm`, `opencode`, or any CLI installed via package managers that resolve to `.cmd` or `.bat` shims.

## Why

Windows `CreateProcess()` API only automatically appends `.exe` suffix; it does NOT consult `PATHEXT` for `.cmd`/`.bat`. When you call `subprocess.run(['npx', ...])` with a bare command name, Windows searches PATH, finds `npx.CMD`, but `CreateProcess` expects `.exe` (or a full path it recognizes), so it fails with WinError 2. The fix: resolve the command name to a **full path with correct suffix** before passing to `subprocess.run`. When CreateProcess sees `.CMD` or `.BAT` in the path, it routes execution through `cmd.exe` automatically.

## How

Add a helper function using `shutil.which()` to resolve command names:

```python
from shutil import which

def resolve_executable(cmd_list):
    """Resolve first element of cmd to full path for Windows .cmd/.bat compatibility.

    On Windows, npm/npx/opencode resolve to .cmd shims. subprocess.run(['npx', ...]) fails
    with WinError 2 because CreateProcess only auto-appends .exe, not .cmd. This function
    resolves 'npx' -> 'C:\\...\\npx.CMD' so CreateProcess recognizes it.

    On non-Windows or if resolution fails, returns cmd_list unchanged.
    """
    if not cmd_list:
        return cmd_list
    resolved = which(cmd_list[0])
    if resolved:
        return [resolved] + cmd_list[1:]
    return cmd_list
```

Then use resolved command in subprocess.run:
```python
cmd = resolve_executable(['npx', '-y', '@playwright/mcp@latest'])
result = subprocess.run(cmd, capture_output=True, text=True)
```

## Example

**Before (fails on Windows):**
```python
result = subprocess.run(['npx', '--version'], capture_output=True)
# WinError 2 on Windows
```

**After (works on all platforms):**
```python
result = subprocess.run(resolve_executable(['npx', '--version']), capture_output=True)
# On Windows: runs C:\Program Files\nodejs\npx.CMD
# On Linux/macOS: runs /usr/local/bin/npx (no change)
# Returns rc=0, stdout='10.9.2'
```
