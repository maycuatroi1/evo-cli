---
name: diagnose-macos-ssh
description: Systematic workflow for checking OpenSSH status, launchd service state, listening ports, and configuration on macOS
pattern_type: debugging_techniques
learned_at: 2026-07-18T15:17:54
source_session: e535ea23-7938-40a1-8c6f-565d4e1f0f5b
---

## When to use

When troubleshooting SSH connectivity or status on macOS:
- Verifying Remote Login is enabled
- SSH not responding from remote machines
- Need to audit current sshd configuration
- Diagnosing port conflicts or permission issues

## How

Run these checks in sequence to build a complete picture:

1. **Check launchd service state** — is sshd enabled or disabled?
   ```bash
   launchctl print-disabled system 2>/dev/null | grep -i ssh
   launchctl list 2>/dev/null | grep -i ssh
   ```
   Look for `"com.openssh.sshd" => enabled/disabled`.

2. **Verify listening on port 22** — is sshd actually bound?
   ```bash
   lsof -nP -iTCP:22 -sTCP:LISTEN 2>/dev/null
   netstat -an 2>/dev/null | grep -E '\.22 ' | head -20
   ```
   Should show both IPv4 and IPv6 LISTEN entries if enabled.

3. **Test live SSH response** — does sshd answer?
   ```bash
   nc -w 3 127.0.0.1 22 </dev/null 2>/dev/null | head -1
   ```
   Should return SSH version banner like `SSH-2.0-OpenSSH_10.2`.

4. **Get local network IP** — what address do remote users need?
   ```bash
   ipconfig getifaddr en0  # (or en1, en2, etc.)
   ```

5. **Check SSH access group restrictions** — are all users or only some allowed?
   ```bash
   dscl . -read /Groups/com.apple.access_ssh GroupMembership
   ```
   Empty = all users allowed; populated = restricted to listed users.

6. **Inspect sshd config files** — what non-default settings are active?
   ```bash
   grep -vE '^\s*(#|$)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* 2>/dev/null
   ```

7. **Get effective sshd configuration** — final truth (requires sudo):
   ```bash
   sudo sshd -T | grep -E 'passwordauthentication|permitrootlogin|pubkeyauthentication'
   ```

## Key insights

- Most checks run unprivileged; only `sshd -T` and config edits need sudo.
- Empty sshd_config means macOS built-in defaults apply (PasswordAuthentication: yes, PermitRootLogin: no, etc.).
- macOS GUI "System Settings → General → Sharing → Remote Login" is equivalent to `sudo systemsetup -setremotelogin on/off`.
- If checks pass but remote SSH still fails, issue is likely in firewall, router, or client key setup, not the sshd service itself.

## Example

```bash
# Quick diagnostic sequence:
$ launchctl print-disabled system | grep ssh
"com.openssh.sshd" => enabled  # ✓ Service is ON

$ lsof -nP -iTCP:22 -sTCP:LISTEN 2>/dev/null | wc -l
2  # ✓ Listening on 2 sockets (IPv4 + IPv6)

$ nc -w 3 127.0.0.1 22 </dev/null 2>/dev/null | head -1
SSH-2.0-OpenSSH_10.2  # ✓ Responding

$ ipconfig getifaddr en0
192.168.1.9  # ✓ LAN IP

# Conclusion: SSH ready. Others can `ssh user@192.168.1.9`
```

To disable if not needed: `sudo systemsetup -setremotelogin off`.
