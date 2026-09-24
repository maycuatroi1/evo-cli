---
name: evo-storage-cleanup
command_path: "evo storage cleanup [--docker] [--xcode] [--brew] [--caches] [--all] [--dry-run]"
occurrences: 8
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/storage.py (add to existing storage group)"
learned_at: 2026-08-21T12:53:44
source_session: ba88d061-1744-422b-a5cb-250c1ed20075
---

# CLI Suggestion: `evo storage cleanup [--docker] [--xcode] [--brew] [--caches] [--all] [--dry-run]`

## Why
Repeated sequence of cleanup commands (docker prune, brew cleanup, yarn cache clean, rm DerivedData, etc.), each run separately. A single command with category flags lets users customize cleanup scope and see what would be removed via --dry-run.

## Observed calls (8x)
- `docker builder prune -af`
- `docker image prune -af`
- `docker volume prune -af`
- `brew cleanup --prune=all && rm -rf ~/Library/Caches/Homebrew/*`
- `yarn cache clean && pip cache purge && go clean -cache && pnpm store prune`
- `rm -rf ~/Library/Developer/Xcode/DerivedData/*`
- `xcrun simctl delete unavailable`
- `rm -rf ~/Library/Caches/{com.microsoft.VSCode.ShipIt,notion.id.ShipIt,...}`

## Proposed location
`evo_cli/commands/storage.py (add to existing storage group)`

## Implementation sketch
```python
@storage.command()
@click.option('--docker', is_flag=True, help='Prune Docker images/volumes/cache')
@click.option('--xcode', is_flag=True, help='Clear Xcode DerivedData and simulators')
@click.option('--brew', is_flag=True, help='Run brew cleanup and clear cache')
@click.option('--caches', is_flag=True, help='Clear package manager caches (yarn, pip, go, pnpm)')
@click.option('--all', is_flag=True, help='Run all of the above')
@click.option('--dry-run', is_flag=True, help='Show what would be deleted, do not delete')
def cleanup(docker, xcode, brew, caches, all, dry_run):
    """Clean up system caches and build artifacts safely."""
    if all:
        docker = xcode = brew = caches = True
    
    freed = 0
    
    if docker:
        console.print("[yellow]Docker cleanup[/yellow]")
        if not dry_run:
            freed += run_cleanup('docker builder prune -af')
            freed += run_cleanup('docker image prune -af')
        else:
            run_command('docker system df', shell=True)
    
    if xcode:
        console.print("[yellow]Xcode cleanup[/yellow]")
        if not dry_run:
            run_command('rm -rf ~/Library/Developer/Xcode/DerivedData/*', shell=True)
            run_command('xcrun simctl delete unavailable', shell=True)
    
    if brew:
        console.print("[yellow]Homebrew cleanup[/yellow]")
        if not dry_run:
            run_cleanup('brew cleanup --prune=all')
            run_command('rm -rf ~/Library/Caches/Homebrew/*', shell=True)
    
    if caches:
        console.print("[yellow]Package manager caches[/yellow]")
        if not dry_run:
            run_command('yarn cache clean 2>/dev/null', shell=True)
            run_command('pip cache purge 2>/dev/null', shell=True)
            run_command('go clean -cache 2>/dev/null', shell=True)
            run_command('pnpm store prune 2>/dev/null', shell=True)
    
    console.print(f"\n[green]Done.[/green]")
    if not dry_run:
        run_command('df -h /System/Volumes/Data | tail -1', shell=True)
```
