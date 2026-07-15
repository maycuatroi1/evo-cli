---
name: network-latency-breakdown-timing
description: Diagnose which phase of connection (DNS/TCP/TLS/TTFB) is causing slowness using curl timing
pattern_type: debugging_techniques
learned_at: 2026-06-20T09:59:11
source_session: 5ed23f8c-d212-4ced-9497-d11d4590a17e
---

## When to use
When network feels slow but you can't pinpoint whether it's DNS, routing, TLS, or server response. Use curl's built-in timing to break down the connection into phases.

## How
Run curl with `-w` (write-out format) to extract each phase:
```bash
curl -sS -o NUL -w "dns=%{time_namelookup}  tcp=%{time_connect}  tls=%{time_appconnect}  ttfb=%{time_starttransfer}  total=%{time_total}\n" https://target.com
```

Breakdown:
- `time_namelookup`: DNS lookup time
- `time_connect`: TCP handshake (establishes connection)
- `time_appconnect`: TLS handshake (HTTPS only)
- `time_starttransfer`: Time to first byte (server response delay)
- `time_total`: End-to-end time

If one phase is abnormally high, that's the bottleneck.

## Why
Networking problems are often misattributed to DNS, but the real cause may be TCP/TLS negotiation, packet loss, or server load. Breaking it down tells you exactly where to investigate.

## Example
Test across multiple targets to spot patterns:
```bash
for h in google.com facebook.com github.com; do
  echo "=== $h ==="
  curl -sS -o NUL -w "dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n" "https://$h"
done
```
