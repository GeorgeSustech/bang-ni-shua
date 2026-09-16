# 帮你刷

This is a local Python program that uses a dedicated Google Chrome profile to operate the Changjiang Rain Classroom web player and plays opened, unfinished videos from a selected course. It only uses normal web-page playback and reads the progress shown by the platform; it does not forge learning records through private APIs and does not answer check-ins, questions, quizzes, or assignments.

Two entry points:

- **macOS GUI**: open `帮你刷.app` or double-click `启动帮你刷.command` and pick a course in the window.
- **Windows command line**: double-click `启动帮你刷.cmd` for a guided flow, or call the `cli.py` subcommands directly. The command line version also runs on macOS.

The GitHub URL uses the ASCII name `bang-ni-shua` because GitHub does not allow Chinese repository names; the project, app window, and README are named “帮你刷”.

Language: [简体中文](README.md) · [English](README.en.md)

## 1. System, hardware, and software requirements

### macOS (GUI version)

Minimum

- macOS 13 Ventura or later (Chrome's current Mac support range)
- A 64-bit Intel Mac or Apple Silicon (M1)
- 8 GB RAM and at least 2 GB of free disk space
- A reliable network connection; keep the Mac awake and do not close the lid while running
- Google Chrome; Python 3.11 or later, or the bundled Python installer in this repository

Recommended

- macOS 14 Sonoma or later
- Apple Silicon M1/M2/M3/M4, or a quad-core-or-better Intel processor
- 16 GB RAM, at least 5 GB of free SSD space, and AC power
- The latest stable Google Chrome and Python 3.14.7 universal2

The repository includes the official Python 3.14.7 macOS universal2 installer (about 75 MB) at `installers/python-3.14.7-macos11.pkg`, together with its SHA-256 checksum. On a clean Mac, double-click `安装帮你刷环境.command`: it verifies and installs Python, creates a virtual environment, installs dependencies, and launches the app. Installing Python may ask for an administrator password.

### Windows (command line version)

- Windows 10 (1809 or later) or Windows 11, 64-bit
- Google Chrome (the installer checks for it and opens the official download page when missing)
- Python 3.11 or later; the installer offers a winget install of Python 3.12 when none is found
- 8 GB RAM, at least 2 GB of free disk space, and a network connection while running

The command line version does not use `tkinter`, needs no GUI components, and can run from any terminal, PowerShell, or Task Scheduler.

## 2. Official Chrome installation page

If Chrome is not installed, use the [official Google Chrome installation page](https://www.google.com/chrome/).

- macOS: place Chrome at `/Applications/Google Chrome.app`, then run `安装帮你刷环境.command` or open `帮你刷.app`.
- Windows: the default install location is enough; the program looks for `chrome.exe` in the registry and the usual directories.

## 3. How to use

### 3.1 macOS GUI

1. Double-click `启动帮你刷.command`, or open `帮你刷.app`.
2. Click “打开登录窗口” and complete WeChat QR-code login in the dedicated Chrome window.
3. Enter a course name, click “查找课程”, and choose the exact class.
4. Click “核对视频列表”, review the list, then click “开始 / 从未完成处继续”.

### 3.2 Windows command line

1. Double-click `安装帮你刷环境.cmd`: it checks for Python 3.11+, creates `.venv`, installs dependencies, and checks Chrome. The first install downloads packages from the network.
2. Double-click `启动帮你刷.cmd` for a menu: `1` start/resume (Enter picks it), `2` change course, `3` change speed, `4` show progress, `0` exit. The header shows the current course keyword and speed, and speed changes are saved immediately. After choosing: pick a class, review the video list, confirm, and run; on first use it offers to open the login window.
3. Or use the subcommands directly:

```powershell
cd D:\path\to\bang-ni-shua
.\.venv\Scripts\python.exe cli.py doctor --input     # print environment info and probe keyboard input
.\.venv\Scripts\python.exe cli.py login          # open the dedicated Chrome window and log in
.\.venv\Scripts\python.exe cli.py courses 英语    # list classes whose name contains “英语”
.\.venv\Scripts\python.exe cli.py videos 英语     # review the video list without playing
.\.venv\Scripts\python.exe cli.py run 英语        # resume from the first unfinished video
.\.venv\Scripts\python.exe cli.py run 英语 --speed 1.5   # play at 1.5x and remember the setting
.\.venv\Scripts\python.exe cli.py status          # show saved progress and the last result
```

If typing in the window does nothing, a click probably put the console into selection mode; press `Esc` inside the window. You can also skip the menu entirely: `启动帮你刷.cmd run 课程关键字 --speed 1.5`.

Controls while running:

- Enter: pause / resume
- `q` or `s`: stop, save progress, and exit
- `Ctrl+C`: first press stops gracefully, a second one exits immediately

When a keyword matches several classes, pick one with `--class 班级关键字` or `--index 序号`:

```powershell
.\.venv\Scripts\python.exe cli.py run 英语 --class 博士
.\.venv\Scripts\python.exe cli.py run 英语 --index 2 --yes
```

`--yes` skips the confirmation prompt, which suits Windows Task Scheduler. Running the same course again skips videos the platform already marks complete and videos that are not open yet.

`--speed` sets the playback rate (`0.5`–`4`; `1` is normal speed) and remembers it in `settings.json`, so later runs reuse it; the guided flow asks once and Enter keeps the stored value. The rate is applied as `playbackRate` on the player only — nothing is seeked and no completion field is touched. Note that **the platform may not count time watched at a higher rate**, in which case the video goes through the “recheck after one hour, then report as unrecorded” path, so raising the speed is a trade-off you decide on.

Exit codes: `0` all opened videos completed · `1` unfinished items remain · `2` stopped manually · `3` blocked or failed.

### 3.3 Runtime behaviour

The program plays videos in order at normal speed by default. If it encounters an unrecognized dialog, in-video question, quiz, or a stuck page, it records the reason, sends a notification, and attempts the next video. If playback ends but the platform has not recorded completion, it refreshes the page and checks again after one hour while continuing with other videos. A status notification is sent every six hours, and an exit report is sent when the queue finishes.

The dedicated Chrome window is no longer brought to the front while videos play: it stays where you put it and progress is reported in the terminal. Only “打开登录窗口” raises it, because the QR code has to be scanned. The launch flags also turn off Chrome's throttling of background and occluded windows, so the platform's own progress reporting is not slowed down.

## 4. Feishu notifications and privacy

The source code, README files, installer, and commit history contain no personal webhook, password, cookie, or login information. The existing local `codex-feishu-notify` helper is preferred; its secret is never copied into this repository. macOS looks for `~/.local/bin/codex-feishu-notify`; Windows looks for `codex-feishu-notify.cmd`, `.bat`, or `.exe` under `%USERPROFILE%\.local\bin\`.

Other users can copy `.env.example` to `.env` and put their own Feishu bot HTTPS webhook there before launching. Real `.env` files are ignored by `.gitignore` and must not be committed. You can also set `COURSEPLAYER_FEISHU_WEBHOOK` or `COURSEPLAYER_NOTIFY_COMMAND` to use your own notification transport. Webhook destinations are restricted to official Feishu/Lark HTTPS domains.

Runtime state is stored in:

- macOS: `~/Library/Application Support/CoursePlayer/`, with user-only permissions.
- Windows: `%LOCALAPPDATA%\CoursePlayer\`, protected by the user profile ACL (Windows has no chmod bits).

Both locations hold `state.json`, `settings.json`, `last-result.json`, and the dedicated Chrome profile. Login state stays in that dedicated profile; the program never reads the user's everyday Chrome profile.

## 5. Development and tests

macOS:

```bash
cd 帮你刷
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest -v test_core.py
./.venv/bin/python app.py
```

Windows:

```powershell
cd D:\path\to\bang-ni-shua
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v test_core.py
.\.venv\Scripts\python.exe cli.py status
```

All cross-platform differences live in `platform_support.py` (data directory, Chrome paths, detached process flags, Chrome background flags, notification command parsing, sleep blocking); `core.py` and `browser.py` contain no system branches.

The project uses Playwright for Python and controls the installed Google Chrome browser, so `playwright install` is not needed. Whether a video is finally counted by Rain Classroom is determined by the platform's own displayed status; the program lists completed, blocked, and unrecorded-after-recheck videos separately.
