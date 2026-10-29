---
name: git-repository-age-audit
description: Scan repositories by git commit age and dirty status to identify stale or inactive projects
pattern_type: debugging_techniques
learned: true
learned_at: 2026-10-12T12:36:27
source_session: e93fbb0c-6aa5-45be-8557-36d2e0028ba9
---

## When to use
When freeing disk space and need to identify which repos in ~/github (or another directory) haven't been touched in weeks/months, or to audit what branches have uncommitted work.

## How
1. For each repo directory, get last commit timestamp: `git -C <repo> log -1 --format=%ct`
2. Calculate age in days: `(current_time - last_commit_time) / 86400`
3. Check for uncommitted work: `git -C <repo> status --short | wc -l`
4. Check for unpushed work: `git -C <repo> log origin/main..HEAD --oneline`
5. Group results by age bucket (>60d, 30-59d, <30d) and sort

## Example
The pattern that identified 45 stale artifact directories to remove:
```bash
now=$(date +%s)
while read d; do
  last=$(git -C "$d" log -1 --format=%ct 2>/dev/null || echo 0)
  age=$(( (now - last) / 86400 ))
  dirty=$(git -C "$d" status --short 2>/dev/null | wc -l)
  echo "$age\t$dirty\t$d"
done | awk -F'\t' '{print $1, ($1>=60 ? ">=60d" : ($1>=30 ? "30-59d" : "<30d")), $2, $3}' | sort
```

## Safety notes
- Age check alone is insufficient—repos can be mature and stable
- Always verify no unpushed work before deletion: `git log origin/main..HEAD`
- Check `git status` for uncommitted changes—these will be lost
- Consider `git log -1 --format=%ai` for human-readable timestamp instead of Unix epoch
