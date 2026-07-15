---
name: git-dubious-ownership-windows
description: Resolve 'detected dubious ownership' error on Windows when git repo is owned by different user/group
pattern_type: error_resolution
learned_at: 2026-06-20T09:59:11
source_session: 5ed23f8c-d212-4ced-9497-d11d4590a17e
---

## When to use
When `git pull` or git commands fail with:
```
fatal: detected dubious ownership in repository at '<path>'
'<path>' is owned by: BUILTIN/Administrators
but the current user is: DESKTOP-.../user
```
This occurs on Windows when the repository folder is owned by Administrators group but git is run by a non-admin user.

## How
Add the repository path to git's safe.directory list:
```bash
git config --global --add safe.directory <full-path>
```
Then retry the git command.

Alternative (once per repo):
```bash
git config --local --add safe.directory $(pwd)
```

## Why
Windows ACLs can cause owner/group mismatches, especially after cloning into a system directory or when running under different user contexts. Git's ownership check prevents unauthorized access, but can be safely disabled per repo.

## Example
```bash
git config --global --add safe.directory C:/Users/somet/github/evo-cli
git pull
```
