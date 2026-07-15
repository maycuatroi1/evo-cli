---
name: ipv6-half-broken-diagnosis
description: Diagnose and fix IPv6 that is only partially working (DNS resolves AAAA but no IPv6 route), causing intermittent lag
pattern_type: debugging_techniques
learned_at: 2026-06-20T09:59:11
source_session: 5ed23f8c-d212-4ced-9497-d11d4590a17e
---

## When to use
When users report intermittent slowness on specific sites (Google, Facebook, YouTube) but network diagnostics show low latency and no packet loss. This pattern is especially common on Windows.

## Symptoms
- Sites with IPv6 (dual-stack) feel slower or lag intermittently
- Other sites with IPv4-only feel normal
- DNS is fast (~10ms)
- Packet loss is 0%
- But browser feels "stuck" for a few seconds on certain sites

Root cause: Browser (Happy Eyeballs) tries IPv6 first, times out, then falls back to IPv4 — adding 3-5 second delay.

## How to diagnose

1. **Check if machine has IPv6 address and route:**
   ```powershell
   Get-NetIPAddress -AddressFamily IPv6 | Where-Object { $_.IPAddress -notlike "fe80*" }
   Get-NetRoute -DestinationPrefix "::/0"
   ```
   If both return nothing, machine has no global IPv6 and no IPv6 route.

2. **Check if DNS still returns AAAA records (dual-stack):**
   ```powershell
   nslookup google.com
   # or: Resolve-DnsName google.com -Type AAAA
   ```
   If you get both A and AAAA records, IPv6 is half-broken.

3. **Test IPv6 connectivity directly:**
   ```bash
   ping -6 -n 1 google.com
   ```
   If this fails ("could not find host" or timeout), IPv6 routing is broken.

## How to fix (Windows)

Prefer IPv4 over IPv6 by adjusting the prefix policy (requires admin PowerShell):
```powershell
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters" -Name DisabledComponents -Value 0x20 -Type DWord
```
Then **restart the machine**.

Value `0x20` deprioritizes IPv6 without disabling it — browser will try IPv4 first and avoid the timeout.

## Why
On systems with DNS but no IPv6 connectivity, the browser's Happy Eyeballs algorithm (RFC 8305) tries IPv6 first because DNS returned an AAAA record. It waits for timeout (~3-5 seconds) before falling back to IPv4. This is why only dual-stack sites lag.

## Example
After fix, the same site that felt slow should load immediately because the browser skips the failed IPv6 attempt.
