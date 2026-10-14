# win-utils

Small command-line utilities for Windows. Python 3.12 or newer, standard library only.

| Utility | What it does |
|---|---|
| [longpaths](#longpaths) | Finds files and folders whose paths are too long to copy to another disk |
| [recent](#recent) | Lists the most recently modified files, by file type or folder, and who owns them |

## Getting started

Each utility has a launcher script in the repo root. It runs straight from a clone, from any folder, with nothing to install:

```powershell
python C:\path\to\win-utils\longpaths.py --help
```

To type just `longpaths`, add a function to your PowerShell profile (`notepad $PROFILE`):

```powershell
function longpaths { python C:\path\to\win-utils\longpaths.py @args }
function recent { python C:\path\to\win-utils\recent.py @args }
```

Or install the repo with pip, which puts `longpaths` and `recent` commands on your PATH:

```powershell
pip install -e .
longpaths --help
```

`python -m winutils.longpaths` (or `winutils.recent`) also works, but only when the current folder is the repo root, because that is where Python finds the `winutils` package.

Run the tests from the repo root:

```powershell
python -m unittest -v
```

## longpaths

Copying a folder to another disk with Explorer (or any program that isn't long-path aware) fails for paths of 260 characters or more (Windows `MAX_PATH`). A tree that is fine where it is now can go over the limit once it lands in a deeper destination folder. `longpaths` scans a folder and lists every item that will cause trouble.

The examples assume the profile function or pip install from [Getting started](#getting-started); otherwise replace `longpaths` with `python C:\path\to\win-utils\longpaths.py`.

```powershell
# Check paths as they are now
longpaths C:\data\Photos

# Check paths as they will be after copying the contents of C:\data\Photos to E:\Backup\2024\Photos
longpaths C:\data\Photos --target E:\Backup\2024\Photos

# Save a spreadsheet of the problems
longpaths C:\data\Photos -t E:\Backup\2024\Photos -f csv -o problems.csv
```

`--target` is the folder that receives the *contents* of the scanned folder. If you are dragging `C:\data\Photos` onto `E:\Backup`, the files end up under `E:\Backup\Photos`, so pass `--target E:\Backup\Photos`. The target does not have to exist, so you can check a copy to a drive that isn't plugged in.

### What counts as too long

| Item | Fails at | Why |
|---|---|---|
| File path | 260 characters or more | `MAX_PATH` is 260 including the terminating null |
| Folder path | 248 characters or more | Creating a folder keeps 12 characters free for an 8.3 file name |
| Single name | more than 255 characters | NTFS limit for one file or folder name |

Lengths are counted in UTF-16 code units, the same way Windows counts them, so an emoji counts as 2. With `--target`, both the current path and the destination path are checked, because an over-long source also breaks a copy. Change the path limit with `--max-path N` (folders then fail at `N-12`).

### Output

```
Source  C:\data\Photos
Target  E:\Backup\2024\Photos  (receives the contents of Source)
Limits  files fail at 260 characters, folders at 248, names over 255

  LEN   OVER  KIND  WHY     PATH (relative to Source and Target)
  274     15  file  target  2019\Holiday in ...\IMG_0001.jpg
  251      4  dir   target  2019\Holiday in ...

Scanned 12,408 files and 311 folders.
Too long: 1 file and 1 folder.
```

- **LEN**: the longest of the checked paths. **OVER**: how many characters to remove to get under the limit.
- **WHY**: which check failed: `source`, `target`, `name`, or a combination such as `source+target`.
- `--format csv` and `--format json` include the full source and target paths. With `--output FILE` the report is written as UTF-8 (CSV with a BOM so Excel reads it correctly).
- Symbolic links and junctions are counted but not followed. Folders that can't be read (for example, access denied) are listed at the end; `--quiet` only counts them.

Exit status: `0` if nothing is too long, `1` if something is, `2` on an error such as a missing folder. You can use it in scripts:

```powershell
longpaths D:\Projects -t \\nas\backup\Projects -q
if ($LASTEXITCODE -eq 1) { Write-Warning "Shorten some paths before copying" }
```

## recent

`recent` lists the files below a folder that changed most recently, newest first, and counts how many of them each user owns. You can narrow the list by file name and age, and group it by file type or by folder to see what changed where.

The examples assume the profile function or pip install from [Getting started](#getting-started); otherwise replace `recent` with `python C:\path\to\win-utils\recent.py`.

```powershell
# The 20 most recently modified files
recent D:\Projects

# CSV and Excel files changed in the last 7 days
recent \\nas\shared\Finance -i "*.csv,*.xlsx" --since 7d

# What changed in each folder since 1 October, skipping Git and build folders
recent D:\Projects --by dir --since 2026-10-01 -x .git -x node_modules

# Every match, grouped by extension, as a spreadsheet
recent D:\Projects --by type --top 0 -f csv -o changes.csv
```

### Options

| Option | What it does |
|---|---|
| `-i`, `--include PATTERN` | Only files whose name matches, such as `*.csv`. Repeat the option or give a comma list: `-i "*.csv,*.xlsx"` |
| `-x`, `--exclude PATTERN` | Leave out files whose name matches, and skip folders whose name matches, such as `.git`. Wins over `--include` |
| `-s`, `--since WHEN` | Only files modified at or after WHEN: an age such as `30m`, `12h`, `7d` or `2w`, or a local date or time such as `2026-10-01` or `2026-10-01T08:00` |
| `-b`, `--by file\|type\|dir` | `file` (default) lists files newest first, `type` groups them by extension, `dir` by the folder they are in |
| `-n`, `--top N` | How many files to list: 20, or 5 per group with `--by type` or `--by dir`. `0` lists them all |
| `--no-owners` | Don't look up owners. This is faster on large trees and network shares |

Patterns are wildcards matched against file names (not paths), regardless of case. Age units are `s` seconds, `m` minutes, `h` hours, `d` days and `w` weeks.

### Output

```
Folder  D:\Projects
Filter  include *.csv, *.xlsx; modified since 2026-10-07 14:30

.csv  97 files, newest 2026-10-14 09:12  (CORP\alice 80, CORP\bob 17)
  MODIFIED             SIZE  OWNER       PATH (relative to Folder)
  2026-10-14 09:12   1.2 MB  CORP\alice  Finance\2026\q3\sales.csv
  2026-10-13 17:40  88.0 KB  CORP\bob    Finance\2026\q3\returns.csv

.xlsx  37 files, newest 2026-10-12 11:05  (CORP\alice 17, CORP\carol 13, CORP\bob 7)
  MODIFIED              SIZE  OWNER       PATH (relative to Folder)
  2026-10-12 11:05  412.3 KB  CORP\carol  Budget\2027 draft.xlsx
  2026-10-11 08:51   36.9 KB  CORP\alice  Finance\forecast.xlsx

Matched 134 files in 2 extensions; showing the newest 2 of each.
Scanned 5,210 files and 311 folders.
Owners of the 134 matching files:
  CORP\alice  97
  CORP\bob    24
  CORP\carol  13
```

- Each group header shows how many files matched in that group, when the newest one changed, and the owners with the most files in it.
- Owners are counted over **every** matching file, not just the ones listed, so `--top` changes the listing but not the counts. The owner is the account on the file's Security tab. An account that no longer exists shows as its SID, and `(unknown)` means the owner couldn't be read.
- `--format csv` writes one row per listed file (`group`, `modified`, `size`, `owner`, `relative_path`, `path`). `--format json` also has the owner counts for each group. Add `--top 0` to export every match. With `--output FILE` the report is written as UTF-8 (CSV with a BOM so Excel reads it correctly).
- Symbolic links and junctions are not followed. Folders that can't be read are listed at the end; `--quiet` only counts them.

Exit status: `0` if any file matched, `1` if none did, `2` on an error such as a missing folder. You can use it in scripts:

```powershell
recent \\nas\drop -i "*.csv" --since 1d -q
if ($LASTEXITCODE -eq 1) { Write-Warning "No new CSV files today" }
```
