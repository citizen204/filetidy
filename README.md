# filetidy

[![tests](https://github.com/citizen204/filetidy/actions/workflows/tests.yml/badge.svg)](https://github.com/citizen204/filetidy/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.8%2B-blue)
![platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)
![license](https://img.shields.io/badge/license-MIT-green)

Sort loose files into folders by type — identically on macOS, Windows and Linux.

Point it at a cluttered Desktop or Downloads folder and it files everything into
`_Archive/Documents`, `_Archive/PDF`, `_Archive/Screenshots`, and so on. It is a
dry run by default, every move is journalled, and `filetidy undo` puts it all
back.

No dependencies — the standard library only. Python 3.8+.

---

## Install

```bash
pip install git+https://github.com/citizen204/filetidy.git
```

Or clone and run it in place, with no install at all:

```bash
git clone https://github.com/citizen204/filetidy.git
cd filetidy
python -m filetidy run ~/Desktop
```

## Use

```bash
# See what would happen — nothing is moved
filetidy run ~/Desktop

# Do it
filetidy run ~/Desktop --apply

# Changed your mind
filetidy undo
```

On Windows the same commands work with Windows paths:

```powershell
filetidy run $env:USERPROFILE\Downloads --apply
```

### Result

```
Desktop/
└── _Archive/
    ├── Screenshots/   276 files
    ├── Documents/      42
    ├── PDF/            31
    ├── Code/           15
    ├── Images/         10
    ├── Compressed/      8
    └── Videos/          4
```

## Commands

| Command | What it does |
|---|---|
| `filetidy run PATH` | Show the plan. Add `--apply` to carry it out. |
| `filetidy undo` | Reverse the last run. `--list` shows earlier sessions, `--session ID` picks one. |
| `filetidy watch PATH` | Keep a folder tidy continuously — files get sorted as they land. |
| `filetidy rules` | Print which extension maps to which folder. |
| `filetidy doctor PATH` | Find filenames that break on another OS. `--fix` renames them. |
| `filetidy init` | Write a starter config file. |

### Useful options for `run` and `watch`

| Option | Effect |
|---|---|
| `--apply` | Actually move files (otherwise it is a dry run) |
| `-v` | List every file, not just the per-category counts |
| `-r, --recursive` | Also sort files that are already in sub-folders |
| `--date-folders` | Add a `YYYY-MM` level inside each category |
| `--archive NAME` | Use a different destination folder name |
| `--sanitize-names` | Repair names that break on Windows while filing them |
| `--min-age SECONDS` | Leave very recent files alone (default 60) |
| `--exclude GLOB` | Skip matching names; repeatable |
| `--include-hidden` | Include dotfiles |
| `--no-journal` | Do not record the moves (disables undo) |

## Safety

This tool moves your files, so it is built to be boring about it:

- **Dry run by default.** `run` shows a plan and exits. Only `--apply` touches anything.
- **Undo.** Every applied move is appended to a journal (`~/.local/state/filetidy/`
  on macOS and Linux, `%LOCALAPPDATA%\filetidy\` on Windows). `filetidy undo`
  replays it backwards and prunes the folders it emptied.
- **Nothing is ever overwritten.** A name that already exists becomes
  `report (2).pdf`. This is re-checked at the moment of the move, not just when
  the plan is built, so a file that appears in between is still safe.
- **In-progress downloads are skipped** — `.crdownload`, `.part`, `.download`,
  `.aria2` and friends — as are files modified in the last 60 seconds.
- **Nothing is deleted, ever.** filetidy only moves and renames.
- **The archive is never re-processed**, so repeated runs cannot nest
  `_Archive/_Archive/...`.

## Filename portability

Cross-platform is the point, so filetidy knows the ways a filename can be fine
on one OS and broken on another:

- **macOS screenshots contain U+202F**, a narrow no-break space, between the
  time and `am`/`pm`. It looks exactly like a space but is not one, so shell
  globs and scripts miss those files. filetidy matches screenshots through a
  unicode-normalising comparison, so `Screenshot ...`, `Screenshot (3).png`,
  `截屏...`, `屏幕截图...` and `CleanShot ...` all land in the same folder.
- **Windows rejects `< > : " / \ | ? *`**, control characters, trailing dots and
  spaces, and the device names `CON`, `PRN`, `NUL`, `COM1`…`LPT9`.
- **A `:` in a macOS filename** is stored as `/` and displayed as `:` — the
  reason a folder can look different in Finder and in the terminal.
- **macOS stores names in NFD, most other systems in NFC**, so two names that
  look identical can compare unequal.

`filetidy doctor` reports all of this and `--fix` repairs it:

```
$ filetidy doctor ~/Desktop
2 names would cause trouble on another platform:

  rent:bills.xlsx
      ! Windows-illegal character(s): :
      -> rent-bills.xlsx

  Screenshot 2026-05-22 at 1.32.23 pm.png
      ! non-standard space: U+202F
      -> Screenshot 2026-05-22 at 1.32.23 pm.png
```

Non-ASCII names are **not** anglicised. `预算表.xlsx` and `café.txt` are valid
everywhere and are left exactly as they are — only genuinely unsafe characters
are replaced.

## Configuration

`filetidy init` writes a starter `filetidy.json` in the current folder;
`filetidy init --user` writes it to the per-user location
(`~/.config/filetidy/` or `%APPDATA%\filetidy\`). A config sitting in the folder
being organised wins over the per-user one.

```json
{
  "archive_name": "_Archive",
  "min_age_seconds": 60,
  "sanitize_names": false,
  "date_subfolders": false,
  "excludes": ["*.lnk", "~$*"],
  "categories": {
    "Invoices": ["pdf"],
    "Other": []
  }
}
```

`categories` is merged key by key with the defaults, so you only name what you
want to change. Setting a category to `[]` removes it. The first category that
claims an extension wins, which is how `Invoices` above takes `.pdf` away from
the built-in `PDF` folder.

Run `filetidy rules` to see the resulting map.

## Keeping a folder tidy automatically

```bash
filetidy watch ~/Downloads --apply
```

It polls every 30 seconds (`--interval`), and the `--min-age` window means a
file is only filed once the browser has finished writing it.

To run it in the background at login, use the scheduler your OS already has:
`launchd` on macOS, Task Scheduler on Windows, a systemd user timer or a cron
entry on Linux.

## Development

```bash
git clone https://github.com/citizen204/filetidy.git
cd filetidy
python -m unittest discover -s tests -t . -v
```

69 tests, no dependencies. CI runs them on Ubuntu, macOS and Windows.

---

## 中文说明

把桌面、下载文件夹里散落的文件按类型自动归档，macOS 和 Windows 行为完全一致。

**默认是预演（dry run），不会动任何文件**，确认没问题再加 `--apply`。每次移动都有记录，`filetidy undo` 可以一键还原。零依赖，Python 3.8 以上即可。

```bash
# 安装
pip install git+https://github.com/citizen204/filetidy.git

# 先看看会怎么分类（不动文件）
filetidy run ~/Desktop

# 确认后执行
filetidy run ~/Desktop --apply

# 后悔了
filetidy undo
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--apply` | 真正执行移动（不加就只是预演） |
| `-v` | 列出每个文件，而不只是分类统计 |
| `-r` | 连子文件夹里的文件一起整理 |
| `--date-folders` | 每个分类下再按 `年-月` 分层 |
| `--sanitize-names` | 归档时顺手修掉在 Windows 上会出错的文件名 |
| `--min-age 秒数` | 跳过刚修改过的文件，默认 60 秒 |
| `--exclude 通配符` | 排除匹配的文件，可重复使用 |

### 关于中文和特殊字符文件名

这是跨平台工具最容易踩的坑，所以专门做了处理：

- **macOS 截图文件名里有 U+202F**（窄不换行空格），夹在时间和 `am`/`pm` 之间。它看起来跟普通空格一模一样，但在终端里用 `*` 通配符匹配会失败。filetidy 做 unicode 归一化后再匹配，所以 `Screenshot ...`、`Screenshot (3).png`、`截屏...`、`屏幕截图...`、`CleanShot ...` 都能正确识别。
- **Windows 不接受 `< > : " / \ | ? *`**、控制字符、结尾的点和空格，以及 `CON`、`NUL`、`COM1` 这类设备名。
- **文件名里的 `:` 在 macOS 上实际存储为 `/`**，Finder 里显示成冒号、终端里是斜杠 —— 这就是同一个文件夹在两个地方看起来名字不一样的原因。
- **macOS 用 NFD 存储文件名，其他系统用 NFC**，两个看起来相同的名字可能比较起来不相等。

用 `filetidy doctor ~/Desktop` 扫描，加 `--fix` 自动修复。

**中文文件名不会被转成拼音或英文** —— `预算表.xlsx` 在所有系统上都合法，会原样保留。只有真正会出问题的字符才替换。
