# Rain Classroom Video Assistant (macOS)

This is a local Python GUI application designed for macOS. It uses a dedicated Google Chrome profile to operate the Changjiang Rain Classroom web player and plays opened, unfinished videos from a selected course. It only uses normal web-page playback and reads the progress shown by the platform; it does not forge learning records through private APIs and does not answer check-ins, questions, quizzes, or assignments.

Language: [简体中文](README.md) · [English](README.en.md)

## 1. macOS, hardware, and software requirements

### Minimum

- macOS 13 Ventura or later (Chrome's current Mac support range)
- A 64-bit Intel Mac or Apple Silicon (M1)
- 8 GB RAM and at least 2 GB of free disk space
- A reliable network connection; keep the Mac awake and do not close the lid while running
- Google Chrome; Python 3.11 or later, or the bundled Python installer in this repository

### Recommended

- macOS 14 Sonoma or later
- Apple Silicon M1/M2/M3/M4, or a quad-core-or-better Intel processor
- 16 GB RAM, at least 5 GB of free SSD space, and AC power
- The latest stable Google Chrome and Python 3.14.7 universal2

The repository includes the official Python 3.14.7 macOS universal2 installer (about 75 MB) at `installers/python-3.14.7-macos11.pkg`, together with its SHA-256 checksum. On a clean Mac, double-click `安装环境.command`: it verifies and installs Python, creates a virtual environment, installs dependencies, and launches the app. Installing Python may ask for an administrator password.

## 2. Official Chrome installation page

If Chrome is not installed, use the [official Google Chrome installation page](https://www.google.com/chrome/). After installation, place Chrome at `/Applications/Google Chrome.app`, then run `安装环境.command` or open `雨课堂视频助手.app`.

## 3. How to use

1. Double-click `启动雨课堂助手.command`, or open `雨课堂视频助手.app`.
2. Click “打开登录窗口” and complete WeChat QR-code login in the dedicated Chrome window.
3. Enter a course name, click “查找课程”, and choose the exact class.
4. Click “核对视频列表”, review the list, then click “开始 / 从未完成处继续”.

The app plays videos at normal speed in order. If it encounters an unrecognized dialog, in-video question, quiz, or a stuck page, it records the reason, sends a notification, and attempts the next video. If playback ends but the platform has not recorded completion, it refreshes the page and checks again after one hour while continuing with other videos. A status notification is sent every six hours, and an exit report is sent when the queue finishes.

## 4. Feishu notifications and privacy

The source code, README files, installer, and commit history contain no personal webhook, password, cookie, or login information. On the author's Mac, the existing `codex-feishu-notify` helper is preferred; its secret is never copied into this repository.

Other users can copy `.env.example` to `.env` and put their own Feishu bot HTTPS webhook there before launching the app. Real `.env` files are ignored by `.gitignore` and must not be committed. You can also set `COURSEPLAYER_FEISHU_WEBHOOK` or `COURSEPLAYER_NOTIFY_COMMAND` to use your own notification transport. Webhook destinations are restricted to official Feishu/Lark HTTPS domains.

Runtime state is stored in `~/Library/Application Support/CoursePlayer/` with user-only permissions. Login state is kept in the dedicated Chrome profile; the app does not read the user's everyday Chrome profile.

## 5. Development and tests

```bash
cd course-player
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest -v test_core.py
./.venv/bin/python app.py
```

The project uses Playwright for Python and controls the installed Google Chrome browser. Whether a video is finally counted by Rain Classroom is determined by the platform's own displayed status; the app lists completed, blocked, and unrecorded-after-recheck videos separately.
