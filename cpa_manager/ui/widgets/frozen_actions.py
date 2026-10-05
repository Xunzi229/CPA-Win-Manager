"""Fixed row actions alongside a horizontally scrollable Treeview."""
import tkinter as tk
from tkinter import ttk
from cpa_manager.ui.widgets.help_hint import HelpHint


class FrozenActions:
    def __init__(self, parent, table, actions, invoke, allowed, close_editor, visible=None, enabled=None, label=None, help_text=None):
        self.table, self.actions = table, actions
        self.invoke, self.allowed, self.close_editor = invoke, allowed, close_editor
        self.visible = visible or (lambda row, action: True)
        self.enabled = enabled or (lambda row, action: True)
        self.label = label or (lambda row, action, text: text)
        self.help_text = help_text or {}
        self.hints = {}
        self.syncing = False
        self.mirrored_selection = {}
        self.scrollbar = None
        self.buttons = {}
        self.render_timer = None
        style = ttk.Style(table)
        current_rh = style.lookup("FrozenRows.Treeview", "rowheight")
        try:
            current_rh = int(current_rh) if current_rh else 32
        except (ValueError, TypeError):
            current_rh = 32
        style.configure("FrozenRows.Treeview", rowheight=current_rh)
        style.configure("FrozenActions.Treeview", rowheight=current_rh)
        # Both panes share an edge; omit native widget borders at that seam.
        for name in ("FrozenRows.Treeview", "FrozenActions.Treeview"):
            style.layout(name, [("Treeview.padding", {"sticky": "nswe", "children": [
                ("Treeview.treearea", {"sticky": "nswe"})]})])
            style.configure(name, borderwidth=0, padding=0, relief="flat")
        style.map("FrozenActions.Treeview", background=[("selected", "#eef2f5")],
                  foreground=[("selected", "#475569")])
        for heading_style in ("FrozenRows.Treeview.Heading", "FrozenActions.Treeview.Heading"):
            style.configure(heading_style, background="#f1f5f9", foreground="#334155",
                            font=("Microsoft YaHei UI", 9, "bold"), bordercolor="#e2e8f0", relief="flat", padding=[4, 6])
            style.map(heading_style, background=[("active", "#e2e8f0")])
        table.configure(style="FrozenRows.Treeview")
        self.tree = ttk.Treeview(parent, columns=[a[0] for a in actions], show="headings",
                                 selectmode="browse", height=table.cget("height"), style="FrozenActions.Treeview")
        for key, label, width in actions:
            self.tree.heading(key, text=label, anchor="center")
            self.tree.column(key, width=width, minwidth=width, stretch=False, anchor="center")
        self.tree.tag_configure("action", foreground="#475569")
        self.tree.tag_configure("disabled", foreground="#888888")
        table.configure(yscrollcommand=lambda first, last: self.sync_scroll(table, self.tree, first, last))
        self.tree.configure(yscrollcommand=lambda first, last: self.sync_scroll(self.tree, table, first, last))
        table.bind("<<TreeviewSelect>>", lambda _: self.sync_selection(table, self.tree), add="+")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.sync_selection(self.tree, table))
        self.tree.bind("<ButtonRelease-1>", self.clicked)
        self.tree.bind("<ButtonPress-1>", self.block_resize)
        self.tree.bind("<Motion>", self.hover)
        self.tree.bind("<Configure>", lambda _: self.schedule_render(), add="+")
        self.tree.bind("<Map>", lambda _: self.schedule_render(), add="+")
        self.tree.bind("<Destroy>", self.cancel_render, add="+")

    def cancel_render(self, event):
        if event.widget is self.tree and self.render_timer is not None:
            self.tree.winfo_toplevel().after_cancel(self.render_timer)
            self.render_timer = None

    def sync_scroll(self, source, peer, first, last):
        if self.syncing:
            return
        self.syncing = True
        try:
            if abs(peer.yview()[0] - float(first)) > 0.000001:
                self.close_editor()
                peer.yview_moveto(first)
            if self.scrollbar:
                self.scrollbar.set(first, last)
        finally:
            self.syncing = False
            self.schedule_render()

    def yview(self, *args):
        self.close_editor()
        self.table.yview(*args)

    def sync_selection(self, source, peer):
        selected = source.selection()
        if self.mirrored_selection.pop(source, None) == selected:
            return
        if peer.selection() != selected:
            if peer is self.table:
                self.close_editor()
            self.mirrored_selection[peer] = selected
            peer.selection_set(selected)

    def update(self, row):
        values = [label if self.visible(row, action) else "" for action, label, _ in self.actions]
        if self.tree.exists(row):
            self.tree.item(row, values=values)
        else:
            self.tree.insert("", "end", iid=row, values=values, tags=("action",))
        self.schedule_render()

    def remove(self, row):
        if self.tree.exists(row):
            self.tree.delete(row)
        for key in list(self.buttons):
            if key[0] == row:
                self.buttons.pop(key).destroy()
                if key in self.hints:
                    self.hints.pop(key).hide()
        self.schedule_render()

    def set_enabled(self, enabled):
        for row in self.tree.get_children():
            self.tree.item(row, tags=("action" if enabled else "disabled",))
        if not enabled:
            self.tree.configure(cursor="")
        self.schedule_render()

    def schedule_render(self):
        if getattr(self, "badges", None) is not None:
            self.badges.schedule_render()
        if self.render_timer is None:
            self.render_timer = self.tree.winfo_toplevel().after_idle(self.render_buttons)

    @staticmethod
    def palette(action):
        if action in ("install", "update"):
            return "#edf2f7", "#486581", "#e1e9f1", "#d6dfe8"
        if action == "clear":
            return "#f7f5f4", "#766b65", "#eee9e6", "#e3ddda"
        return "#f6f7f8", "#536171", "#e9edf1", "#dde2e7"

    def render_buttons(self):
        self.render_timer = None
        visible = set()
        for row in self.tree.get_children():
            for action, label, _ in self.actions:
                if not self.visible(row, action):
                    continue
                bounds = self.tree.bbox(row, action)
                if not bounds:
                    continue
                x, y, width, height = bounds
                key = (row, action)
                enabled = self.allowed() and self.enabled(row, action)
                visible.add(key)
                background, foreground, hover, border = self.palette(action)
                button = self.buttons.get(key)
                if button is None:
                    button = tk.Button(self.tree, text=label, font=("Microsoft YaHei UI", 9),
                                       relief="flat", bd=0, highlightthickness=1,
                                       padx=2, pady=0, activeforeground=foreground, activebackground=hover,
                                       disabledforeground="#94a3b8",
                                       command=lambda r=row, a=action: self.activate(r, a))
                    button.bind("<Enter>", lambda _, b=button, color=hover: b.configure(background=color) if str(b.cget("state")) == "normal" else None)
                    button.bind("<Leave>", lambda _, b=button, color=background: b.configure(background=color) if str(b.cget("state")) == "normal" else None)
                    self.buttons[key] = button
                    if action in self.help_text:
                        self.hints[key] = HelpHint(button, self.help_text[action])
                button.configure(text=self.label(row, action, label), state="normal" if enabled else "disabled",
                                 background=background if enabled else "#f1f5f9", foreground=foreground,
                                 highlightbackground=border if enabled else "#e2e8f0",
                                 highlightcolor=border, cursor="hand2" if enabled else "")
                button.place(x=x + 3, y=y + 3, width=width - 6, height=height - 6)
        for key in list(self.buttons):
            if key not in visible:
                self.buttons.pop(key).destroy()
                if key in self.hints:
                    self.hints.pop(key).hide()

    def activate(self, row, action):
        if not self.allowed() or not self.table.exists(row) or not self.visible(row, action) or not self.enabled(row, action):
            return
        self.close_editor()
        self.table.selection_set(row)
        self.tree.selection_set(row)
        self.invoke(row, action)

    def block_resize(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator" or region == "cell" and not self.allowed():
            return "break"

    def hover(self, event):
        cell = self.tree.identify_region(event.x, event.y) == "cell"
        if cell:
            row = self.tree.identify_row(event.y)
            column = self.tree.identify_column(event.x)
            action = self.actions[int(column[1:]) - 1][0] if column else None
            cell = bool(row and action and self.visible(row, action) and self.enabled(row, action))
        self.tree.configure(cursor="hand2" if cell and self.allowed() else "")

    def clicked(self, event):
        if not self.allowed() or self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row or not column or not self.table.exists(row):
            return
        self.activate(row, self.actions[int(column[1:]) - 1][0])
