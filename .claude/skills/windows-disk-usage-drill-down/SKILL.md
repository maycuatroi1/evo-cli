---
name: windows-disk-usage-drill-down
description: Systematic PowerShell technique to identify large disk consumers by iterative drill-down from top-level folders
learned: true
pattern_type: debugging_techniques
learned_at: 2026-06-23T00:06:18
source_session: abfd043a-8f8d-471b-a8df-8fd2e104f9d3
---

## When to use
When investigating high disk usage on Windows and need to identify which folders/files are consuming the most space.

## How
Follow a hierarchical drill-down pattern:
1. **Check drive summary** — Get-PSDrive to see total/used/free
2. **List top-level folders** — Get-ChildItem C:\ -Directory with recursive size calculation (Measure-Object -Sum)
3. **Rank by size** — Sort-Object SizeGB -Descending
4. **Drill into largest** — repeat steps 2-3 for the folder consuming most space
5. **Isolate culprits** — continue drilling until you reach specific files or packages

Key technique: Use `Get-ChildItem -LiteralPath <path> -Recurse -File | Measure-Object -Property Length -Sum` wrapped in ForEach-Object to calculate folder sizes non-destructively.

## Example
```powershell
# Step 1: Check drive
Get-PSDrive C | Select-Object @{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}

# Step 2-3: List top folders by size
Get-ChildItem -Path C:\ -Directory -Force | ForEach-Object {
  $size = (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  [PSCustomObject]@{ Folder = $_.Name; SizeGB = [math]::Round($size/1GB,2) }
} | Sort-Object SizeGB -Descending

# Step 4: Drill into a specific path (e.g. AppData)
Get-ChildItem -Path $env:LOCALAPPDATA -Directory -Force | ForEach-Object {
  $size = (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  [PSCustomObject]@{ Folder = $_.Name; SizeGB = [math]::Round($size/1GB,2) }
} | Sort-Object SizeGB -Descending
```

`-ErrorAction SilentlyContinue` keeps access-denied folders (System Volume Information, other users) from flooding the output; their size is undercounted, so run elevated for a full picture. Recursing all of `C:\` takes minutes.

Use `-Force` to include hidden folders (AppData, $Recycle.Bin) and `-LiteralPath` to avoid PowerShell parsing special characters.
