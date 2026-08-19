---
name: windows-ipv6-dns-fix
description: Diagnose and fix IPv6 DNS resolution lag causing network slowness on Windows
pattern_type: debugging_techniques
learned: true
learned_at: 2026-08-13T14:32:58
source_session: 47ca5c41-399e-4a48-a0a9-70458bf25c08
---

## When to use

Network operations to dual-stack sites (Google, Facebook) show unpredictable 1-2 second hangs or elevated TLS latency (40-60ms+), while IPv4-only connections are fast. DNS queries return AAAA records but TCP connections hang briefly.

## How

1. Diagnose with `evo netcheck` - compare latency variance across hosts
2. Run `nslookup <domain>` - if AAAA records exist but aren't reachable, IPv6 is broken
3. Test: `ping -6 ipv6.google.com` - times out if IPv6 is down

Fix on Windows:
```
Set registry: HKLM\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\DisabledComponents = 0x20
Restart the system.
```

This disables IPv6 stack and prevents the system from waiting for failed IPv6 resolution fallbacks.

## Example

`evo netcheck` output with broken IPv6:
```
Google (2404:6800:4005:81f::200e): TLS 42ms, hangs briefly during dual-stack resolution
Facebook: TLS 22ms, faster after IPv6 timeout
GitHub: TLS 59ms, high variance indicating resolution fallback
```

The latency variance on dual-stack sites despite healthy DNS (5-9ms) indicates broken IPv6 causing 1-2 second connection delays. Apply registry fix to resolve.
