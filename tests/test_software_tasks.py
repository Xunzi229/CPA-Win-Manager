import threading
import time
import unittest

from cpa_manager.core.software_tasks import SoftwareTasks


class SoftwareTasksTests(unittest.TestCase):
    def drain_until(self, tasks, predicate):
        limit = time.monotonic() + 5
        while not predicate() and time.monotonic() < limit:
            tasks.drain()
            time.sleep(0.005)
        tasks.drain()
        self.assertTrue(predicate())

    def test_downloads_are_fifo_while_checks_run_independently_and_callbacks_stay_on_main_thread(self):
        tasks = SoftwareTasks()
        entered, release, checked = threading.Event(), threading.Event(), threading.Event()
        order, threads = [], []
        main_thread = threading.get_ident()
        def callback(job, event, value):
            threads.append(threading.get_ident())
        def first(emit, control):
            order.append("first")
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release download")
        try:
            tasks.submit(("portable", "a"), "download", first, callback)
            self.assertTrue(entered.wait(5))
            tasks.submit(("installer", "b"), "download", lambda *_: order.append("second"), callback)
            tasks.submit(("portable", "c"), "check", lambda *_: checked.set(), callback)
            self.assertTrue(checked.wait(5))
            self.drain_until(tasks, lambda: not tasks.has_page("portable", "check"))
            self.assertEqual(order, ["first"])
            self.assertFalse(tasks.jobs[("installer", "b")].started)
        finally:
            release.set()
        self.drain_until(tasks, lambda: not tasks.jobs)
        self.assertEqual(order, ["first", "second"])
        self.assertEqual(set(threads), {main_thread})

    def test_check_failure_does_not_block_next_check_and_duplicate_row_is_rejected(self):
        tasks = SoftwareTasks(check_workers=1)
        release = threading.Event()
        events = []
        def failing(emit, control):
            release.wait(5)
            raise OSError("network failed")
        callback = lambda job, event, value: events.append((job.key[1], event))
        tasks.submit(("page", "a"), "check", failing, callback)
        self.assertFalse(tasks.submit(("page", "a"), "check", lambda *_: None, callback))
        tasks.submit(("page", "b"), "check", lambda emit, _: emit("result", "ok"), callback)
        release.set()
        self.drain_until(tasks, lambda: not tasks.jobs)
        self.assertIn(("a", "error"), events)
        self.assertIn(("b", "result"), events)

    def test_cancellation_removes_waiting_jobs_but_allows_commit_to_finish(self):
        tasks = SoftwareTasks()
        entered, release = threading.Event(), threading.Event()
        ran = []
        def committing(emit, control):
            control.begin_commit()
            entered.set()
            release.wait(5)
            ran.append("committed")
        callback = lambda *_: None
        try:
            tasks.submit(("a", "active"), "download", committing, callback)
            self.assertTrue(entered.wait(5))
            tasks.submit(("a", "queued"), "download", lambda *_: ran.append("cancelled job"), callback)
            tasks.submit(("b", "next"), "download", lambda *_: ran.append("other page"), callback)
            self.assertFalse(tasks.cancel_page("a"))
            self.assertNotIn(("a", "queued"), tasks.jobs)
            self.assertTrue(tasks.jobs[("a", "active")].cancel_requested)
            self.assertFalse(tasks.jobs[("a", "active")].control.is_set())
        finally:
            release.set()
        self.drain_until(tasks, lambda: not tasks.jobs)
        self.assertEqual(ran, ["committed", "other page"])

    def test_cancel_after_download_finishes_keeps_committed_package_result(self):
        tasks = SoftwareTasks()
        received = []
        tasks.submit(("page", "a"), "download", lambda emit, _: emit("downloaded", "committed package"),
                     lambda job, event, value: received.append(event))
        limit = time.monotonic() + 5
        while tasks.jobs[("page", "a")].control.phase != "finished" and time.monotonic() < limit:
            time.sleep(0.005)
        self.assertEqual(tasks.jobs[("page", "a")].control.phase, "finished")
        tasks.cancel_page("page")
        self.drain_until(tasks, lambda: not tasks.jobs)
        self.assertIn("downloaded", received)
