---
name: nmap-tuning-for-filtered-hosts
description: Optimize nmap port scan for hosts with packet filtering or high latency
learned: true
pattern_type: debugging_techniques
learned_at: 2026-09-04T09:32:47
source_session: 89379a01-a8c5-47d3-a7c6-f1350c76ad8c
---

## When to use
Port scan with nmap hangs or times out on hosts that filter connections, drop packets, or have high latency. Default `nmap -T4 --top-ports 1000` may wait beyond 300s without completing.
Only scan hosts you own or are authorized to test.

## How
1. **Set a minimum packet rate** (`--min-rate 800`): nmap slows down when probes go unanswered, which a filtering host causes on purpose. A floor keeps the scan moving; it does not get past the filter.
2. **Reduce max retries** (`--max-retries 1`): a filtering host drops probes every time, so retransmits only add time. Trade-off: a port whose single reply was lost can be reported `filtered`.
3. **Cap per-probe wait** (`--max-rtt-timeout 500ms`): stops nmap from inflating its timeout on a lossy path.
4. **Host timeout is a last resort** (`--host-timeout 240s`): a host that hits it is skipped and nmap prints no port table for it, so the scan "finishes" with nothing. Save results with `-oA` and treat an empty report as a timeout, not a closed host.

## Example
```bash
# Original (hangs/timeouts):
nmap -Pn -T4 --top-ports 1000 --open <TARGET>

# Tuned for filtered host:
nmap -Pn -T4 --min-rate 800 --max-retries 1 --max-rtt-timeout 500ms --top-ports 1000 --open -oA scan <TARGET>
```
