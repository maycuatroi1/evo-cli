---
name: macos-safe-cleanup-sequence
description: Progressive cleanup of macOS caches and build artifacts, distinguishing safe auto-recoverable vs. risky permanent deletions
pattern_type: error_resolution
learned_at: 2026-08-21T12:53:44
source_session: ba88d061-1744-422b-a5cb-250c1ed20075
---

## When to use
When you've identified disk space is nearly full and want to free up space by clearing caches and build artifacts without risking user data or active project state.

## How
Find what is big before deleting anything: `df -h /System/Volumes/Data`, then
`du -sh ~/* ~/Library/* 2>/dev/null | sort -hr | head -20` and drill into the largest.

**Phase 1: Auto-recoverable** (safe to delete, regenerated on next use)
1. Docker build cache: `docker builder prune -af` (~15–20 GB typical)
2. Docker dangling images: `docker image prune -f` (untagged layers only)
3. Xcode DerivedData: `rm -rf ~/Library/Developer/Xcode/DerivedData/*`
4. Homebrew cleanup: `brew cleanup --prune=all && rm -rf ~/Library/Caches/Homebrew/*`
5. Package manager caches: `yarn cache clean`, `pip cache purge`, `go clean -cache`, `pnpm store prune`
6. App updater caches: `rm -rf ~/Library/Caches/{com.microsoft.VSCode.ShipIt,notion.id.ShipIt,termius-updater,org.swift.swiftpm}`

**Phase 2: Requires user decision** (permanent, but often safe)
- Docker unused images: `docker image prune -af` — also removes locally built images that cannot be re-pulled
- Docker volumes: `docker volume prune -f` removes anonymous unused volumes only; `-a` also deletes named
  volumes (database data) of stopped containers. List with `docker volume ls -f dangling=true` first
- iOS DeviceSupport (old SDKs): `rm -rf ~/Library/Developer/Xcode/iOS\ DeviceSupport/*`
- Simulator devices (unavailable): `xcrun simctl delete unavailable`
- App bundles in `~/Library/Application Support/` — only if app is no longer needed

**Phase 3: Archive or delete** (user-specific, rarely needed)
- Installers/archives in `~/Downloads` — often can be re-downloaded
- Old cloned repos in `~/github/` — identify inactive projects by last commit: `git -C <repo> log -1 --format=%cI`; check `git status` and unpushed work first

## Example
```bash
# Phase 1: No questions asked
docker builder prune -af
docker image prune -f
rm -rf ~/Library/Developer/Xcode/DerivedData/*
brew cleanup --prune=all
rm -rf ~/Library/Caches/Homebrew/*
yarn cache clean && pip cache purge

# Phase 2: Check first, then prune
docker ps -a  # stopped containers still own their named volumes
docker volume ls -f dangling=true
docker volume prune -f

# Final check
df -h /System/Volumes/Data
```

## Safety notes
- Never use `rm -rf` on paths you're unsure about; test with `du -sh` first
- For Docker: `docker system df` shows what will be pruned; `docker ps -a` shows all containers (running + stopped)
- Verify no app is actively writing to a directory before deleting (e.g., use `lsof +D <path>`)
- After cleanup, check if previously built projects rebuild cleanly (test one CI build)
