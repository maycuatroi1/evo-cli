---
name: safe-folder-cleanup-with-references
description: Organize Downloads/Desktop by detecting safe-to-delete files through reference tracing and duplicate identification
pattern_type: project_specific
learned: true
learned_at: 2026-11-02T13:28:17
source_session: a770c384-851f-445c-9b26-142ed62b1df5
---

## When to use

Before aggressively deleting or reorganizing large folders like Downloads or Desktop that may contain:
- Files with existing references in configs, credentials, or repo code
- Exact duplicates or redundant archives
- Items too old to be useful but potentially still referenced

## How

1. **Profile the folder**: Size distribution, file types by extension, age distribution (mtime by year).
2. **Find duplicates**: Hash files for exact matches, extract zips and check if contents already exist separately, identify renamed collisions (e.g., `file.docx`, `file (1).docx`).
3. **Trace references**: Grep credentials, SSH configs, omelet.json, and repo code (with `rg`) to find paths still actively used.
4. **Smart categorization**: Use a classification script with pattern rules (regex on filenames, keywords) to bucket files into categories (Teaching, Work, Research, Images, etc.). 
5. **Decide for each file**: Keep if referenced or recent (<30 days); move to category folder if safe; trash if duplicate or broken reference.
6. **Safe execution**: Move files with logging (TSV record for audit trail), verify counts before/after, handle iCloud dataless files, report failures.

## Example

```bash
# Build reference census
grep -r 'Downloads/' ~/.omelet.d/credentials/ ~/.config/ /Users/*/github/*/
rg -o 'Downloads/[^"\x27`)]' /Users/anhbinhnguyen/github/

# Classify and move
python3 classify.py items.json > plan.txt
grep 'move' plan.txt | while read _ file category; do
  mkdir -p ~/Downloads/$category
  mv ~/Downloads/$file ~/Downloads/$category/
done
```

Result: 948 files reorganized into 10 categories, 50 duplicates trashed, 69 kept-in-place because still referenced.
