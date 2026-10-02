"""Windows file lock shared by installers and resumable downloads."""
from contextlib import contextmanager


@contextmanager
def update_lock(root):
    import msvcrt
    with (root / "update.lock").open("a+b") as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("另一个更新任务正在运行。") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
