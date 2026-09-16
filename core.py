"""Persistent queue and Feishu delivery; never generates learning progress."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from platform_support import app_dir, notify_candidates, restrict, split_command

APP_DIR = app_dir()
NOTIFY_COMMAND_ENV = "COURSEPLAYER_NOTIFY_COMMAND"
FEISHU_WEBHOOK_ENV = "COURSEPLAYER_FEISHU_WEBHOOK"
RECHECK_SECONDS = 3600
HEARTBEAT_SECONDS = 6 * 3600


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    restrict(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        restrict(temporary, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def read_json(path: Path, default):
    if not path.exists():
        return default
    # Do not silently overwrite corrupt progress or a notification outbox.
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Course:
    id: str
    name: str
    url: str
    detail: str = ""


@dataclass
class Video:
    id: str
    name: str
    url: str
    status: str = "queued"
    reason: str = ""
    recheck_at: float | None = None
    position: float = 0
    duration: float = 0


STATUS = {
    "queued": "待播放", "playing": "播放中", "completed": "平台已完成",
    "pending": "等待复查", "blocked": "受阻已跳过", "unrecorded": "平台未记入",
    "locked": "尚未开放", "unknown": "完成状态待核实",
}


class Store:
    def __init__(self, path: Path = APP_DIR / "state.json"):
        self.path = path
        self.data = read_json(path, {"version": 1, "courses": {}})

    def previous(self, course_id: str):
        return self.data["courses"].get(course_id, {})

    def save(self, course: Course, videos: list[Video], phase: str, next_status: float):
        self.data["courses"][course.id] = {
            "course": asdict(course), "videos": [asdict(v) for v in videos],
            "phase": phase, "updated_at": time.time(), "next_status": next_status,
        }
        atomic_json(self.path, self.data)


class Notifier:
    """Single delivery thread, durable outbox, bounded retries, no secret output."""
    def __init__(self, emit: Callable, directory: Path = APP_DIR,
                 sender: Callable | None = None, clock: Callable = time.time):
        self.emit, self.clock = emit, clock
        self.path = directory / "notifications.json"
        self.items = read_json(self.path, [])
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.sender = sender or self._send
        self.thread = threading.Thread(target=self._loop, daemon=True, name="feishu")
        self.thread.start()

    @staticmethod
    def _send(message):
        # Prefer the user's existing local helper. It reads the webhook from
        # the system keychain and is never copied into this project.
        for candidate in notify_candidates():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                result = subprocess.run([str(candidate), message], capture_output=True, timeout=25)
                if result.returncode:
                    raise RuntimeError("飞书脚本返回失败")
                return
        # Classmates can provide their own command without changing source.
        command = os.environ.get(NOTIFY_COMMAND_ENV, "").strip()
        if command:
            result = subprocess.run(split_command(command) + [message], capture_output=True, timeout=25)
            if result.returncode:
                raise RuntimeError("通知命令返回失败")
            return
        # Or use a Feishu/Lark webhook held only in the process environment.
        # Restrict the destination to official Feishu/Lark hosts.
        webhook = os.environ.get(FEISHU_WEBHOOK_ENV, "").strip()
        if not webhook:
            raise RuntimeError("未配置飞书通知：本机无通知脚本，请设置 COURSEPLAYER_FEISHU_WEBHOOK")
        parsed = urllib.parse.urlparse(webhook)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "feishu.cn" or host.endswith(".feishu.cn") or
                                             host == "larksuite.com" or host.endswith(".larksuite.com")):
            raise RuntimeError("飞书通知地址不是受支持的官方 HTTPS 域名")
        body = json.dumps({"msg_type": "text", "content": {"text": message}},
                          ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(webhook, data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError("飞书 webhook 返回失败")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("飞书 webhook 网络失败") from exc

    def enqueue(self, message, key):
        with self.lock:
            if any(i["key"] == key for i in self.items):
                return
            self.items.append({"key": key, "message": message[:6000], "attempts": 0,
                               "due": self.clock(), "sent": False})
            atomic_json(self.path, self.items)
        self.wake.set()

    def retry_failed(self):
        with self.lock:
            for item in self.items:
                if not item["sent"] and item["attempts"] >= 3:
                    item.update(attempts=0, due=self.clock())
            atomic_json(self.path, self.items)
        self.wake.set()

    def deliver_due(self):
        with self.lock:
            item = next((i for i in self.items if not i["sent"] and
                         i["attempts"] < 3 and i["due"] <= self.clock()), None)
            if item is None:
                return False
            message = item["message"]
        try:
            self.sender(message)
        except Exception:
            with self.lock:
                item["attempts"] += 1
                item["due"] = self.clock() + 60 * item["attempts"]
                atomic_json(self.path, self.items)
            self.emit("log", "飞书发送失败；" + ("已保留通知，可点击重试。" if item["attempts"] >= 3 else "稍后自动重试。"))
        else:
            with self.lock:
                item["sent"] = True
                # Retain recent deduplication keys without unbounded growth.
                sent = [i for i in self.items if i["sent"]][-200:]
                self.items = sent + [i for i in self.items if not i["sent"]]
                atomic_json(self.path, self.items)
            self.emit("log", "飞书通知已发送。")
        return True

    def _loop(self):
        while not self.closed.is_set():
            try:
                while not self.closed.is_set() and self.deliver_due():
                    pass
            except Exception:
                self.emit("log", "通知队列写入失败，请检查磁盘空间；通知未确认送达。")
            self.wake.wait(1)
            self.wake.clear()

    def close(self):
        self.closed.set()
        self.wake.set()


class Stopped(Exception):
    pass


class Blocked(Exception):
    pass


class GlobalBlock(Blocked):
    pass


class Engine:
    """UI independent runner. Adapter operates the actual browser player."""
    def __init__(self, adapter, store, notifier, emit, stop=None, pause=None,
                 clock=time.time, sleep=time.sleep):
        self.adapter, self.store, self.notifier, self.emit = adapter, store, notifier, emit
        self.stop = stop or threading.Event()
        self.pause = pause or threading.Event()
        self.clock, self.sleep = clock, sleep
        self.course = None
        self.videos = []
        self.phase = "准备中"
        self.next_status = self.clock() + HEARTBEAT_SECONDS
        self.run_id = str(time.time_ns())
        self.current = "—"
        self.last_save = 0
        self.prepared = False
        self.rechecking = False

    def summary(self):
        counts = {k: sum(v.status == k for v in self.videos) for k in STATUS}
        parts = [f"课程：{self.course.name}", f"状态：{self.phase}", f"当前：{self.current}",
                 f"已完成 {counts['completed']} / 视频总数 {len(self.videos)}",
                 f"待播放 {counts['queued']}，待复查 {counts['pending']}，受阻 {counts['blocked']}，未记入 {counts['unrecorded']}，未开放 {counts['locked']}"]
        for v in self.videos:
            if v.status in {"blocked", "unrecorded", "unknown"}:
                parts.append(f"• {v.name}：{v.reason or STATUS[v.status]}")
        return "\n".join(parts)

    def save(self):
        if self.prepared:
            self.store.save(self.course, self.videos, self.phase, self.next_status)
        self.last_save = self.clock()
        self.emit("snapshot", {"phase": self.phase, "current": self.current,
                               "videos": [asdict(v) for v in self.videos]})

    def notify(self, title, detail, key):
        self.notifier.enqueue(f"【雨课堂 · {title}】\n{detail}", self.run_id + ":" + key)

    def tick(self):
        if self.stop.is_set():
            raise Stopped()
        if self.clock() >= self.next_status:
            self.notify("六小时状态", self.summary(), f"status:{int(self.next_status)}")
            self.next_status = self.clock() + HEARTBEAT_SECONDS
            self.save()
        if self.clock() - self.last_save >= 10:
            self.save()
        was_paused = False
        while self.pause.is_set():
            if not was_paused:
                self.adapter.pause_video()
                self.phase = "已暂停"
                self.save()
                was_paused = True
            if self.stop.is_set():
                raise Stopped()
            if self.clock() >= self.next_status:
                self.notify("六小时状态", self.summary(), f"status:{int(self.next_status)}")
                self.next_status = self.clock() + HEARTBEAT_SECONDS
                self.save()
            self.sleep(0.3)
        if was_paused:
            self.phase = "运行中"
            self.save()
        if self.prepared and not self.rechecking:
            self.recheck_due()

    def prepare(self, course):
        self.course = course
        # Fresh platform data always wins over local "completed" flags.
        self.videos = self.adapter.list_videos(course)
        previous = self.store.previous(course.id)
        prior = {v["id"]: v for v in previous.get("videos", [])}
        if not self.videos:
            raise Blocked("未识别到视频列表，不能判定课程完成。请检查课程目录。")
        for video in self.videos:
            old = prior.get(video.id)
            if video.status not in {"completed", "locked"} and old and old["status"] == "pending":
                video.status = "pending"
                video.recheck_at = old["recheck_at"]
        self.phase = "运行中"
        self.prepared = True
        self.save()

    def refresh_completion(self, video, delayed=False):
        complete = self.adapter.is_complete(self.course, video, refresh=True)
        if complete is True:
            video.status, video.reason, video.recheck_at = "completed", "", None
        elif delayed:
            video.status, video.reason = "unrecorded", "播放结束，一小时复查后平台仍未显示完成" if complete is False else "一小时复查时无法确认平台完成状态"
            video.recheck_at = None
            self.notify("进度未确认", f"{self.course.name}\n{video.name}\n{video.reason}", "unrecorded:" + video.id)
        else:
            video.status = "pending"
            video.reason = "已刷新，等待一小时后复查"
            video.recheck_at = self.clock() + RECHECK_SECONDS
        self.save()

    def recheck_due(self):
        if self.rechecking:
            return
        self.rechecking = True
        current = self.current
        try:
            for video in self.videos:
                if video.status == "pending" and (video.recheck_at or 0) <= self.clock():
                    self.tick()
                    self.current = video.name
                    try:
                        self.refresh_completion(video, delayed=True)
                    except (GlobalBlock, Stopped):
                        raise
                    except Exception:
                        video.status, video.reason = "unrecorded", "一小时复查失败，需人工核实平台进度"
                        video.recheck_at = None
                        self.notify("复查失败", f"{self.course.name}\n{video.name}\n{video.reason}", "unrecorded:" + video.id)
                        self.save()
        finally:
            self.current = current
            self.rechecking = False

    def run(self, course):
        self.course = course
        try:
            self.tick()
            self.prepare(course)
            for video in self.videos:
                self.tick()
                self.recheck_due()
                if video.status not in {"queued", "unknown"}:
                    continue
                if video.status == "unknown":
                    video.status, video.reason = "blocked", "无法识别平台完成状态，需核实后再播放"
                    self.notify("视频状态待核实", f"{course.name}\n{video.name}\n{video.reason}", "blocked:" + video.id)
                    self.save()
                    continue
                self.current, self.phase = video.name, "播放中"
                video.status = "playing"
                self.save()
                try:
                    self.adapter.play(video, self.tick, self.progress)
                    self.refresh_completion(video)
                except GlobalBlock:
                    raise
                except Stopped:
                    raise
                except Exception as exc:
                    video.status = "blocked"
                    video.reason = str(exc) if isinstance(exc, Blocked) else f"页面操作失败（{type(exc).__name__}）"
                    self.notify("视频受阻，继续下一节", f"{course.name}\n{video.name}\n{video.reason}", "blocked:" + video.id)
                    self.adapter.pause_video()
                    self.save()
            while any(v.status == "pending" for v in self.videos):
                self.phase, self.current = "等待进度复查", "视频播放已结束"
                self.tick()
                self.recheck_due()
                self.sleep(1)
            incomplete = any(v.status not in {"completed", "locked"} for v in self.videos)
            opened = any(v.status != "locked" for v in self.videos)
            self.phase = "本次结束，仍有未完成项" if incomplete else ("已开放视频全部完成" if opened else "没有已开放视频")
        except Stopped:
            self.phase = "手动停止"
            for v in self.videos:
                if v.status == "playing":
                    v.status = "queued"
        except Exception as exc:
            self.phase = "任务受阻"
            reason = str(exc) if isinstance(exc, Blocked) else f"操作失败（{type(exc).__name__}）"
            for v in self.videos:
                if v.status == "playing":
                    v.status, v.reason = "blocked", reason
            self.emit("log", reason)
            self.notify("需要处理", f"{course.name}\n{reason}", "fatal")
        finally:
            self.adapter.pause_video()
            self.save()
            result = self.summary()
            atomic_json(self.store.path.parent / "last-result.json", {
                "time": self.clock(), "result": result,
                "videos": [asdict(v) for v in self.videos],
            })
            self.notify("退出结果", result, "final")
            self.emit("finished", result)

    def progress(self, position, duration):
        for v in self.videos:
            if v.status == "playing":
                v.position, v.duration = position, duration
                break
        self.emit("progress", {"position": position, "duration": duration})
