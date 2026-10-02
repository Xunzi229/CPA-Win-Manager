"""Inline choice editors for ttk tables."""
from tkinter import ttk


class TableChoices:
    def __init__(self, table, choices, commit, allowed=lambda: True, text_value=None, text_commit=None):
        self.table, self.choices, self.commit, self.allowed = table, choices, commit, allowed
        self.editor = None
        self.text_value, self.text_commit = text_value, text_commit
        table.bind("<Double-1>", self.edit_text, add="+")
        table.bind("<ButtonPress-1>", lambda _: self.close(), add="+")
        table.bind("<ButtonRelease-1>", self.open, add="+")
        table.bind("<Configure>", lambda _: self.close(), add="+")
        table.bind("<MouseWheel>", lambda _: self.close(), add="+")

    def close(self):
        if self.editor is not None:
            self.editor.destroy()
            self.editor = None

    def open(self, event):
        if isinstance(self.editor, ttk.Entry) and not isinstance(self.editor, ttk.Combobox):
            return
        self.close()
        if not self.allowed() or self.table.identify_region(event.x, event.y) != "cell":
            return
        row = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        key = self.table["columns"][int(column[1:]) - 1]
        values, current = self.choices(row, key)
        if not values:
            return
        bounds = self.table.bbox(row, column)
        if not bounds:
            return
        x, y, width, height = bounds
        self.editor = ttk.Combobox(self.table, values=values, state="readonly")
        self.editor.set(current)
        self.editor.place(x=x, y=y, width=width, height=height)
        self.editor.focus_set()
        def selected(_):
            value = self.editor.get()
            self.close()
            self.commit(row, key, value)
        self.editor.bind("<<ComboboxSelected>>", selected)
        self.editor.bind("<Escape>", lambda _: self.close())

    def edit_text(self, event):
        if not self.text_value or not self.allowed() or self.table.identify_region(event.x, event.y) != "cell":
            return
        row = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        key = self.table["columns"][int(column[1:]) - 1]
        value = self.text_value(row, key)
        if value is None:
            return
        self.close()
        bounds = self.table.bbox(row, column)
        if not bounds:
            return
        x, y, width, height = bounds
        self.editor = ttk.Entry(self.table, justify="center")
        self.editor.insert(0, value)
        self.editor.place(x=x, y=y, width=width, height=height)
        self.editor.focus_set()
        self.editor.selection_range(0, "end")
        def save(_):
            if self.text_commit(row, key, self.editor.get().strip()):
                self.close()
            return "break"
        self.editor.bind("<Return>", save)
        self.editor.bind("<Escape>", lambda _: self.close())
