# 帮你刷（macOS）

这是一个给 macOS 设计的本地 Python 图形程序，用专用 Google Chrome 窗口操作长江雨课堂，按课程目录播放已开放且未完成的视频。它只通过网页操作播放器和读取平台显示的进度，不调用接口伪造学习记录，也不会填写签到、弹题、测验或作业。

语言版本：[简体中文](README.md) · [English](README.en.md)

## 1. macOS、机器型号和软件要求

### 最低配置

- macOS 13 Ventura 或更高版本（Chrome 当前 Mac 支持范围）
- 64 位 Intel Mac 或 Apple Silicon（M1）
- 8 GB 内存，至少 2 GB 可用磁盘空间
- 可靠的网络连接；运行期间保持 Mac 开机并避免合盖睡眠
- Google Chrome；Python 3.11 或更高版本，或使用仓库内置的 Python 安装包

### 推荐配置

- macOS 14 Sonoma 或更高版本
- Apple Silicon M1/M2/M3/M4，或四核及以上 Intel 处理器
- 16 GB 内存，至少 5 GB 可用磁盘空间，接通电源
- 最新稳定版 Google Chrome 和 Python 3.14.7 universal2

仓库包含官方 Python 3.14.7 macOS universal2 安装包（约 75 MB），位于 `installers/python-3.14.7-macos11.pkg`，并附带 SHA-256 校验值。裸机用户双击 `安装帮你刷环境.command`，脚本会校验并安装 Python、创建虚拟环境、安装依赖，然后启动程序；安装 Python 可能需要管理员密码。

## 2. Chrome 官方安装网页

如果电脑没有 Chrome，请使用 [Google Chrome 官方安装网页](https://www.google.com/chrome/) 下载并安装。安装后把 Chrome 放在 `/Applications/Google Chrome.app`，再运行 `安装帮你刷环境.command` 或双击 `帮你刷.app`。

## 3. 使用方法

1. 双击 `启动帮你刷.command`，或双击 `帮你刷.app`。
2. 点击“打开登录窗口”，在专用 Chrome 窗口完成微信扫码登录。
3. 在程序输入课程名称，点击“查找课程”，选择具体班级。
4. 点击“核对视频列表”，确认列表后点击“开始 / 从未完成处继续”。

程序按正常倍速依次播放视频，遇到无法识别的弹窗、弹题、测验或页面卡住，会记录原因、发送通知并尝试下一节。视频播放结束但平台未记入进度时，程序会刷新页面并在一小时后复查；复查期间继续播放其他视频。运行期间每 6 小时发送一次状态，队列结束后发送退出结果。

## 4. 飞书通知和隐私

代码、README、安装包和提交历史不包含任何个人 webhook、密码、Cookie 或登录信息。本机已有的 `codex-feishu-notify` 会优先使用；仓库中没有复制它读取的密钥。

其他用户可以复制 `.env.example` 为 `.env`，填入自己的飞书机器人 HTTPS webhook，再双击启动脚本。真实 `.env` 已被 `.gitignore` 忽略，不能提交。也可以设置环境变量 `COURSEPLAYER_FEISHU_WEBHOOK`，或设置 `COURSEPLAYER_NOTIFY_COMMAND` 指向自己的通知脚本。通知地址只允许官方 Feishu/Lark HTTPS 域名。

运行状态保存在 `~/Library/Application Support/CoursePlayer/`，目录权限为当前用户私有。登录状态保存在同目录的专用 Chrome profile，不会读取用户日常 Chrome profile。

## 5. 开发和测试

```bash
cd 帮你刷
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest -v test_core.py
./.venv/bin/python app.py
```

本项目依赖 Playwright Python；浏览器控制使用已安装的 Google Chrome。课程视频是否最终计入平台，仍以雨课堂页面显示的状态为准；程序会把视频已完成、受阻和复查未记入的项目分别列出。
