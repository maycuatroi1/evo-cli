---
name: storage-audit
command_path: "evo storage audit [--root PATH] [--threshold SIZE_MB] [--depth N]"
occurrences: 20
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/storage.py (new subcommand group)"
learned_at: 2026-10-12T12:36:27
source_session: e93fbb0c-6aa5-45be-8557-36d2e0028ba9
---

# CLI Suggestion: `evo storage audit [--root PATH] [--threshold SIZE_MB] [--depth N]`

## Why
Session repeatedly drilled through ~/Library, ~/github, ~/.cache with `du -sh <path>` and `sort -hr` to find what's consuming space. A CLI audit would provide structured traversal, filtering by threshold, and categorized output (caches, build artifacts, app data, repos, etc.), saving 20+ manual commands.

## Observed calls (20x)
- `du -sh ~/*`
- `du -sh ~/Library/*`
- `du -sh ~/.cache/*`
- `du -sh ~/Library/Containers/* ~/Library/Group\ Containers/*`
- `du -sh ~/.evo/* ~/.elan/* ~/go/*`
- `du -sh /Applications/* /Library/*`

## Proposed location
`evo_cli/commands/storage.py (new subcommand group)`

## Implementation sketch
```python
@click.group()
def storage():
    '''Storage and disk management utilities.'''
    pass

@storage.command()
@click.option('--root', type=click.Path(exists=True), default='~', help='Root directory to audit')
@click.option('--threshold', type=int, default=100, help='Minimum size in MB to report')
@click.option('--depth', type=int, default=2, help='Traversal depth')
def audit(root, threshold, depth):
    '''Scan directory for large items, grouped by category.'''
    # Walk tree, collect sizes, categorize (cache/build/.venv/node_modules/repo)
    # Filter by threshold, sort descending, output as table
    pass
```
