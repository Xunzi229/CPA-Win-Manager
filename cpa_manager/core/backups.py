"""Cleanup of generated installation backups within the selected software root."""
from pathlib import Path
import re
import shutil

from cpa_manager.core.download import DownloadControl
from cpa_manager.core.locking import update_lock


def backup_directories(directory, kind):
    root = Path(directory).expanduser().resolve()
    if kind == "portable":
        parent, pattern = root, r"\.install-backup-[0-9a-f]{32}"
    elif kind == "project":
        parent, pattern = root / ".update-backups", r"\d{8}-\d{6}-[0-9a-f]{8}"
    else:
        raise ValueError("未知的安装备份类型。")
    if not parent.exists() or parent.is_symlink() or parent.is_junction():
        return []
    return [p for p in parent.iterdir() if re.fullmatch(pattern, p.name)
            and p.is_dir() and not p.is_symlink() and not p.is_junction()
            and p.resolve().parent == parent.resolve()]


def clear_backups(directory, kind, report, control=None):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        report(100, "没有可清理的安装备份。")
        return 0
    deleted, errors = 0, []
    with update_lock(root):
        backups = backup_directories(root, kind)
        if isinstance(control, DownloadControl):
            control.begin_commit()
        parent = root if kind == "portable" else root / ".update-backups"
        for index, backup in enumerate(backups):
            # Recheck the resolved target immediately before recursive deletion.
            if (parent.is_symlink() or parent.is_junction()
                    or backup.is_symlink() or backup.is_junction()
                    or backup.resolve().parent != parent
                    or not backup.resolve().is_relative_to(root)):
                errors.append(backup.name + "：备份路径已变化")
                continue
            try:
                shutil.rmtree(backup)
                deleted += 1
            except OSError as error:
                errors.append(backup.name + "：" + str(error))
            report(100 * (index + 1) / len(backups), "正在清理安装备份…")
    if errors:
        raise RuntimeError(f"已清理 {deleted} 个备份，部分清理失败：\n" + "\n".join(errors))
    report(100, f"已清理 {deleted} 个安装备份。" if backups else "没有可清理的安装备份。")
    return deleted
