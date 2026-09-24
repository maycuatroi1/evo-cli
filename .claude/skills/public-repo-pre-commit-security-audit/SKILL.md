---
name: public-repo-pre-commit-security-audit
description: Scan what is about to be pushed to a public repo, and its history, for secrets and identifying data; tell real leaks from placeholders
learned: true
pattern_type: debugging_techniques
learned_at: 2026-09-25T00:06:57
source_session: 5d69e20b-7d07-4700-9281-82e0eb2732e8
---

## When to use
Before committing or pushing to a public repository (evo-cli is public), or when asked "is anything leaking?".
Covers keys and tokens, but also the quieter leaks: real server IPs, scan targets, internal hostnames, home paths, emails.

## How
1. **Scan exactly what `git add -A` would add**: tracked files plus untracked, not-ignored ones.
   `{ git ls-files; git ls-files --others --exclude-standard; } | sort -u`
2. **Scan history too**: every blob from `git rev-list --all --objects`, plus `git log --all --format=%B` for commit messages.
   Without gitleaks/trufflehog, a short Python regex scan works: private keys, `ghp_`/`github_pat_`, `sk-`, `AIza`, `AKIA`, JWTs, `otpauth://`, `user:pass@` URLs, `password = "..."`, emails, `/Users/<name>` and `C:\Users\<name>`, IPv4.
3. **Drop known false positives**: RFC 5737 ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), public resolvers (8.8.8.8, 1.1.1.1, ISP DNS), `1.2.3.4`-style test data, `Chrome/138.0.0.0` user agents, and hashes in `uv.lock`/`package-lock.json` that match phone or key patterns.
4. **Check which refs hold a hit before calling it public**:
   `for r in $(git for-each-ref --format='%(refname)'); do git grep -l '<value>' $r; done`
   Only branches and tags on the remote are public. Local tool refs such as `refs/codex/*` are not pushed by default, but `git push --mirror` would push them.
5. **Auto-generated agent files are a leak source**: learned skills and CLI suggestions quote real commands, including scan targets, LAN IPs and home paths. Read them before committing.
6. **Fix forward**: replace real hosts with RFC 5737 IPs and RFC 2606 names (`example.com`, `*.example`, `*.test`). Add ignore rules for key or profile files (`*.pem`, `*.key`, `*.ovpn`, `.env.*`).
7. **A leaked secret gets rotated**: rewriting history does not unpublish it, because clones, forks and PyPI sdists keep copies. Rewrite only when the user decides to, and only after rotating.

## Gotcha
`git stash` then `git stash pop` without `--index` drops staged state. A file you untracked with `git rm --cached` is tracked again afterwards, so check `git status` before committing.
