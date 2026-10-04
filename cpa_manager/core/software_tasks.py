"""Row checks and a shared FIFO download queue; callbacks run only when drained."""
from collections import deque
from dataclasses import dataclass, field
import queue
import threading

from cpa_manager.core.download import DownloadControl, DownloadCancelled


@dataclass
class SoftwareJob:
    key: tuple
    kind: str
    task: object
    callback: object
    control: DownloadControl = field(default_factory=DownloadControl)
    started: bool = False
    cancel_requested: bool = False


class SoftwareTasks:
    def __init__(self, check_workers=4):
        self.jobs = {}
        self.waiting = {"check": deque(), "download": deque()}
        self.active = {"check": 0, "download": 0}
        self.limits = {"check": check_workers, "download": 1}
        self.events = queue.Queue()

    def submit(self, key, kind, task, callback):
        if key in self.jobs:
            return False
        job = SoftwareJob(key, kind, task, callback)
        self.jobs[key] = job
        self.waiting[kind].append(job)
        callback(job, "queued", None)
        self._start(kind)
        return True

    def _start(self, kind):
        while self.waiting[kind] and self.active[kind] < self.limits[kind]:
            job = self.waiting[kind].popleft()
            if job.control.is_set():
                continue
            job.started = True
            self.active[kind] += 1
            job.callback(job, "started", None)
            events = self.events
            def worker(job=job):
                def emit(event, value):
                    events.put((job, event, value))
                try:
                    job.task(emit, job.control)
                except DownloadCancelled as error:
                    emit("cancelled", str(error))
                except Exception as error:
                    emit("error", str(error))
                finally:
                    job.control.finish()
                    emit("done", None)
            threading.Thread(target=worker, daemon=True).start()

    def drain(self):
        while True:
            try:
                job, event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if self.jobs.get(job.key) is not job:
                continue
            if event == "done":
                del self.jobs[job.key]
                self.active[job.kind] -= 1
                try:
                    job.callback(job, event, value)
                finally:
                    self._start(job.kind)
            elif event in ("error", "cancelled", "downloaded", "installed") or not job.control.is_set():
                job.callback(job, event, value)

    def cancel_page(self, page):
        accepted = True
        for job in list(self.jobs.values()):
            if job.key[0] != page:
                continue
            job.cancel_requested = True
            if not job.started:
                self.waiting[job.kind].remove(job)
                del self.jobs[job.key]
                job.control.request_cancel()
                job.control.finish()
                job.callback(job, "cancelled", "排队任务已取消。")
                job.callback(job, "done", None)
            elif not job.control.request_cancel():
                accepted = False
        return accepted

    def has_page(self, page, kind=None):
        return any(job.key[0] == page and (kind is None or job.kind == kind) for job in self.jobs.values())


def shared_tasks(app):
    if not hasattr(app, "software_tasks"):
        app.software_tasks = SoftwareTasks()
    return app.software_tasks
