"""Red update markers aligned with visible version cells."""
import tkinter as tk
from tkinter import ttk


class TableBadges:
    def __init__(self, table, column):
        self.table, self.column = table, column
        self.rows = set()
        self.labels = {}
        self.timer = None
        self.icon = tk.PhotoImage(master=table, width=10, height=10)
        for y in range(10):
            for x in range(10):
                if (x - 4.5) ** 2 + (y - 4.5) ** 2 <= 16:
                    self.icon.put("#e53935", (x, y))
        for event in ("<Configure>", "<Map>", "<<TreeviewSelect>>", "<ButtonRelease-1>"):
            table.bind(event, lambda _: self.schedule_render(), add="+")

    def set(self, row, enabled):
        if enabled:
            self.rows.add(row)
        else:
            self.rows.discard(row)
        self.schedule_render()

    def schedule_render(self):
        if self.timer is None:
            self.timer = self.table.winfo_toplevel().after_idle(self.render)

    def render(self):
        self.timer = None
        visible = set()
        selection = self.table.selection()
        style = ttk.Style(self.table)
        for row in list(self.rows):
            if not self.table.exists(row):
                self.rows.discard(row)
                continue
            bounds = self.table.bbox(row, self.column)
            if not bounds:
                continue
            x, y, width, height = bounds
            if x + 4 < 0 or x + 16 > self.table.winfo_width():
                continue
            visible.add(row)
            label = self.labels.get(row)
            if label is None:
                label = tk.Label(self.table, image=self.icon, borderwidth=0, highlightthickness=0, cursor="hand2")
                label.bind("<Button-1>", lambda _, r=row: (self.table.selection_set(r), self.table.event_generate("<<TreeviewSelect>>")))
                label.bind("<Button-3>", lambda event, r=row: (self.table.selection_set(r), self.table.event_generate("<<TreeviewSelect>>"), self.table.event_generate("<Button-3>", x=event.x_root - self.table.winfo_rootx(), y=event.y_root - self.table.winfo_rooty())))
                try:
                    from cpa_manager.ui.widgets.help_hint import HelpHint
                    HelpHint(label, "检测到新版本，可点击行右侧按钮进行升级更新。")
                except Exception:
                    pass
                self.labels[row] = label
            background = style.lookup(self.table.cget("style") or "Treeview", "background",
                                      state=("selected",) if row in selection else ()) or "#ffffff"
            label.configure(background=background)
            label.place(x=x + 4, y=y + (height - 10) // 2, width=10, height=10)
        for row in list(self.labels):
            if row not in visible:
                self.labels.pop(row).destroy()
