"""Long River Rain Classroom adapter, verified against the September 2026 UI.

Reads course rows and their rendered Vue props. Playback uses normal player
controls. No progress/heartbeat endpoints are called or modified.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import unicodedata
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
from core import APP_DIR, Blocked, GlobalBlock, Course, Video
from platform_support import chrome_candidates, chrome_path, detached_kwargs, restrict

ORIGIN = "https://changjiang.yuketang.cn"
INDEX = ORIGIN + "/v2/web/index"

# Read only the resource metadata attached to the visible course directory.
LEAF_ROWS = """es => es.map((e,index) => {
  let v=e.__vue__, props=null;
  for(let i=0;v && i<6;i++,v=v.$parent) {
    if(v.$props && v.$props.leafData) {props=v.$props;break;}
  }
  const leaf=props?.leafData;
  return {index, id:leaf?.id == null ? null : String(leaf.id),
    name:e.querySelector('.leaf-title .title')?.textContent.trim(),
    video:!!e.querySelector('.icon--shipin'),
    locked:leaf?.is_locked, visible:leaf?.is_show,
    start:leaf?.start_time, end:leaf?.end_time,
    progress:e.querySelector('.progress-wrap')?.innerText.trim() || '',
    text:e.innerText};
})"""


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def completion(text):
    text = text.strip()
    if text in {"已完成", "完成", "100%", "100.0%", "100.00%"}:
        return True
    if text in {"未开始", "未完成", "进行中", "学习中"} or re.fullmatch(r"\d+(?:\.\d+)?%", text):
        return False
    return None


class BrowserAdapter:
    def __init__(self, emit=lambda *_: None):
        self.emit = emit
        self.pw = None
        self.browser = None
        self.catalog = None
        self.player = None
        self.native_dialog = False
        self.directory_url = None
        self.nonvideo_count = 0
        self.cancel = lambda: None

    def connect(self):
        if self.browser and self.browser.is_connected():
            return
        chrome = chrome_path()
        if chrome is None:
            searched = "、".join(str(p) for p in chrome_candidates()) or "系统默认位置"
            raise GlobalBlock(f"未找到 Google Chrome，请先安装 Chrome。已查找：{searched}")
        profile = APP_DIR / "browser"
        profile.mkdir(parents=True, exist_ok=True)
        restrict(profile, 0o700)
        active = profile / "DevToolsActivePort"

        def endpoint():
            try:
                port = int(active.read_text().splitlines()[0])
                address = f"http://127.0.0.1:{port}"
                # Disable ambient HTTP proxy for loopback.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(address + "/json/version", timeout=1) as r:
                    json.load(r)
                return address
            except Exception:
                return None

        address = endpoint()
        if not address:
            subprocess.Popen([str(chrome), "--user-data-dir=" + str(profile),
                              "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                              "--no-first-run", "--no-default-browser-check", "--new-window", ORIGIN + "/web/"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached_kwargs())
            for _ in range(60):
                self.cancel()
                address = endpoint()
                if address:
                    break
                time.sleep(.25)
        if not address:
            raise GlobalBlock("专用浏览器未能启动，请关闭专用 Chrome 窗口后重试。")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(address, timeout=15000)
        self.context = self.browser.contexts[0]
        self.context.set_default_timeout(8000)
        self.context.set_default_navigation_timeout(25000)
        self.catalog = next((p for p in self.context.pages if urlparse(p.url).hostname == "changjiang.yuketang.cn"
                             and "/video/" not in p.url and "/learning/" not in p.url), None)
        if self.catalog is None:
            self.catalog = self.context.new_page()
        self.player = next((p for p in self.context.pages if urlparse(p.url).hostname == "changjiang.yuketang.cn"
                            and "/video/" in p.url), None)
        if self.player:
            self._watch(self.player)

    def _watch(self, page):
        def on_dialog(dialog):
            self.native_dialog = True
            # Do not accept or answer an unknown native prompt. Dismiss allows
            # navigation away from this leaf so the next video can be tried.
            try:
                dialog.dismiss()
            except Exception:
                pass
        page.on("dialog", on_dialog)

    def login(self):
        self.connect()
        if not self.catalog.url.startswith(ORIGIN):
            self.catalog.goto(ORIGIN + "/web/", wait_until="domcontentloaded")
        self.catalog.bring_to_front()
        self.emit("log", "请在专用浏览器扫码登录，然后点击“查找课程”。")

    def check_login(self, page):
        if page.is_closed():
            raise GlobalBlock("专用浏览器窗口已关闭，请重新打开浏览器。")
        path = urlparse(page.url).path
        if path.rstrip("/") == "/web" or "login" in path.lower():
            raise GlobalBlock("登录已失效，请在专用浏览器重新扫码，然后再次开始。")

    def scan_courses(self, query):
        self.connect()
        self.catalog.goto(INDEX, wait_until="domcontentloaded")
        end = time.monotonic() + 25
        while time.monotonic() < end:
            self.cancel()
            self.check_login(self.catalog)
            if self.catalog.locator(".el-card h1").count():
                break
            self.catalog.wait_for_timeout(250)
        self.check_login(self.catalog)
        cards = self.catalog.locator(".el-card").evaluate_all("""es => es.map(e=>({
            name:e.querySelector('h1')?.textContent.trim(),
            detail:e.querySelector('.className')?.textContent.trim(),
            teacher:e.querySelector('.teacherName')?.textContent.trim()
        })).filter(e=>e.name && e.detail)""")
        if not cards:
            raise Blocked("未识别到课程卡片，请确认已登录且位于“我听的课”。")
        result = []
        for c in cards:
            if normalize(query) in normalize(c["name"]):
                detail = c["detail"]
                key = hashlib.sha256((c["name"] + "|" + detail).encode()).hexdigest()[:20]
                result.append(Course(key, c["name"], "", detail))
        self.catalog.bring_to_front()
        return result

    def open_course(self, course):
        self.connect()
        self.pause_video()
        if course.url:
            if not course.url.startswith(ORIGIN + "/"):
                raise Blocked("课程地址不是长江雨课堂。")
            self.catalog.goto(course.url, wait_until="domcontentloaded")
        else:
            if "/v2/web/index" not in self.catalog.url:
                self.catalog.goto(INDEX, wait_until="domcontentloaded")
            self.catalog.locator(".el-card h1").first.wait_for(timeout=25000)
            matches = self.catalog.locator(".el-card").filter(
                has=self.catalog.get_by_text(course.name, exact=True)).filter(
                has=self.catalog.get_by_text(course.detail, exact=True))
            if matches.count() != 1:
                raise Blocked("课程匹配不是唯一结果，请重新查找并选择具体班级。")
            matches.first.locator("h1").click()
            self.catalog.wait_for_url(re.compile(r"/studentLog/\d+"), timeout=25000)
            student_url = self.catalog.url
            # Capture the stable studentLog route before switching tabs. On
            # some loads the tab click races the Vue render and briefly lands
            # on /v2/web/?tab=content; the saved route lets us recover safely.
            self.catalog.get_by_text("学习内容", exact=True).click()
            # Vue switches the iframe asynchronously and may keep the outer
            # URL on the previous tab; the iframe directory is the reliable
            # readiness signal.
            self.catalog.wait_for_timeout(150)
            if "/studentLog/" not in self.catalog.url:
                self.catalog.goto(student_url + ("&tab=content" if "?" in student_url else "?tab=content"),
                                  wait_until="domcontentloaded")
            course.url = self.catalog.url
        self.check_login(self.catalog)
        match = re.search(r"/studentLog/(\d+)", self.catalog.url)
        if not match:
            raise Blocked("课程页面格式发生变化，未识别到课程编号。")
        course.id = match.group(1)
        self.directory_url = course.url
        self._directory()

    def _directory(self):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            self.cancel()
            self.check_login(self.catalog)
            for frame in self.catalog.frames:
                if "/pro/lms/" in frame.url and frame.locator(".leaf-detail").count():
                    return frame
            self.catalog.wait_for_timeout(300)
        raise Blocked("课程目录未加载完成，网络或页面结构可能发生变化。")

    def _rows(self):
        return self._directory().locator(".leaf-detail").evaluate_all(LEAF_ROWS)

    def list_videos(self, course):
        self.open_course(course)
        rows = self._rows()
        self.nonvideo_count = sum(not row["video"] for row in rows)
        self.emit("log", f"目录共 {len(rows)} 项；识别 {len(rows)-self.nonvideo_count} 个视频，另有 {self.nonvideo_count} 项作业、讨论或资料。")
        videos = []
        now_ms = time.time() * 1000
        for row in rows:
            if not row["video"]:
                continue
            if not row["id"] or not row["name"]:
                raise Blocked("视频编号无法识别，已停止以避免选错视频。")
            locked = row["locked"] is True or row["visible"] is False or (row["start"] or 0) > now_ms
            done = completion(row["progress"])
            status = "locked" if locked else "completed" if done else "queued" if done is False else "unknown"
            videos.append(Video(row["id"], row["name"], "", status))
        if len({v.id for v in videos}) != len(videos):
            raise Blocked("目录存在重复视频编号，需要检查后再运行。")
        return videos

    def _open_video(self, video):
        self.pause_video()
        self.native_dialog = False
        if self.player and not self.player.is_closed():
            self.player.close()
            self.player = None
        frame = self._directory()
        rows = frame.locator(".leaf-detail").evaluate_all(LEAF_ROWS)
        row = next((r for r in rows if r["id"] == video.id and r["video"]), None)
        if row is None:
            raise Blocked("视频已从当前目录移除，已跳过。")
        if row["locked"] is True or row["visible"] is False:
            raise Blocked("该视频当前不可访问，已跳过。")
        existing = set(self.context.pages)
        frame.locator(".leaf-detail").nth(row["index"]).click(timeout=10000)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            self.cancel()
            new_pages = [p for p in self.context.pages if p not in existing]
            if new_pages:
                self.player = new_pages[-1]
                self._watch(self.player)
                break
            # A future UI may navigate the current tab instead of opening one.
            if self.catalog.locator("video").count():
                self.player = self.catalog
                self.catalog = self.context.new_page()
                self.catalog.goto(self.directory_url, wait_until="domcontentloaded")
                self._watch(self.player)
                break
            self.catalog.wait_for_timeout(250)
        if not self.player:
            raise Blocked("视频窗口未打开，可能被弹窗拦截。")
        self.player.bring_to_front()
        video.url = self.player.url
        end = time.monotonic() + 45
        while time.monotonic() < end:
            self.cancel()
            self.check_login(self.player)
            blocker = self._blocker()
            if blocker:
                raise Blocked(blocker)
            found = self._video()
            if found:
                return found
            self.player.wait_for_timeout(250)
        raise Blocked("45 秒内未出现视频播放器，已跳过。")

    def _video(self):
        if not self.player or self.player.is_closed():
            return None
        for frame in self.player.frames:
            locator = frame.locator("video")
            for index in range(locator.count()):
                if locator.nth(index).is_visible():
                    return locator.nth(index)
        return None

    def _blocker(self):
        if self.native_dialog:
            return "出现无法处理的浏览器弹窗，已跳过。"
        for frame in self.player.frames:
            for dialog in frame.locator("[role=dialog]:visible, .el-message-box:visible, .el-dialog:visible, .ant-modal-content:visible").all():
                text = dialog.inner_text().strip()
                if text:
                    return "出现弹窗或交互题，需要本人处理：" + text[:180]
            # A video quiz is normally rendered inside the player, not the global directory.
            for selector in (".xt_video_player_question:visible", ".video-exercise:visible", ".xt_video_player_error:visible"):
                loc = frame.locator(selector)
                if loc.count() and loc.first.inner_text().strip():
                    return "播放器提示：" + loc.first.inner_text().strip()[:180]
        return None

    def _resume(self, locator):
        # Normal speed only. Never seek or change completion fields.
        locator.evaluate("v => { v.playbackRate=1; }")
        for frame in self.player.frames:
            button = frame.locator(".xt_video_player_play_btn")
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                return
        locator.evaluate("v => v.play().then(()=>true).catch(()=>false)")

    def play(self, video, tick, progress):
        self.cancel = tick
        try:
            self._play(video, tick, progress)
        finally:
            self.cancel = lambda: None

    def _play(self, video, tick, progress):
        retries = 0
        locator = self._open_video(video)
        last_position = -1
        last_motion = time.monotonic()
        paused_attempt = 0
        while True:
            before_tick = time.monotonic()
            tick()
            if time.monotonic() - before_tick > 2:
                last_motion = time.monotonic()
            self.check_login(self.player)
            blocker = self._blocker()
            if blocker:
                raise Blocked(blocker)
            state = locator.evaluate("v=>({position:v.currentTime,duration:Number.isFinite(v.duration)?v.duration:0,paused:v.paused,ended:v.ended,error:!!v.error,rate:v.playbackRate})")
            progress(state["position"], state["duration"])
            if state["ended"] and state["duration"] > 0:
                self.player.wait_for_timeout(2500)
                return
            if state["position"] > last_position + .25:
                last_position, last_motion = state["position"], time.monotonic()
                paused_attempt = 0
            if state["rate"] != 1:
                locator.evaluate("v=>{v.playbackRate=1}")
            if state["paused"] and time.monotonic() - paused_attempt > 10:
                self._resume(locator)
                paused_attempt = time.monotonic()
            if state["error"] or time.monotonic() - last_motion > 90:
                if retries >= 2:
                    raise Blocked("视频持续卡住或报错，重试两次后仍无法播放。")
                retries += 1
                self.emit("log", f"{video.name}：播放卡顿，重新打开（{retries}/2）。")
                locator = self._open_video(video)
                last_position, last_motion = -1, time.monotonic()
            # Pump Playwright events; user Stop and Pause are checked every second.
            self.player.wait_for_timeout(1000)

    def is_complete(self, course, video, refresh=True):
        self.check_login(self.catalog)
        if refresh:
            self.catalog.reload(wait_until="domcontentloaded")
        rows = self._rows()
        row = next((r for r in rows if r["id"] == video.id), None)
        if self.player and not self.player.is_closed():
            self.player.bring_to_front()
        return completion(row["progress"]) if row else None

    def pause_video(self):
        if not self.player or self.player.is_closed():
            return
        try:
            for frame in self.player.frames:
                frame.locator("video").evaluate_all("es=>es.forEach(v=>v.pause())")
        except Exception:
            pass

    def disconnect(self):
        self.pause_video()
        if self.pw:
            self.pw.stop()
        self.browser, self.pw = None, None
