"""Column sorting and persistent pinned rows shared by software tables."""
import re


def sort_value(value, column):
    text = str(value).removeprefix("↑ ").strip()
    if column == "size":
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|K[i]?B|M[i]?B|G[i]?B|T[i]?B)", text, re.I)
        if match:
            units = {"b": 0, "kib": 1, "mib": 2, "gib": 3, "tib": 4}
            unit = match[2].lower()
            unit = unit if unit == "b" or "i" in unit else unit[0] + "ib"
            return (0, float(match[1]) * 1024 ** units[unit])
        return (1, 0)
    if text in ("", "—", "尚无记录"):
        return (1, ())
    if column in ("local", "latest", "version", "downloaded"):
        text = re.sub(r"^[vV](?=\d)", "", text)
    # Natural ordering makes 1.10 greater than 1.2 and handles dated versions.
    return (0, tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                     for part in re.split(r"(\d+)", text) if part))


class TableOrder:
    def __init__(self, table, actions, profiles, persist, allowed, close_editor):
        self.table, self.actions = table, actions
        self.profiles, self.persist = profiles, persist
        self.allowed, self.close_editor = allowed, close_editor
        self.column = None
        self.descending = False
        self.labels = {key: table.heading(key, "text") for key in table["columns"]}
        self.first_column = table["columns"][0]
        table.tag_configure("pinned", foreground="#2563eb")
        for key in self.labels:
            table.heading(key, command=lambda column=key: self.sort(column))

    def sort(self, column):
        if not self.allowed():
            return
        self.close_editor()
        self.descending = not self.descending if self.column == column else False
        self.column = column
        self.apply()

    def apply(self):
        profiles = {p["id"]: p for p in self.profiles()}
        rows = list(self.table.get_children())
        # Original configuration order is the stable tie breaker.
        index = {row: position for position, row in enumerate(profiles)}
        rows.sort(key=lambda row: index.get(row, len(index)))
        if self.column:
            rows.sort(key=lambda row: sort_value(self.table.set(row, self.column), self.column),
                      reverse=self.descending)
        rows.sort(key=lambda row: profiles.get(row, {}).get("pinned") is not True)
        for position, row in enumerate(rows):
            pinned = profiles.get(row, {}).get("pinned") is True
            tags = [tag for tag in self.table.item(row, "tags") if tag != "pinned"]
            self.table.item(row, tags=tags + (["pinned"] if pinned else []))
            label = self.table.set(row, self.first_column).removeprefix("↑ ")
            self.table.set(row, self.first_column, ("↑ " if pinned else "") + label)
            self.table.move(row, "", position)
            if self.actions.tree.exists(row):
                self.actions.tree.move(row, "", position)
        for key, label in self.labels.items():
            suffix = (" ▼" if self.descending else " ▲") if key == self.column else ""
            self.table.heading(key, text=label + suffix)
        self.actions.schedule_render()

    def toggle_pin(self, row):
        if not self.allowed():
            return
        profile = next((p for p in self.profiles() if p["id"] == row), None)
        if profile is None:
            return
        self.close_editor()
        old = profile.get("pinned", False)
        profile["pinned"] = old is not True
        if self.persist() is False:
            profile["pinned"] = old
        self.apply()

    def add_pin_menu(self, menu, row):
        profile = next((p for p in self.profiles() if p["id"] == row), {})
        menu.add_command(label="取消置顶" if profile.get("pinned") is True else "置顶此行",
                         command=lambda: self.toggle_pin(row))
        menu.add_separator()
