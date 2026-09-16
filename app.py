#!/usr/bin/env python3
"""帮你刷 — run with .venv/bin/python app.py."""
from __future__ import annotations

import fcntl
import queue
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from browser import BrowserAdapter
from core import APP_DIR, STATUS, Blocked, Engine, Notifier, Store, atomic_json, read_json


class Worker(threading.Thread):
    def __init__(self, emit, notifier):
        super().__init__(daemon=True, name="browser-worker")
        self.emit, self.notifier = emit, notifier
        self.commands = queue.Queue()
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        self.active = threading.Event()

    def run(self):
        adapter = BrowserAdapter(self.emit)
        while True:
            command, value = self.commands.get()
            if command == "quit":
                adapter.disconnect()
                return
            self.active.set()
            inhibitor = None
            try:
                if command == "login":
                    adapter.login()
                elif command == "scan":
                    self.emit("courses", adapter.scan_courses(value))
                elif command == "preview":
                    videos = adapter.list_videos(value)
                    self.emit("preview", {"course": value, "videos": videos})
                elif command == "start":
                    # Prevent idle sleep only while a task is active. This does
                    # not override lid close, shutdown, or explicit system sleep.
                    inhibitor = subprocess.Popen(["/usr/bin/caffeinate", "-di", "-w", str(__import__('os').getpid())],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    engine = Engine(adapter, Store(), self.notifier, self.emit,
                                    self.stop_event, self.pause_event)
                    adapter.cancel = engine.tick
                    engine.run(value)
            except Exception as exc:
                detail = str(exc) if isinstance(exc, Blocked) else f"操作失败（{type(exc).__name__}），请重新打开浏览器或重试。"
                self.emit("error", detail)
            finally:
                adapter.cancel = lambda: None
                if inhibitor:
                    inhibitor.terminate()
                self.active.clear()
                self.emit("idle", command)


class App:
    def __init__(self, root):
        self.root = root
        root.title("帮你刷")
        root.geometry("1050x780")
        root.minsize(840, 650)
        root.configure(bg="#f4f6fa")
        self.events = queue.Queue()
        self.closing = False
        self.busy = False
        self.running = False
        self.courses = []
        self.result = ""
        self.settings = read_json(APP_DIR / "settings.json", {"course_name": "英语（博士）"})
        self.course_name = tk.StringVar(value=self.settings["course_name"])
        self.phase = tk.StringVar(value="准备就绪")
        self.current = tk.StringVar(value="选择课程后开始")
        self.counts = tk.StringVar(value="视频完成情况将显示在这里")
        self.position = tk.StringVar(value="00:00 / 00:00")
        self._build()
        self.notifier = Notifier(self.emit)
        self.worker = Worker(self.emit, self.notifier)
        self.worker.start()
        last = read_json(APP_DIR / "last-result.json", {})
        if last:
            self.result = last.get("result", "")
            self.log("上次结果已保存，可点击“导出结果”查看。")
        self.log("运行规则：正常倍速 · 一小时进度复查 · 每六小时飞书状态 · 异常及退出结果通知")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def _build(self):
        style = ttk.Style()
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", font=("PingFang SC", 12))
        style.configure("Title.TLabel", font=("PingFang SC", 25, "bold"), foreground="#14243b")
        style.configure("Sub.TLabel", foreground="#637188", font=("PingFang SC", 11))
        style.configure("State.TLabel", foreground="#2563c7", font=("PingFang SC", 15, "bold"))
        style.configure("Treeview", rowheight=30, font=("PingFang SC", 11))
        style.configure("Treeview.Heading", font=("PingFang SC", 11, "bold"))
        main = ttk.Frame(self.root, padding=24)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="帮你刷", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="选择课程，依次播放已开放的视频。进度和结果随时可见。", style="Sub.TLabel").pack(anchor="w", pady=(3, 18))
        search = ttk.Frame(main)
        search.pack(fill="x")
        ttk.Label(search, text="课程名称").pack(side="left", padx=(0, 10))
        self.entry = ttk.Entry(search, textvariable=self.course_name, font=("PingFang SC", 13))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _: self.search())
        self.login_button = ttk.Button(search, text="打开登录窗口", command=lambda: self.dispatch("login", None))
        self.login_button.pack(side="left", padx=(12, 8))
        self.search_button = ttk.Button(search, text="查找课程", command=self.search)
        self.search_button.pack(side="left")
        self.selection = ttk.Combobox(main, state="readonly", font=("PingFang SC", 12))
        self.selection.pack(fill="x", pady=(12, 12))
        buttons = ttk.Frame(main)
        buttons.pack(fill="x")
        self.preview_button = ttk.Button(buttons, text="核对视频列表", command=self.preview, state="disabled")
        self.preview_button.pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(buttons, text="开始 / 从未完成处继续", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(buttons, text="暂停", command=self.pause, state="disabled")
        self.pause_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Button(buttons, text="导出结果", command=self.export).pack(side="right")
        ttk.Separator(main).pack(fill="x", pady=18)
        ttk.Label(main, textvariable=self.phase, style="State.TLabel").pack(anchor="w")
        ttk.Label(main, textvariable=self.current, wraplength=930).pack(anchor="w", pady=(6, 4))
        progress_row = ttk.Frame(main)
        progress_row.pack(fill="x", pady=5)
        self.progress = ttk.Progressbar(progress_row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_row, textvariable=self.position, style="Sub.TLabel", width=20, anchor="e").pack(side="right")
        ttk.Label(main, textvariable=self.counts, style="Sub.TLabel").pack(anchor="w", pady=(3, 12))
        table = ttk.Frame(main)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("name", "status", "detail"), show="headings", height=8)
        for key, title, width in [("name", "视频", 380), ("status", "状态", 135), ("detail", "说明 / 复查时间", 390)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=70, stretch=key != "status")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tree.tag_configure("completed", foreground="#137957")
        self.tree.tag_configure("blocked", foreground="#b35024")
        self.tree.tag_configure("unrecorded", foreground="#b35024")
        self.tree.tag_configure("playing", foreground="#2563c7")
        self.logbox = scrolledtext.ScrolledText(main, height=5, font=("Menlo", 10), wrap="word", state="disabled", relief="flat", background="#eaf0f7")
        self.logbox.pack(fill="x", pady=(14, 8))
        footer = ttk.Frame(main)
        footer.pack(fill="x")
        ttk.Label(footer, text="运行期间请保持 Mac 开机联网；关闭本窗口会停止任务。", style="Sub.TLabel").pack(side="left")
        ttk.Button(footer, text="重试失败的飞书通知", command=lambda: self.notifier.retry_failed()).pack(side="right")

    def emit(self, kind, value):
        self.events.put((kind, value))

    def log(self, text):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + text + "\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > 500:
            self.logbox.delete("1.0", "100.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def controls(self):
        for button in (self.login_button, self.search_button):
            button.configure(state="disabled" if self.busy else "normal")
        self.entry.configure(state="disabled" if self.busy else "normal")
        self.selection.configure(state="disabled" if self.busy else "readonly")
        for button in (self.preview_button, self.start_button):
            button.configure(state="normal" if self.courses and not self.busy else "disabled")
        self.pause_button.configure(state="normal" if self.running else "disabled")
        self.stop_button.configure(state="normal" if self.running else "disabled")

    def dispatch(self, command, value):
        if self.busy:
            return
        self.busy = True
        self.running = command == "start"
        self.worker.stop_event.clear()
        self.worker.pause_event.clear()
        self.pause_button.configure(text="暂停")
        self.phase.set({"login": "打开浏览器", "scan": "查找课程", "preview": "读取视频目录", "start": "准备播放"}[command])
        self.controls()
        self.worker.commands.put((command, value))

    def search(self):
        name = self.course_name.get().strip()
        if not name:
            self.log("请输入课程名称。")
            return
        # Keep settings written elsewhere, such as the command line speed.
        atomic_json(APP_DIR / "settings.json", {**self.settings, "course_name": name})
        self.courses = []
        self.selection.set("")
        self.dispatch("scan", name)

    def selected(self):
        index = self.selection.current()
        if index < 0 or index >= len(self.courses):
            self.log("请先选择一个匹配的课程班级。")
            return None
        return self.courses[index]

    def preview(self):
        if course := self.selected():
            self.dispatch("preview", course)

    def start(self):
        if course := self.selected():
            self.result = ""
            self.progress["value"] = 0
            self.dispatch("start", course)

    def pause(self):
        if self.worker.pause_event.is_set():
            self.worker.pause_event.clear()
            self.pause_button.configure(text="暂停")
            self.log("继续执行。")
        else:
            self.worker.pause_event.set()
            self.pause_button.configure(text="继续")
            self.log("已请求暂停，当前页面操作结束后生效。")

    def stop(self):
        self.worker.stop_event.set()
        self.worker.pause_event.clear()
        self.phase.set("正在停止并保存结果")
        self.stop_button.configure(state="disabled")

    def render_videos(self, videos):
        existing = set(self.tree.get_children())
        for video in videos:
            key = video["id"]
            detail = video["reason"]
            if video.get("recheck_at"):
                detail = "复查：" + datetime.fromtimestamp(video["recheck_at"]).strftime("%m-%d %H:%M:%S")
            values = (video["name"], STATUS.get(video["status"], video["status"]), detail)
            if key in existing:
                self.tree.item(key, values=values, tags=(video["status"],))
                existing.remove(key)
            else:
                self.tree.insert("", "end", iid=key, values=values, tags=(video["status"],))
        for key in existing:
            self.tree.delete(key)
        done = sum(v["status"] == "completed" for v in videos)
        waiting = sum(v["status"] == "pending" for v in videos)
        blocked = sum(v["status"] in {"blocked", "unrecorded"} for v in videos)
        self.counts.set(f"平台已完成 {done} / {len(videos)} 个视频　·　等待复查 {waiting}　·　需处理 {blocked}")

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(value)
            elif kind == "error":
                self.phase.set("需要处理")
                self.log(value)
            elif kind == "courses":
                self.courses = value
                self.selection["values"] = [f"{c.name}  ·  {c.detail}" for c in value]
                if len(value) == 1:
                    self.selection.current(0)
                    self.phase.set("已找到课程，可以核对列表或开始")
                elif value:
                    self.phase.set(f"找到 {len(value)} 个班级，请选择")
                else:
                    self.phase.set("没有匹配的课程，请修改名称")
                self.log(f"找到 {len(value)} 个匹配课程班级。")
            elif kind == "preview":
                from dataclasses import asdict
                self.render_videos([asdict(v) for v in value["videos"]])
                self.current.set(value["course"].name + " · " + value["course"].detail)
                self.phase.set("列表已核对，点击开始播放")
            elif kind == "snapshot":
                self.phase.set(value["phase"])
                self.current.set(value["current"])
                self.render_videos(value["videos"])
            elif kind == "progress":
                duration, position = value["duration"], value["position"]
                self.progress["value"] = 100 * position / duration if duration else 0
                def stamp(t):
                    return f"{int(t)//3600:02}:{int(t)//60%60:02}:{int(t)%60:02}"
                self.position.set(stamp(position) + " / " + stamp(duration))
            elif kind == "finished":
                self.result = value
                self.log(value.replace("\n", "；"))
            elif kind == "idle":
                self.busy, self.running = False, False
                if value == "login":
                    self.phase.set("登录后，点击查找课程")
                self.controls()
        self.root.after(150, self.poll)

    def export(self):
        if not self.result:
            self.log("还没有退出结果；运行中可在列表查看进度。")
            return
        name = filedialog.asksaveasfilename(title="保存结果", defaultextension=".txt", initialfile="帮你刷运行结果.txt", filetypes=[("文本文件", "*.txt")])
        if name:
            Path(name).write_text(self.result + "\n", encoding="utf-8")
            self.log("结果已导出。")

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.busy:
            self.stop()
        self.close_deadline = time.monotonic() + 35
        self.root.after(200, self._finish_close)

    def _finish_close(self):
        if self.busy:
            self.root.after(200, self._finish_close)
            return
        with self.notifier.lock:
            pending = any(not i["sent"] and i["attempts"] < 3 for i in self.notifier.items)
        if pending and time.monotonic() < self.close_deadline:
            self.phase.set("正在发送退出通知")
            self.root.after(200, self._finish_close)
            return
        self.worker.commands.put(("quit", None))
        self.notifier.close()
        self.root.destroy()


def main():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    APP_DIR.chmod(0o700)
    lock = (APP_DIR / "app.lock").open("w")
    root = tk.Tk()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        root.withdraw()
        messagebox.showinfo("帮你刷", "程序已经运行，请切换到现有窗口。")
        root.destroy()
        return
    try:
        App(root)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("无法启动", f"程序初始化失败（{type(exc).__name__}）。请检查本地数据文件或联系维护者。")
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
