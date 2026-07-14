---
name: temp
command_path: "evo temp"
occurrences: 1
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/temp.py (new or add to system group)"
learned_at: 2026-06-22T23:44:12
source_session: ff0df63c-f765-4467-abb5-7b14390eb0a0
---

# CLI Suggestion: `evo temp`

## Why
User explicitly requested adding a temp monitoring subcommand to CLI for convenient reuse. Pattern: detect/install smctemp if needed, read CPU/GPU/battery temps across multiple sensors, show thermal pressure and top CPU processes to identify heat sources, format as table.

## Observed calls (1x)
- `smctemp -c -f`
- `smctemp -g -f`
- `ioreg -r -n AppleSmartBattery | grep '"Temperature"'`
- `brew install narugit/tap/smctemp`
- `sudo powermetrics --samplers thermal`

## Proposed location
`evo_cli/commands/temp.py (new or add to system group)`

## Implementation sketch
```python
@click.command('temp')
@click.option('--watch', is_flag=True, help='Watch temps continuously')
def temp(watch):
    """Display CPU/GPU/battery temperatures on macOS."""
    # 1. Check if smctemp installed; offer brew install narugit/tap/smctemp if missing
    # 2. Read CPU/GPU/battery temps via smctemp + ioreg
    # 3. Check thermal pressure: powermetrics --samplers thermal (cached sudo if available)
    # 4. Show top CPU processes to diagnose heat sources  
    # 5. Format as table: CPU°C | GPU°C | Battery°C | Pressure | Top Process
    # 6. With --watch, loop every 2s
```
