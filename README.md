# 帮你刷

这是一个本地 Python 程序，用专用 Google Chrome 窗口操作长江雨课堂，按课程目录播放已开放且未完成的视频。它只通过网页操作播放器和读取平台显示的进度，不调用接口伪造学习记录，也不会填写签到、弹题、测验或作业。

两种入口：

- **macOS 图形界面版**：双击 `帮你刷.app` 或 `启动帮你刷.command`，在窗口里选课程。
- **Windows 命令行版**：双击 `启动帮你刷.cmd` 进入向导，或用 `cli.py` 的子命令按需运行。命令行版在 macOS 上同样可用。

GitHub 的仓库 URL 使用 ASCII 名称 `bang-ni-shua`（GitHub 不允许中文仓库名）；项目、应用窗口和 README 名称均为“帮你刷”。

语言版本：[简体中文](README.md) · [English](README.en.md)

## 1. 系统、机器型号和软件要求

### macOS（图形界面版）

最低配置

- macOS 13 Ventura 或更高版本（Chrome 当前 Mac 支持范围）
- 64 位 Intel Mac 或 Apple Silicon（M1）
- 8 GB 内存，至少 2 GB 可用磁盘空间
- 可靠的网络连接；运行期间保持 Mac 开机并避免合盖睡眠
- Google Chrome；Python 3.11 或更高版本，或使用仓库内置的 Python 安装包

推荐配置

- macOS 14 Sonoma 或更高版本
- Apple Silicon M1/M2/M3/M4，或四核及以上 Intel 处理器
- 16 GB 内存，至少 5 GB 可用磁盘空间，接通电源
- 最新稳定版 Google Chrome 和 Python 3.14.7 universal2

仓库包含官方 Python 3.14.7 macOS universal2 安装包（约 75 MB），位于 `installers/python-3.14.7-macos11.pkg`，并附带 SHA-256 校验值。裸机用户双击 `安装帮你刷环境.command`，脚本会校验并安装 Python、创建虚拟环境、安装依赖，然后启动程序；安装 Python 可能需要管理员密码。

### Windows（命令行版）

- Windows 10（1809 或更高）或 Windows 11，64 位
- Google Chrome（安装脚本会检测，缺失时打开官方安装页）
- Python 3.11 或更高版本；没有时安装脚本会询问是否用 winget 安装 Python 3.12
- 8 GB 内存，至少 2 GB 可用磁盘空间，运行期间保持开机联网

命令行版不使用 `tkinter`，不需要图形界面组件，可在 Windows 终端、PowerShell 或计划任务中运行。

## 2. Chrome 官方安装网页

如果电脑没有 Chrome，请使用 [Google Chrome 官方安装网页](https://www.google.com/chrome/) 下载并安装。

- macOS：把 Chrome 放在 `/Applications/Google Chrome.app`，再运行 `安装帮你刷环境.command` 或双击 `帮你刷.app`。
- Windows：使用默认安装位置即可，程序会自动在注册表和常见目录中查找 `chrome.exe`。

## 3. 使用方法

### 3.1 macOS 图形界面版

1. 双击 `启动帮你刷.command`，或双击 `帮你刷.app`。
2. 点击“打开登录窗口”，在专用 Chrome 窗口完成微信扫码登录。
3. 在程序输入课程名称，点击“查找课程”，选择具体班级。
4. 点击“核对视频列表”，确认列表后点击“开始 / 从未完成处继续”。

### 3.2 Windows 命令行版

1. 双击 `安装帮你刷环境.cmd`：检查 Python 3.11+、创建 `.venv`、安装依赖并检查 Chrome。首次安装会联网下载依赖。
2. 双击 `启动帮你刷.cmd` 进入向导：输入课程名称关键字 → 选择班级 → 核对视频列表 → 确认开始。首次使用会提示先打开登录窗口扫码。
3. 也可以在终端里按需执行子命令：

```powershell
cd D:\path\to\bang-ni-shua
.\.venv\Scripts\python.exe cli.py login          # 打开专用 Chrome 窗口扫码登录
.\.venv\Scripts\python.exe cli.py courses 英语    # 列出名称包含“英语”的课程班级
.\.venv\Scripts\python.exe cli.py videos 英语     # 核对视频列表，不播放
.\.venv\Scripts\python.exe cli.py run 英语        # 从上次未完成处继续播放
.\.venv\Scripts\python.exe cli.py status          # 查看本地保存的进度和上次结果
```

运行期间的控制：

- 回车：暂停 / 继续
- `q` 或 `s`：停止，保存进度后退出
- `Ctrl+C`：第一次优雅停止，第二次立即退出

同名课程有多个班级时，用 `--class 班级关键字` 或 `--index 序号` 指定，例如：

```powershell
.\.venv\Scripts\python.exe cli.py run 英语 --class 博士
.\.venv\Scripts\python.exe cli.py run 英语 --index 2 --yes
```

`--yes` 跳过确认，适合放进 Windows 任务计划程序定时运行；重复运行同一课程时会跳过平台已完成和尚未开放的视频。

命令行版的退出码：`0` 已开放视频全部完成 · `1` 仍有未完成项 · `2` 手动停止 · `3` 受阻或出错。

### 3.3 运行行为

程序按正常倍速依次播放视频，遇到无法识别的弹窗、弹题、测验或页面卡住，会记录原因、发送通知并尝试下一节。视频播放结束但平台未记入进度时，程序会刷新页面并在一小时后复查；复查期间继续播放其他视频。运行期间每 6 小时发送一次状态，队列结束后发送退出结果。

播放过程中程序不会再把专用 Chrome 窗口切到前台，窗口停在你放它的地方，进度显示在终端里；只有点击“打开登录窗口”需要扫码时会主动调前台。启动参数同时关闭了 Chrome 对后台、被遮挡窗口的节流，避免平台页面自己的进度上报被拖慢。

## 4. 飞书通知和隐私

代码、README、安装包和提交历史不包含任何个人 webhook、密码、Cookie 或登录信息。本机已有的 `codex-feishu-notify` 会优先使用；仓库中没有复制它读取的密钥。macOS 上会查找 `~/.local/bin/codex-feishu-notify`，Windows 上会查找 `%USERPROFILE%\.local\bin\` 下的 `codex-feishu-notify.cmd`、`.bat` 或 `.exe`。

其他用户可以复制 `.env.example` 为 `.env`，填入自己的飞书机器人 HTTPS webhook，再双击启动脚本。真实 `.env` 已被 `.gitignore` 忽略，不能提交。也可以设置环境变量 `COURSEPLAYER_FEISHU_WEBHOOK`，或设置 `COURSEPLAYER_NOTIFY_COMMAND` 指向自己的通知脚本。通知地址只允许官方 Feishu/Lark HTTPS 域名。

运行状态保存位置：

- macOS：`~/Library/Application Support/CoursePlayer/`，目录权限为当前用户私有。
- Windows：`%LOCALAPPDATA%\CoursePlayer\`，权限由用户配置文件的 ACL 保护（Windows 没有 chmod 权限位）。

两处都保存 `state.json`、`settings.json`、`last-result.json` 和专用 Chrome profile，登录状态只保存在该专用 profile 中，不会读取用户日常 Chrome profile。

## 5. 开发和测试

macOS：

```bash
cd 帮你刷
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest -v test_core.py
./.venv/bin/python app.py
```

Windows：

```powershell
cd D:\path\to\bang-ni-shua
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v test_core.py
.\.venv\Scripts\python.exe cli.py status
```

跨平台相关差异集中在 `platform_support.py`（数据目录、Chrome 路径、独立进程标志、后台运行用的 Chrome 参数、通知命令解析、阻止睡眠），`core.py` 与 `browser.py` 不含系统判断。

本项目依赖 Playwright Python；浏览器控制使用已安装的 Google Chrome，因此不需要执行 `playwright install` 下载额外浏览器。课程视频是否最终计入平台，仍以雨课堂页面显示的状态为准；程序会把视频已完成、受阻和复查未记入的项目分别列出。
