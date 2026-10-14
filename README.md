# win-utils

Small command-line utilities for Windows. Python 3.12 or newer, standard library only.

| Utility | What it does |
|---|---|
| [longpaths](#longpaths) | Finds files and folders whose paths are too long to copy to another disk |

## Getting started

Run any utility straight from a clone:

```powershell
python -m winutils.longpaths --help
```

Or install the repo to get a command on your PATH:

```powershell
pip install -e .
longpaths --help
```

Run the tests from the repo root:

```powershell
python -m unittest -v
```

## longpaths

Copying a folder to another disk with Explorer (or any program that isn't long-path aware) fails for paths of 260 characters or more (Windows `MAX_PATH`). A tree that is fine where it is now can go over the limit once it lands in a deeper destination folder. `longpaths` scans a folder and lists every item that will cause trouble.

```powershell
# Check paths as they are now
python -m winutils.longpaths C:\data\Photos

# Check paths as they will be after copying the contents of C:\data\Photos to E:\Backup\2024\Photos
python -m winutils.longpaths C:\data\Photos --target E:\Backup\2024\Photos

# Save a spreadsheet of the problems
python -m winutils.longpaths C:\data\Photos -t E:\Backup\2024\Photos -f csv -o problems.csv
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
python -m winutils.longpaths D:\Projects -t \\nas\backup\Projects -q
if ($LASTEXITCODE -eq 1) { Write-Warning "Shorten some paths before copying" }
```
