---
name: disk-usage-analyzer
command_path: "evo disk-usage [PATH]"
occurrences: 1
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/disk.py (new system diagnostics group)"
learned_at: 2026-06-23T00:06:18
source_session: abfd043a-8f8d-471b-a8df-8fd2e104f9d3
---

# CLI Suggestion: `evo disk-usage [PATH]`

## Why
Encapsulate Windows disk-space drill-down analysis as a portable CLI tool. The pattern of iteratively checking top-level folders, then drilling into the largest, is reusable for system diagnostics and matches evo-cli's goal of automating workflow tasks.

## Observed calls (1x)
- `Get-PSDrive C (check drive summary)`
- `Get-ChildItem -Path C:\ -Directory (top-level folders)`
- `Get-ChildItem -Path C:\Users -Directory (drill into Users)`
- `Get-ChildItem -Path C:\Program Files (x86) -Directory (drill into Program Files)`
- `Get-ChildItem -Path C:\Users\somet\AppData\Local\Packages -Directory (drill into Packages)`

## Proposed location
`evo_cli/commands/disk.py (new system diagnostics group)`

## Implementation sketch
```python
import click
from evo_cli.console import console, run_command

@click.command()
@click.argument('path', default='C:\\', type=click.Path(exists=True))
@click.option('--depth', default=2, help='How many levels to drill down')
@click.option('--top-n', default=10, help='Show top N folders per level')
def disk_usage(path, depth, top_n):
    """Analyze disk usage and identify large folders (Windows)."""
    # Use Get-ChildItem PowerShell helper to list and rank folders by size
    # Iteratively drill down into largest folders
    # Output structured summary
```
