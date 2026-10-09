"""Exclusive file lock for install and service control."""
from contextlib import contextmanager

try:
    import fcntl
except ImportError:
    fcntl = None


@contextmanager
def file_lock(path):
    if fcntl is None:
        raise RuntimeError("文件锁仅支持 macOS。")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("另一个安装或启停任务正在运行。") from None
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
