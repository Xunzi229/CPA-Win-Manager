"""Table height resizer handle and row height controls."""
import tkinter as tk
from tkinter import ttk
from cpa_manager.ui.widgets.help_hint import add_help

MIN_TABLE_ROWS = 3
MAX_TABLE_ROWS = 30
DEFAULT_PORTABLE_ROWS = 7
DEFAULT_INSTALLER_ROWS = 9

MIN_ROW_HEIGHT = 24
MAX_ROW_HEIGHT = 48
DEFAULT_ROW_HEIGHT = 32

ROW_HEIGHT_PRESETS = [
    (26, "紧凑 (26px)"),
    (32, "标准 (32px)"),
    (36, "舒适 (36px)"),
    (40, "宽松 (40px)"),
]


def clamp_rows(value, default=DEFAULT_PORTABLE_ROWS):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        val = int(value)
    except (ValueError, TypeError):
        return default
    return max(MIN_TABLE_ROWS, min(MAX_TABLE_ROWS, val))


def clamp_row_height(value, default=DEFAULT_ROW_HEIGHT):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        val = int(value)
    except (ValueError, TypeError):
        return default
    return max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, val))


class TableResizer:
    def __init__(self, parent, table, actions, save_height, default_height=7, app=None):
        self.parent = parent
        self.table = table
        self.actions = actions
        self.save_height = save_height
        self.default_height = default_height
        self.app = app

        self.dragging_bar = False
        self.bar_start_y = 0
        self.bar_start_rows = default_height

        self.dragging_heading = False
        self.heading_start_y = 0
        self.heading_start_rh = DEFAULT_ROW_HEIGHT

        # 1. Resize bar (below the table / horizontal scrollbar)
        self.bar = tk.Frame(parent, height=8, cursor="sb_v_double_arrow", bg="#f8fafc")
        self.handle = tk.Frame(self.bar, width=38, height=3, bg="#cbd5e1")
        self.handle.place(relx=0.5, rely=0.5, anchor="center")

        for widget in (self.bar, self.handle):
            widget.bind("<Enter>", self.on_bar_enter)
            widget.bind("<Leave>", self.on_bar_leave)
            widget.bind("<ButtonPress-1>", self.on_bar_press)
            widget.bind("<B1-Motion>", self.on_bar_drag)
            widget.bind("<ButtonRelease-1>", self.on_bar_release)
            widget.bind("<Double-Button-1>", self.on_bar_double_click)

        add_help(self.bar, "上下拖动调整列表高度，双击自适应内容行数。")

        # 2. Heading bottom edge drag for row height
        table.bind("<Motion>", self.on_table_motion, add="+")
        table.bind("<ButtonPress-1>", self.on_table_press, add="+")
        table.bind("<B1-Motion>", self.on_table_drag, add="+")
        table.bind("<ButtonRelease-1>", self.on_table_release, add="+")

    def current_rows(self):
        try:
            return int(self.table.cget("height"))
        except (ValueError, TypeError):
            return self.default_height

    def on_bar_enter(self, _):
        self.handle.configure(bg="#3b82f6")
        self.bar.configure(bg="#f1f5f9")

    def on_bar_leave(self, _):
        if not self.dragging_bar:
            self.handle.configure(bg="#cbd5e1")
            self.bar.configure(bg="#f8fafc")

    def on_bar_press(self, event):
        self.dragging_bar = True
        self.bar_start_y = event.y_root
        self.bar_start_rows = self.current_rows()
        self.handle.configure(bg="#2563eb")

    def on_bar_drag(self, event):
        if not self.dragging_bar:
            return
        dy = event.y_root - self.bar_start_y
        row_height = getattr(self.app, "table_row_height", DEFAULT_ROW_HEIGHT) if self.app else DEFAULT_ROW_HEIGHT
        delta_rows = int(round(dy / max(1, row_height)))
        target = max(MIN_TABLE_ROWS, min(MAX_TABLE_ROWS, self.bar_start_rows + delta_rows))
        if target != self.current_rows():
            self.apply_height(target)

    def on_bar_release(self, _):
        if self.dragging_bar:
            self.dragging_bar = False
            self.handle.configure(bg="#cbd5e1")
            self.bar.configure(bg="#f8fafc")
            if self.save_height:
                self.save_height(self.current_rows())

    def on_bar_double_click(self, _):
        count = len(self.table.get_children())
        target = max(MIN_TABLE_ROWS, min(MAX_TABLE_ROWS, count)) if count > 0 else self.default_height
        if self.current_rows() == target:
            target = self.default_height
        self.apply_height(target)
        if self.save_height:
            self.save_height(target)

    def apply_height(self, height):
        self.table.configure(height=height)
        if self.actions and hasattr(self.actions, "tree"):
            self.actions.tree.configure(height=height)
            self.actions.schedule_render()

    def adjust_rows(self, delta):
        cur = self.current_rows()
        target = max(MIN_TABLE_ROWS, min(MAX_TABLE_ROWS, cur + delta))
        self.apply_height(target)
        if self.save_height:
            self.save_height(target)

    # Heading edge interaction
    def is_heading_bottom_edge(self, event):
        region = self.table.identify_region(event.x, event.y)
        if region != "heading":
            return False
        # Treeview heading bottom boundary is typically around y=21..28
        first_row = self.table.get_children()
        heading_bottom = 25
        if first_row:
            bbox = self.table.bbox(first_row[0])
            if bbox:
                heading_bottom = bbox[1]
        return abs(event.y - heading_bottom) <= 4

    def on_table_motion(self, event):
        if self.dragging_heading or self.dragging_bar:
            return
        if self.is_heading_bottom_edge(event):
            self.table.configure(cursor="sb_v_double_arrow")
        elif str(self.table.cget("cursor")) == "sb_v_double_arrow":
            self.table.configure(cursor="")

    def on_table_press(self, event):
        if self.is_heading_bottom_edge(event):
            self.dragging_heading = True
            self.heading_start_y = event.y_root
            self.heading_start_rh = getattr(self.app, "table_row_height", DEFAULT_ROW_HEIGHT) if self.app else DEFAULT_ROW_HEIGHT
            return "break"

    def on_table_drag(self, event):
        if not self.dragging_heading:
            return
        dy = event.y_root - self.heading_start_y
        new_rh = max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, self.heading_start_rh + dy))
        if self.app and hasattr(self.app, "set_table_row_height"):
            self.app.set_table_row_height(new_rh, persist=False)
        return "break"

    def on_table_release(self, _):
        if self.dragging_heading:
            self.dragging_heading = False
            self.table.configure(cursor="")
            if self.app and hasattr(self.app, "set_table_row_height"):
                cur_rh = getattr(self.app, "table_row_height", DEFAULT_ROW_HEIGHT)
                self.app.set_table_row_height(cur_rh, persist=True)

    def add_context_menu(self, menu):
        height_menu = tk.Menu(menu, tearoff=False)
        height_menu.add_command(label="自适应内容行数", command=lambda: self.on_bar_double_click(None))
        height_menu.add_command(label="加高 (+2 行)", command=lambda: self.adjust_rows(2))
        height_menu.add_command(label="降低 (-2 行)", command=lambda: self.adjust_rows(-2))
        height_menu.add_command(label=f"恢复默认高度 ({self.default_height} 行)",
                                command=lambda: (self.apply_height(self.default_height), self.save_height(self.default_height)))
        menu.add_cascade(label="列表显示高度", menu=height_menu)

        if self.app and hasattr(self.app, "set_table_row_height"):
            row_menu = tk.Menu(menu, tearoff=False)
            current_rh = getattr(self.app, "table_row_height", DEFAULT_ROW_HEIGHT)
            for rh, label in ROW_HEIGHT_PRESETS:
                prefix = "✓ " if current_rh == rh else "  "
                row_menu.add_command(label=prefix + label,
                                     command=lambda h=rh: self.app.set_table_row_height(h))
            menu.add_cascade(label="表格行高间距", menu=row_menu)
