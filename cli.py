#!/usr/bin/env python3
"""帮你刷 — 命令行版本（Windows / macOS）。

只通过网页操作播放器和读取平台显示的进度，不调用接口伪造学习记录，也不会
填写签到、弹题、测验或作业。图形界面版本仍是 app.py。

用法：
  python cli.py                       交互式向导（等同双击启动脚本）
  python cli.py login                 打开专用 Chrome 窗口扫码登录
  python cli.py courses [关键字]       列出匹配的课程班级
  python cli.py videos [关键字]        核对视频列表
  python cli.py run [关键字] [--speed N]  播放已开放且未完成的视频（N 为倍速，1 为原速）
  python cli.py status                查看本地保存的进度和上次结果

退出码：0 已开放视频全部完成 · 1 仍有未完成项 · 2 手动停止 · 3 受阻或出错。
"""
from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import threading
import time
import unicodedata
from datetime import datetime

from browser import SPEED_RANGE, BrowserAdapter, normalize, normalize_speed
from core import (APP_DIR, STATUS, Blocked, Engine, GlobalBlock, Notifier, Store,
                  atomic_json, read_json)
from platform_support import IS_WINDOWS, SleepInhibitor, chrome_path

VERSION = "1.0.0"
PAUSE_KEYS = {"p", "pause", "暂停", "c", "continue", "继续", "回车", ""}
STOP_KEYS = {"q", "quit", "stop", "s", "退出", "停止"}


def configure_console() -> None:
    """Force UTF-8 so Chinese text survives Windows code pages and redirection."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def clock_text(seconds: float) -> str:
    return f"{int(seconds) // 3600:02}:{int(seconds) // 60 % 60:02}:{int(seconds) % 60:02}"


def display_width(text: str) -> int:
    """Terminal columns used by text; Chinese characters occupy two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


class Reporter:
    """Line based progress output; one self-updating line on a real terminal."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.progress_dirty = False
        self.last_status = ""
        self.last_status_time = 0.0

    def write(self, text: str) -> None:
        if self.progress_dirty:
            self.stream.write("\r" + " " * 80 + "\r")
            self.progress_dirty = False
        self.stream.write(text + "\n")
        self.stream.flush()

    def emit(self, kind, value) -> None:
        if kind == "log":
            self.write(f"{stamp()}  {value}")
        elif kind == "error":
            self.write(f"{stamp()}  ！{value}")
        elif kind == "snapshot":
            self.snapshot(value)
        elif kind == "progress":
            self.progress(value)
        elif kind == "finished":
            self.write("")
            self.write(str(value))

    def snapshot(self, value) -> None:
        counts = {}
        for video in value["videos"]:
            counts[video["status"]] = counts.get(video["status"], 0) + 1
        text = (f"{value['phase']} · 当前：{value['current']} · "
                f"已完成 {counts.get('completed', 0)}/{len(value['videos'])} · "
                f"待播放 {counts.get('queued', 0)} · 待复查 {counts.get('pending', 0)} · "
                f"受阻 {counts.get('blocked', 0)} · 未记入 {counts.get('unrecorded', 0)}")
        now = time.monotonic()
        if text != self.last_status or now - self.last_status_time > 120:
            self.last_status, self.last_status_time = text, now
            self.write(f"{stamp()}  {text}")

    def progress(self, value) -> None:
        if not self.interactive:
            return
        duration, position = value["duration"], value["position"]
        percent = 100 * position / duration if duration else 0
        line = f"  播放 {clock_text(position)} / {clock_text(duration)}  {percent:5.1f}%"
        self.stream.write("\r" + line + " " * max(0, 72 - display_width(line)))
        self.stream.flush()
        self.progress_dirty = True


class ConsoleControl(threading.Thread):
    """Reads p / q from the console while a run is active."""

    def __init__(self, stop_event, pause_event):
        super().__init__(daemon=True, name="console-control")
        self.stop_event, self.pause_event = stop_event, pause_event
        self.done = threading.Event()
        self.enabled = bool(getattr(sys.stdin, "isatty", lambda: False)())

    def run(self) -> None:
        if not self.enabled:
            return
        while not self.done.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:
                return
            key = line.strip().casefold()
            if key in STOP_KEYS:
                self.pause_event.clear()
                self.stop_event.set()
                print("已请求停止，正在保存进度……")
            elif key in PAUSE_KEYS:
                if self.pause_event.is_set():
                    self.pause_event.clear()
                    print("继续播放。")
                else:
                    self.pause_event.set()
                    print("已请求暂停，当前页面操作结束后生效（再按回车继续）。")

    def start_listening(self) -> None:
        if self.enabled:
            self.start()

    def stop(self) -> None:
        self.done.set()


def install_sigint(stop_event, pause_event) -> None:
    """First Ctrl+C stops gracefully; a second one leaves immediately."""
    state = {"count": 0}

    def handler(signum, frame):
        state["count"] += 1
        if state["count"] == 1:
            pause_event.clear()
            stop_event.set()
            print("\n已请求停止，正在保存进度……（再按一次 Ctrl+C 立即退出）")
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        pass


def load_settings() -> dict:
    return load_json(APP_DIR / "settings.json", {}) or {}


def save_settings(**updates) -> None:
    """Merge settings so values written by other entry points survive."""
    settings = load_settings()
    settings.update(updates)
    atomic_json(APP_DIR / "settings.json", settings)


def resolve_speed(value=None) -> float:
    """Requested speed, else the saved one, else normal speed."""
    if value is None:
        value = load_settings().get("speed", 1.0)
    return normalize_speed(value)


def speed_notice(speed: float) -> str:
    if speed == 1:
        return "播放倍速：x1（原速）"
    return (f"播放倍速：x{speed:g}；平台可能不把加速后的时长计入进度，"
            f"未计入的项目会在一小时后复查并提示。")


def load_json(path, default):
    """Read a local JSON file, complaining instead of crashing when it is broken."""
    try:
        return read_json(path, default)
    except (ValueError, OSError):
        print(f"！本地文件无法读取，已忽略：{path}")
        return default


def drain_notifications(notifier, timeout: float = 35.0) -> None:
    """Give queued Feishu messages a chance to leave before the process exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with notifier.lock:
            pending = any(not item["sent"] and item["attempts"] < 3 for item in notifier.items)
        if not pending:
            return
        time.sleep(0.5)
    print("！仍有飞书通知未发送，已保存在通知队列中，稍后可重新运行。")


def print_videos(videos) -> None:
    total = len(videos)
    done = sum(v.status == "completed" for v in videos)
    print(f"共 {total} 个视频，平台已完成 {done} 个：")
    for index, video in enumerate(videos, 1):
        detail = f"　{video.reason}" if video.reason else ""
        print(f"  {index:>3}. [{STATUS.get(video.status, video.status)}] {video.name}{detail}")


def choose_course(courses, class_detail: str = "", index: int = 0):
    """Filter the matches by class name or pick one explicitly."""
    if class_detail:
        courses = [c for c in courses
                   if normalize(class_detail) in normalize(c.detail)
                   or normalize(class_detail) in normalize(c.name)]
    if index:
        if not 1 <= index <= len(courses):
            print(f"课程序号超出范围：当前有 {len(courses)} 个匹配结果。")
            return None
        return courses[index - 1]
    if len(courses) == 1:
        return courses[0]
    print("匹配到多个课程班级，请用 --class 班级名 或 --index 序号 指定一个：")
    for number, course in enumerate(courses, 1):
        print(f"  {number}. {course.name} · {course.detail}")
    return None


def scan_courses(adapter, name: str, class_detail: str = "", index: int = 0):
    """Find the course to work on, or None after explaining what is missing."""
    courses = adapter.scan_courses(name)
    if not courses:
        print(f"没有找到名称包含“{name}”的课程班级，请修改关键字后重试。")
        return None
    return choose_course(courses, class_detail, index)


def report_blocked(exc: Exception) -> int:
    """Print a blocked reason, plus the login hint when that is the cause."""
    print(f"！{exc}")
    if isinstance(exc, GlobalBlock) and "登录" in str(exc):
        print("提示：先运行 python cli.py login 完成扫码登录，再重试。")
    return 3


def build_engine(adapter, notifier, emit, stop_event, pause_event) -> Engine:
    engine = Engine(adapter, Store(), notifier, emit, stop_event, pause_event)
    adapter.cancel = engine.tick
    return engine


def run_engine(engine, adapter, course) -> int:
    """Run the queue with console control, keeping the machine awake meanwhile."""
    inhibitor = SleepInhibitor()
    control = ConsoleControl(engine.stop, engine.pause)
    install_sigint(engine.stop, engine.pause)
    if control.enabled:
        print("运行中可用：回车 暂停/继续 · q 停止 · Ctrl+C 停止")
    inhibitor.start()
    control.start_listening()
    try:
        engine.run(course)
    finally:
        control.stop()
        inhibitor.stop()
        adapter.cancel = lambda: None
    if engine.phase == "任务受阻":
        return 3
    if engine.phase == "手动停止":
        return 2
    if engine.videos and all(v.status in {"completed", "locked"} for v in engine.videos):
        return 0
    return 1


def command_login(args) -> int:
    reporter = Reporter()
    adapter, notifier = BrowserAdapter(reporter.emit), Notifier(reporter.emit)
    try:
        adapter.login()
        print("请在打开的专用 Chrome 窗口中微信扫码登录。")
        print("登录完成后运行：python cli.py run 课程关键字")
        return 0
    except Blocked as exc:
        return report_blocked(exc)
    finally:
        adapter.disconnect()
        notifier.close()


def command_courses(args) -> int:
    name = args.name or load_settings().get("course_name", "")
    if not name:
        print("请提供课程名称关键字，例如：python cli.py courses 英语")
        return 3
    reporter, adapter, notifier = Reporter(), None, None
    try:
        adapter, notifier = BrowserAdapter(reporter.emit), Notifier(reporter.emit)
        courses = adapter.scan_courses(name)
        if not courses:
            print(f"没有找到名称包含“{name}”的课程班级。")
            return 1
        save_settings(course_name=name)
        for number, course in enumerate(courses, 1):
            print(f"{number}. {course.name} · {course.detail}")
        return 0
    except Blocked as exc:
        return report_blocked(exc)
    finally:
        if adapter:
            adapter.disconnect()
        if notifier:
            notifier.close()


def command_videos(args) -> int:
    name = args.name or load_settings().get("course_name", "")
    if not name:
        print("请提供课程名称关键字，例如：python cli.py videos 英语")
        return 3
    reporter, adapter, notifier = Reporter(), None, None
    try:
        adapter, notifier = BrowserAdapter(reporter.emit), Notifier(reporter.emit)
        course = scan_courses(adapter, name, args.class_detail, args.index)
        if course is None:
            return 1
        save_settings(course_name=name)
        print(f"课程：{course.name} · {course.detail}")
        print_videos(adapter.list_videos(course))
        return 0
    except Blocked as exc:
        return report_blocked(exc)
    finally:
        if adapter:
            adapter.disconnect()
        if notifier:
            notifier.close()


def command_run(args) -> int:
    name = args.name or load_settings().get("course_name", "")
    if not name:
        print("请提供课程名称关键字，例如：python cli.py run 英语")
        return 3
    speed = resolve_speed(args.speed)
    reporter = Reporter()
    adapter = BrowserAdapter(reporter.emit, speed=speed)
    notifier = Notifier(reporter.emit)
    try:
        course = scan_courses(adapter, name, args.class_detail, args.index)
        if course is None:
            return 1
        save_settings(course_name=name, speed=speed)
        print(f"课程：{course.name} · {course.detail}")
        videos = adapter.list_videos(course)
        print_videos(videos)
        if not args.yes:
            print(speed_notice(speed))
            answer = ask("确认开始播放？", "y")
            if answer is None or answer.strip().casefold() not in {"y", "yes", "是", "确认", "开始"}:
                print("已取消。")
                return 0
        elif speed != 1:
            print(speed_notice(speed))
        engine = build_engine(adapter, notifier, reporter.emit, threading.Event(),
                              threading.Event())
        print("开始播放；重复运行同一课程时会从未完成处继续。")
        code = run_engine(engine, adapter, course)
        print(f"结束：{engine.phase}（退出码 {code}）")
        drain_notifications(notifier)
        return code
    except Blocked as exc:
        return report_blocked(exc)
    finally:
        adapter.disconnect()
        notifier.close()


def command_status(args) -> int:
    print(f"数据目录：{APP_DIR}")
    print(f"默认倍速：x{resolve_speed():g}")
    state = load_json(APP_DIR / "state.json", {"courses": {}})
    if not state.get("courses"):
        print("还没有保存的进度；先运行 python cli.py run 课程关键字")
    for entry in state.get("courses", {}).values():
        course = entry.get("course", {})
        videos = entry.get("videos", [])
        counts = {}
        for video in videos:
            counts[video["status"]] = counts.get(video["status"], 0) + 1
        updated = datetime.fromtimestamp(entry.get("updated_at", 0)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n课程：{course.get('name', '—')} · {course.get('detail', '')}")
        print(f"更新：{updated}　阶段：{entry.get('phase', '—')}")
        print(f"已完成 {counts.get('completed', 0)}/{len(videos)}　待播放 {counts.get('queued', 0)}"
              f"　待复查 {counts.get('pending', 0)}　受阻 {counts.get('blocked', 0)}"
              f"　未记入 {counts.get('unrecorded', 0)}　未开放 {counts.get('locked', 0)}")
        for video in videos:
            if video["status"] in {"blocked", "unrecorded", "unknown", "pending"}:
                detail = video.get("reason") or STATUS.get(video["status"], video["status"])
                print(f"  · {video['name']}：{detail}")
    last = load_json(APP_DIR / "last-result.json", {})
    if last:
        when = datetime.fromtimestamp(last.get("time", 0)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n上次退出结果（{when}）：")
        print(last.get("result", ""))
    return 0


def command_doctor(args) -> int:
    """Report what the command line version sees, for troubleshooting."""
    print(f"Python：{sys.version.split()[0]}　{sys.executable}")
    print(f"平台：{platform.platform()}")
    print(f"数据目录：{APP_DIR}（存在={APP_DIR.exists()}）")
    print(f"标准输入：isatty={sys.stdin.isatty()} encoding={getattr(sys.stdin, 'encoding', '?')}"
          f"　标准输出：isatty={sys.stdout.isatty()} encoding={getattr(sys.stdout, 'encoding', '?')}")
    if IS_WINDOWS:
        try:
            import ctypes
            print(f"控制台代码页：输入={ctypes.windll.kernel32.GetConsoleCP()}"
                  f"　输出={ctypes.windll.kernel32.GetConsoleOutputCP()}")
        except Exception:
            pass
    print(f"Chrome：{chrome_path() or '未找到'}")
    print(f"设置：{load_settings()}")
    webhook = "已设置" if os.environ.get("COURSEPLAYER_FEISHU_WEBHOOK") else "未设置"
    command = "已设置" if os.environ.get("COURSEPLAYER_NOTIFY_COMMAND") else "未设置"
    print(f"环境变量：PYTHONUTF8={os.environ.get('PYTHONUTF8', '')}"
          f"　飞书 webhook={webhook}　通知命令={command}")
    if not args.input_probe:
        return 0
    print("正在检测键盘输入（3 秒内没有回车就说明能正常等待输入）……")
    result = {}

    def probe():
        try:
            result["value"] = input()
        except EOFError:
            result["error"] = "输入流已结束（EOF）"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(3)
    if thread.is_alive():
        print("输入正常：程序会等待你按回车。")
        return 0
    print(f"！输入不可用：{result.get('error', result.get('value'))}")
    return 1


def ask(question: str, default: str = "") -> str | None:
    """Prompt once; None means the user cancelled or input ended."""
    suffix = f"（回车 = {default}）" if default else ""
    try:
        answer = input(f"{question}{suffix}：").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        print()
        return None
    return answer or default


def home_screen(name: str, speed: float) -> None:
    """Show what the next run would use, before anything touches the browser."""
    print()
    print(f"课程关键字：{name or '（未设置）'}　·　倍速：x{speed:g}")
    last = load_json(APP_DIR / "last-result.json", {})
    if last:
        when = datetime.fromtimestamp(last.get("time", 0)).strftime("%Y-%m-%d %H:%M")
        lines = str(last.get("result", "")).splitlines()
        phase = next((line for line in lines if line.startswith("状态：")), "")
        counts = next((line for line in lines if line.startswith("已完成")), "")
        print(f"上次运行：{when}　{phase}　{counts}")
    print("请选择：输入序号后按回车（直接回车 = 1）")
    print("  1  开始 / 继续播放")
    print("  2  更换课程")
    print(f"  3  更改倍速（当前 x{speed:g}，范围 {SPEED_RANGE[0]:g}–{SPEED_RANGE[1]:g}）")
    print("  4  查看本地进度和上次结果")
    print("  0  退出")


def choose_start_options(args):
    """The menu behind the double-click launcher; None means the user left."""
    name = str(load_settings().get("course_name", "") or "")
    speed = resolve_speed()
    while True:
        home_screen(name, speed)
        choice = ask("请输入序号", "1")
        if choice is None:
            return None
        choice = choice.strip().casefold()
        if choice in {"0", "q", "退出", "exit"}:
            return None
        if choice in {"2", "课程"}:
            typed = ask("新的课程名称关键字", name)
            if typed is None:
                return None
            name = typed.strip() or name
        elif choice in {"3", "倍速"}:
            typed = ask(f"播放倍速（{SPEED_RANGE[0]:g}–{SPEED_RANGE[1]:g}，1 为原速）", f"{speed:g}")
            if typed is None:
                return None
            speed = normalize_speed(typed)
            save_settings(speed=speed)
            print(f"倍速已保存：x{speed:g}")
        elif choice in {"4", "进度", "状态"}:
            command_status(args)
        elif choice in {"1", "开始", ""}:
            return name, speed
        else:
            print("请输入 0–4 之间的序号。")


def command_wizard(args) -> int:
    """Interactive menu for the double-click launcher."""
    if not sys.stdin.isatty():
        print("！这个窗口不能输入文字，请改用带参数的运行方式，例如：")
        print("   .\\.venv\\Scripts\\python.exe cli.py run 课程关键字 --speed 1.5")
        return 3
    print("帮你刷 · 命令行向导（Ctrl+C 退出；如果键盘输入没有反应，在窗口里按一下 Esc）")
    chosen = choose_start_options(args)
    if chosen is None:
        print("已退出。")
        return 0
    name, speed = chosen
    if not name:
        print("还没有课程关键字：请重新运行向导，选择 2 更换课程。")
        return 0
    reporter = Reporter()
    adapter = BrowserAdapter(reporter.emit, speed=speed)
    notifier = Notifier(reporter.emit)
    try:
        courses = None
        for attempt in range(2):
            try:
                courses = adapter.scan_courses(name)
                break
            except GlobalBlock as exc:
                report_blocked(exc)
                if attempt or ask("是否现在打开登录窗口？", "y") not in {"y", "yes", "是"}:
                    return 3
                adapter.login()
                ask("在专用 Chrome 窗口完成扫码登录后按回车继续")
        if not courses:
            print(f"没有找到名称包含“{name}”的课程班级；重新运行向导可以选 2 更换课程。")
            return 1
        save_settings(course_name=name)
        if len(courses) == 1:
            course = courses[0]
            print(f"课程：{course.name} · {course.detail}")
        else:
            print("找到多个课程班级：")
            for number, item in enumerate(courses, 1):
                print(f"  {number}. {item.name} · {item.detail}")
            picked = ask("请输入序号", "1")
            if picked is None or not picked.isdigit() or not 1 <= int(picked) <= len(courses):
                print("已取消。")
                return 0
            course = courses[int(picked) - 1]
        print()
        print_videos(adapter.list_videos(course))
        print(speed_notice(speed))
        answer = ask("确认开始播放？", "y")
        if answer is None or answer.strip().casefold() not in {"y", "yes", "是", "确认", "开始"}:
            print("已取消。")
            return 0
        engine = build_engine(adapter, notifier, reporter.emit, threading.Event(),
                              threading.Event())
        code = run_engine(engine, adapter, course)
        print(f"结束：{engine.phase}（退出码 {code}）")
        drain_notifications(notifier)
        return code
    finally:
        adapter.disconnect()
        notifier.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python cli.py", description="帮你刷 命令行版本：按课程目录播放已开放且未完成的视频。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带子命令运行会进入交互式向导。退出码：0 全部完成 · 1 仍有未完成 · 2 手动停止 · 3 受阻或出错。")
    parser.add_argument("--version", action="version", version=f"帮你刷 {VERSION}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="打开专用 Chrome 窗口扫码登录")
    courses = sub.add_parser("courses", help="列出匹配的课程班级")
    courses.add_argument("name", nargs="?", default="", help="课程名称关键字，默认用上次保存的名称")
    videos = sub.add_parser("videos", help="核对视频列表，不播放")
    videos.add_argument("name", nargs="?", default="", help="课程名称关键字，默认用上次保存的名称")
    videos.add_argument("--class", dest="class_detail", default="", help="班级名称关键字，用于排除同名课程")
    videos.add_argument("--index", type=int, default=0, help="直接选择第几个匹配班级（从 1 开始）")
    run = sub.add_parser("run", help="播放已开放且未完成的视频")
    run.add_argument("name", nargs="?", default="", help="课程名称关键字，默认用上次保存的名称")
    run.add_argument("--class", dest="class_detail", default="", help="班级名称关键字，用于排除同名课程")
    run.add_argument("--index", type=int, default=0, help="直接选择第几个匹配班级（从 1 开始）")
    run.add_argument("--yes", action="store_true", help="不询问，直接开始（适合计划任务）")
    run.add_argument("--speed", type=float, default=None,
                     help=f"播放倍速，1 为原速（{SPEED_RANGE[0]:g}–{SPEED_RANGE[1]:g}，默认沿用上次保存的值）")
    sub.add_parser("status", help="查看本地保存的进度和上次结果")
    doctor = sub.add_parser("doctor", help="显示运行环境信息，排查输入、浏览器等问题")
    doctor.add_argument("--input", dest="input_probe", action="store_true",
                        help="顺便检测当前窗口能不能读取键盘输入")
    return parser


def main(argv=None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers = {"login": command_login, "courses": command_courses,
                "videos": command_videos, "run": command_run,
                "status": command_status, "doctor": command_doctor}
    handler = handlers.get(args.command, command_wizard)
    if args.command is None:
        args.name = ""
        args.class_detail = ""
        args.index = 0
        args.yes = False
        args.speed = None
        args.input_probe = False
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 2


if __name__ == "__main__":
    sys.exit(main())
