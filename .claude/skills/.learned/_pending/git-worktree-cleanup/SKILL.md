---
name: git-worktree-cleanup
description: Safely identify and remove merged worktrees after branches are integrated
pattern_type: debugging_techniques
learned: true
learned_at: 2026-10-12T12:36:27
source_session: e93fbb0c-6aa5-45be-8557-36d2e0028ba9
---

## When to use
After merging a feature branch into main, to clean up worktrees and reclaim disk space without losing uncommitted work.

## How
1. For each worktree candidate:
   - Fetch latest: `git -C <worktree> fetch -q origin`
   - Check if merged into base: `git -C <worktree> merge-base --is-ancestor HEAD origin/main`
   - Verify no uncommitted changes: `git -C <worktree> status --short`
   - Confirm no unpushed commits: `git -C <worktree> log origin/main..HEAD --oneline`
2. Find main repo location: `git -C <worktree> rev-parse --git-common-dir` then `dirname` it
3. Remove if all checks pass: `git worktree remove <worktree>`

## Example
The pattern used successfully in this session:
```bash
for w in merged_worktree_list; do
  git -C "$w" fetch -q origin 2>/dev/null || continue
  base=$(git -C "$w" symbolic-ref --short refs/remotes/origin/HEAD | cut -d/ -f2)
  if git -C "$w" merge-base --is-ancestor HEAD "origin/$base" 2>/dev/null && \
     [ -z "$(git -C "$w" status --short)" ]; then
    git worktree remove "$w"
    echo "removed $(basename $w)"
  fi
done
```

## Safety checks before removing
- **Always verify merged**: `git merge-base --is-ancestor` not just visual inspection
- **Check for unpushed commits**: They will be lost if worktree is removed
- **Verify no staged changes**: `git status --short` must be empty
- **Skip if shell files diverged**: e.g. `.env`, `harness.local.yaml` that differ from main repo (check with `diff <(cat worktree/file) <(cat main/file)` first)
