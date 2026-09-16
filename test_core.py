import tempfile
import threading
import unittest
from pathlib import Path

from browser import completion, normalize, normalize_speed
from core import (Blocked, Course, Engine, GlobalBlock, HEARTBEAT_SECONDS,
                  Notifier, RECHECK_SECONDS, Store, Video, read_json)


class Clock:
    def __init__(self): self.now = 100000.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Notifications:
    def __init__(self): self.messages = []
    def enqueue(self, text, key): self.messages.append((text, key))


class Adapter:
    def __init__(self, videos, clock):
        self.videos, self.clock = videos, clock
        self.played, self.checked = [], []
        self.failure, self.results, self.durations = {}, {}, {}
        self.on_tick = None
    def list_videos(self, course): return self.videos
    def play(self, video, tick, progress):
        self.played.append(video.id)
        if video.id in self.failure: raise self.failure[video.id]
        duration = self.durations.get(video.id, 2)
        for _ in range(duration):
            self.clock.sleep(1)
            tick()
            progress(1, duration)
    def is_complete(self, course, video, refresh=True):
        self.checked.append((video.id, self.clock()))
        values = self.results.get(video.id, [True])
        if len(values) > 1: return values.pop(0)
        return values[0]
    def pause_video(self): pass


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.notifications = Clock(), Notifications()
        self.course = Course("1", "英语（博士）", "", "advanced")
        self.store = Store(Path(self.tmp.name) / "state.json")
    def engine(self, adapter, stop=None):
        return Engine(adapter, self.store, self.notifications, lambda *_: None,
                      stop=stop, clock=self.clock, sleep=self.clock.sleep)

    def test_blocked_video_skips_to_next_and_does_not_claim_all_complete(self):
        adapter = Adapter([Video("a", "A", ""), Video("b", "B", "")], self.clock)
        adapter.failure["a"] = Blocked("弹题")
        engine = self.engine(adapter)
        engine.run(self.course)
        self.assertEqual(adapter.played, ["a", "b"])
        self.assertEqual([v.status for v in engine.videos], ["blocked", "completed"])
        self.assertIn("仍有未完成", engine.phase)
        self.assertEqual(len(self.notifications.messages), 2)

    def test_recheck_happens_after_one_hour_while_next_video_is_playing(self):
        adapter = Adapter([Video("a", "A", ""), Video("b", "B", "")], self.clock)
        adapter.results["a"] = [False, True]
        adapter.durations["b"] = RECHECK_SECONDS + 10
        engine = self.engine(adapter)
        engine.run(self.course)
        checks_a = [t for i,t in adapter.checked if i == "a"]
        self.assertEqual(checks_a[1] - checks_a[0], RECHECK_SECONDS)
        self.assertLess(checks_a[1], next(t for i,t in adapter.checked if i == "b"))
        self.assertEqual(engine.videos[0].status, "completed")

    def test_unrecorded_after_refresh_and_hour_sends_notification(self):
        adapter = Adapter([Video("a", "A", "")], self.clock)
        adapter.results["a"] = [False, False]
        engine = self.engine(adapter)
        engine.run(self.course)
        self.assertEqual(engine.videos[0].status, "unrecorded")
        self.assertEqual(len(adapter.checked), 2)
        self.assertTrue(any("进度未确认" in m for m,k in self.notifications.messages))

    def test_six_hour_heartbeat_even_during_long_video(self):
        adapter = Adapter([Video("a", "A", "")], self.clock)
        adapter.durations["a"] = HEARTBEAT_SECONDS + 1
        engine = self.engine(adapter)
        engine.run(self.course)
        self.assertEqual(sum("六小时状态" in m for m,k in self.notifications.messages), 1)

    def test_fresh_platform_completion_wins_and_locked_is_not_played(self):
        self.store.save(self.course, [Video("a", "A", "", "completed")], "done", 0)
        adapter = Adapter([Video("a", "A", ""), Video("b", "B", "", "locked"), Video("c", "C", "", "completed")], self.clock)
        self.engine(adapter).run(self.course)
        self.assertEqual(adapter.played, ["a"])

    def test_pending_recheck_survives_restart_without_replaying(self):
        self.store.save(self.course, [Video("a", "A", "", "pending", recheck_at=self.clock()+20)], "waiting", 0)
        adapter = Adapter([Video("a", "A", "")], self.clock)
        self.engine(adapter).run(self.course)
        self.assertEqual(adapter.played, [])
        self.assertEqual(adapter.checked[0][1], 100020)

    def test_stop_before_prepare_does_not_erase_saved_queue(self):
        self.store.save(self.course, [Video("a", "A", "", "pending", recheck_at=123)], "waiting", 0)
        stop = threading.Event();stop.set()
        self.engine(Adapter([], self.clock), stop).run(self.course)
        self.assertEqual(self.store.previous("1")["videos"][0]["recheck_at"], 123)

    def test_global_login_failure_stops_and_is_not_success(self):
        adapter = Adapter([Video("a", "A", ""), Video("b", "B", "")], self.clock)
        adapter.failure["a"] = GlobalBlock("登录失效")
        engine = self.engine(adapter)
        engine.run(self.course)
        self.assertEqual(adapter.played, ["a"])
        self.assertEqual(engine.phase, "任务受阻")

    def test_unknown_completion_is_not_assumed_unfinished(self):
        adapter = Adapter([Video("a", "A", "", "unknown")], self.clock)
        engine = self.engine(adapter)
        engine.run(self.course)
        self.assertEqual(adapter.played, [])
        self.assertEqual(engine.videos[0].status, "blocked")

    def test_completion_and_course_name_normalization(self):
        self.assertTrue(completion("已完成"))
        self.assertFalse(completion("未完成"))
        self.assertFalse(completion("99%"))
        self.assertIsNone(completion("页面加载中"))
        self.assertEqual(normalize("英语（博士）"), normalize("英语 (博士)"))

    def test_playback_speed_is_clamped_and_defaults_to_normal(self):
        self.assertEqual(normalize_speed(1.5), 1.5)
        self.assertEqual(normalize_speed("2"), 2.0)
        self.assertEqual(normalize_speed(0), 1.0)
        self.assertEqual(normalize_speed(-3), 1.0)
        self.assertEqual(normalize_speed(None), 1.0)
        self.assertEqual(normalize_speed("快"), 1.0)
        self.assertEqual(normalize_speed(99), 4.0)
        self.assertEqual(normalize_speed(0.1), 0.5)


class NotificationTests(unittest.TestCase):
    def test_retry_deduplication_and_durable_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def fail(text):
                calls.append(text)
                raise RuntimeError("network")
            clock = Clock()
            notifier = Notifier(lambda *_: None, Path(d), fail, clock)
            notifier.close();notifier.thread.join(2)
            notifier.enqueue("message", "same")
            notifier.enqueue("message", "same")
            self.assertEqual(len(notifier.items), 1)
            for _ in range(3):
                notifier.deliver_due();clock.sleep(200)
            self.assertEqual(len(calls), 3)
            self.assertFalse(notifier.deliver_due())
            notifier.sender = calls.append
            notifier.retry_failed()
            notifier.deliver_due()
            self.assertTrue(read_json(Path(d)/"notifications.json", [])[0]["sent"])


if __name__ == "__main__":
    unittest.main()
