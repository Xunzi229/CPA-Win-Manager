"""Shared bottom log panel with drag-to-resize height and clear-log controls."""
import time
import tkinter as tk
from tkinter import ttk, scrolledtext

from cpa_manager.ui.theme import style_log_widget
from cpa_manager.ui.widgets.help_hint import add_help

MIN_LOG_LINES = 3
MAX_LOG_LINES = 30
DEFAULT_LOG_LINES = 6
EXPANDED_LOG_LINES = 14
LOG_LINE_PIXELS = 21


def clamp_log_lines(value, default=DEFAULT_LOG_LINES):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        lines = int(value)
    except (ValueError, TypeError):
        return default
    return max(MIN_LOG_LINES, min(MAX_LOG_LINES, lines))


def write_log(widget, text):
    if widget is None or not widget.winfo_exists() or not text:
        return
    lines = str(text).splitlines()
    stamp = time.strftime("%H:%M:%S ")
    widget.configure(state="normal")
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        if len(line_str) >= 9 and line_str[2] == ":" and line_str[5] == ":" and line_str[8] == " ":
            widget.insert("end", line_str + "\n")
        else:
            widget.insert("end", stamp + line_str + "\n")
    widget.see("end")
    widget.configure(state="disabled")


def clear_log(widget):
    if widget is None or not widget.winfo_exists():
        return
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.configure(state="disabled")


def ensure_shared_log(app, window):
    existing = getattr(app, "log", None)
    if existing is not None and existing.winfo_exists():
        return existing
    widget = scrolledtext.ScrolledText(window, height=DEFAULT_LOG_LINES, state="disabled")
    style_log_widget(widget)
    if app is not None:
        app.log = widget
    return widget


class LogPanel:
    def __init__(self, parent, save_height=None, initial_height=DEFAULT_LOG_LINES, default_height=DEFAULT_LOG_LINES):
        self.parent = parent
        self.save_height = save_height
        self.default_height = clamp_log_lines(default_height)
        self.dragging = False
        self.drag_start_y = 0
        self.drag_start_lines = self.default_height

        self.frame = ttk.Frame(parent, style="Window.TFrame")
        self.header = tk.Frame(self.frame, bg="#f8fafc")
        self.header.pack(fill="x", pady=(2, 4))

        self.title_label = tk.Label(
            self.header, text="运行日志", bg="#f8fafc", fg="#475569",
            font=("Microsoft YaHei UI", 9, "bold")
        )
        self.title_label.pack(side="left")

        self.clear_button = ttk.Button(
            self.header, text="清空日志", command=self.clear, padding=(10, 2), width=0
        )
        self.clear_button.pack(side="right")

        self.bar = tk.Frame(self.header, height=22, cursor="sb_v_double_arrow", bg="#f8fafc")
        self.bar.pack(side="left", fill="both", expand=True, padx=10)
        self.handle = tk.Frame(self.bar, width=42, height=4, bg="#cbd5e1", cursor="sb_v_double_arrow")
        self.handle.place(relx=0.5, rely=0.5, anchor="center")

        for widget in (self.bar, self.handle):
            widget.bind("<Enter>", self.on_bar_enter)
            widget.bind("<Leave>", self.on_bar_leave)
            widget.bind("<ButtonPress-1>", self.on_bar_press)
            widget.bind("<B1-Motion>", self.on_bar_drag)
            widget.bind("<ButtonRelease-1>", self.on_bar_release)
            widget.bind("<Double-Button-1>", self.on_bar_double_click)

        self.text = scrolledtext.ScrolledText(
            self.frame, height=clamp_log_lines(initial_height, self.default_height), state="disabled"
        )
        style_log_widget(self.text)
        self.text.pack(fill="both", expand=True)

        for widget in (self.bar, self.handle, self.title_label, self.text):
            widget.bind("<Button-3>", self.open_context_menu, add="+")

        self.help_hints = [
            add_help(self.bar, "上下拖动调整日志框高度，双击切换展开/默认高度；右键可查看更多选项。"),
            add_help(self.clear_button, "清空当前运行日志内容。"),
        ]

    def current_lines(self):
        try:
            return int(self.text.cget("height"))
        except (ValueError, TypeError):
            return self.default_height

    def max_drag_lines(self):
        try:
            win_height = self.parent.winfo_height()
        except Exception:
            win_height = 0
        if win_height > 300:
            return max(MIN_LOG_LINES, min(MAX_LOG_LINES, (win_height - 240) // LOG_LINE_PIXELS))
        return MAX_LOG_LINES

    def apply_height(self, lines):
        target = clamp_log_lines(lines, self.default_height)
        self.text.configure(height=target)
        return target

    def adjust_lines(self, delta):
        target = self.apply_height(self.current_lines() + delta)
        if self.save_height:
            self.save_height(target)
        return target

    def write(self, text):
        write_log(self.text, text)

    def clear(self):
        clear_log(self.text)

    def on_bar_enter(self, _event=None):
        if not self.dragging:
            self.handle.configure(bg="#3b82f6")

    def on_bar_leave(self, _event=None):
        if not self.dragging:
            self.handle.configure(bg="#cbd5e1")

    def on_bar_press(self, event):
        self.dragging = True
        self.drag_start_y = event.y_root
        self.drag_start_lines = self.current_lines()
        self.handle.configure(bg="#2563eb")

    def on_bar_drag(self, event):
        if not self.dragging:
            return
        dy = self.drag_start_y - event.y_root
        delta_lines = int(round(dy / LOG_LINE_PIXELS))
        target = max(MIN_LOG_LINES, min(self.max_drag_lines(), self.drag_start_lines + delta_lines))
        if target != self.current_lines():
            self.apply_height(target)

    def on_bar_release(self, _event=None):
        if self.dragging:
            self.dragging = False
            self.handle.configure(bg="#cbd5e1")
            if self.save_height:
                self.save_height(self.current_lines())

    def on_bar_double_click(self, _event=None):
        expanded = min(self.max_drag_lines(), EXPANDED_LOG_LINES)
        target = self.default_height if self.current_lines() != self.default_height else expanded
        self.apply_height(target)
        if self.save_height:
            self.save_height(target)

    def copy_selection(self):
        if not self.text.tag_ranges("sel"):
            return
        selected = self.text.get("sel.first", "sel.last")
        self.text.clipboard_clear()
        self.text.clipboard_append(selected)

    def open_context_menu(self, event):
        menu = tk.Menu(self.frame, tearoff=False)
        has_sel = bool(self.text.tag_ranges("sel"))
        has_text = bool(self.text.get("1.0", "end").strip())
        menu.add_command(label="复制选中内容", command=self.copy_selection, state="normal" if has_sel else "disabled")
        menu.add_command(label="清空日志", command=self.clear, state="normal" if has_text else "disabled")
        menu.add_separator()
        height_menu = tk.Menu(menu, tearoff=False)
        height_menu.add_command(label="切换展开 / 默认高度", command=lambda: self.on_bar_double_click(None))
        height_menu.add_command(label="加高 (+2 行)", command=lambda: self.adjust_lines(2))
        height_menu.add_command(label="降低 (-2 行)", command=lambda: self.adjust_lines(-2))
        height_menu.add_command(
            label=f"恢复默认高度 ({self.default_height} 行)",
            command=lambda: self.adjust_lines(self.default_height - self.current_lines()),
        )
        menu.add_cascade(label="日志框显示高度", menu=height_menu)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
