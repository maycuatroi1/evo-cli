---
name: evo-storage-audit
command_path: "evo storage audit [--deep] [--category CATEGORY]"
occurrences: 12
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/storage.py (new file, new 'storage' group in cli.py)"
learned_at: 2026-08-21T12:53:44
source_session: ba88d061-1744-422b-a5cb-250c1ed20075
---

# CLI Suggestion: `evo storage audit [--deep] [--category CATEGORY]`

## Why
Repeated pattern of running `du -sh` against ~10 different filesystem paths in sequence (Library/Containers, Library/Caches, Library/Developer, /opt, /Applications, etc.). A single subcommand with presets for these categories eliminates the need to memorize paths and sequencing.

## Observed calls (12x)
- `du -sh ~/* | sort -hr | head -25`
- `du -sh ~/Library/* | sort -hr | head -20`
- `du -sh ~/Library/Containers/* | sort -hr | head -12`
- `du -sh ~/Library/Caches/* | sort -hr | head -15`
- `du -sh /opt/* | sort -hr | head`
- `du -sh /Applications /Library /usr/local /opt | sort -hr`

## Proposed location
`evo_cli/commands/storage.py (new file, new 'storage' group in cli.py)`

## Implementation sketch
```python
import click
from evo_cli.console import console, run_command

@click.group()
def storage():
    """Disk space analysis and cleanup."""
    pass

@storage.command()
@click.option('--deep', is_flag=True, help='Include /opt, /Applications, system dirs')
def audit(deep):
    """Audit disk usage across major categories."""
    categories = {
        'Home': '~',
        'Library': '~/Library',
        'Containers': '~/Library/Containers',
        'Caches': '~/Library/Caches',
        'Developer': '~/Library/Developer',
        'Downloads': '~/Downloads',
    }
    if deep:
        categories.update({
            'Applications': '/Applications',
            'Homebrew': '/opt/homebrew',
            'System Library': '/Library',
        })
    
    for label, path in categories.items():
        console.print(f"\n[bold]{label}[/bold]", end=' → ')
        run_command(f"du -sh {path}/* 2>/dev/null | sort -hr | head -5", shell=True)

cli.add_command(storage)
```
