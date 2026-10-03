"""Shared modal positioning relative to the manager window."""
from cpa_manager.core.runtime import monitor_work_area


def center_dialog(dialog, owner, size=None):
    dialog.update_idletasks()
    width, height = size or (dialog.winfo_reqwidth(), dialog.winfo_reqheight())
    center_x = owner.winfo_rootx() + owner.winfo_width() // 2
    center_y = owner.winfo_rooty() + owner.winfo_height() // 2
    left, top, right, bottom = monitor_work_area(center_x, center_y,
        (0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight()))
    outer_width, outer_height = width + 16, height + 40
    x = max(left + 8, min(center_x - outer_width // 2, right - outer_width - 8))
    y = max(top + 8, min(center_y - outer_height // 2, bottom - outer_height - 8))
    dialog.geometry(f"{width}x{height}+{x}+{y}")
