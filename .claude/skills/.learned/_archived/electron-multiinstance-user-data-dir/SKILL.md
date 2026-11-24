---
name: electron-multiinstance-user-data-dir
description: Launch multiple isolated instances of an Electron app with separate user data directories and persistent launchers
pattern_type: workaround
learned: true
learned_at: 2026-10-05T14:37:36
source_session: d7b56639-479e-48ef-8e00-15cae58dd6f4
---

# Electron Multi-Instance via --user-data-dir

## When to use
- Running multiple isolated instances of an Electron app simultaneously (e.g., different user accounts, teams, or workspaces)
- Each instance needs fully separate settings, cache, history, and application state
- The app's built-in UI doesn't natively support account switching

## How

Electron respects the `--user-data-dir` flag to override the default user data location.

**Launch a new instance with isolated data:**

```bash
open -n -a "/Applications/<App>.app" --args --user-data-dir="$HOME/<profile-dir>"
```

The `-n` flag on macOS spawns a fresh instance without copying or linking the app itself. Each instance maintains completely separate:
- User preferences and settings
- Session history, cache, cookies
- Credentials and authentication tokens  
- Application state and workspace layout

**Create a persistent launcher:**

For repeated access, use Script Editor to create an application launcher:

1. Open Script Editor
2. Paste:
   ```applescript
   do shell script "open -n -a /Applications/<App>.app --args --user-data-dir=$HOME/<profile-dir>"
   ```
3. File → Export → File Format: Application → Save to ~/Applications/

The launcher runs when clicked and can be added to Dock or launched via Spotlight.

## Example

Running two Claude Desktop instances with different accounts:

```bash
# Instance 2: company account
open -n -a "/Applications/Claude.app" --args --user-data-dir="$HOME/ClaudeCompany"
```

Each opens independently; both can run concurrently.

## Limitations
- Unsupported: Most Electron apps (including Claude Desktop) do not officially support this approach
- Fragile: Future app updates may remove or change the flag
- One-off: Each profile needs its own launcher script unless wrapped in a manager app
- Unshared: Session data by default does NOT sync between instances (though file-level mirroring is possible for read-only data like chat history)
