"""Persistent record identifiers and UI-independent selection state."""
import uuid


def ensure_ids(profiles):
    seen = set()
    for profile in profiles:
        value = profile.get("id")
        if not isinstance(value, str) or not value or value in seen:
            value = uuid.uuid4().hex
            profile["id"] = value
        seen.add(value)


class ChoiceState:
    """Selection data independent of a hidden Tk widget."""
    def __init__(self, variable):
        self.variable = variable
        self.values = []

    def configure(self, **options):
        if "values" in options:
            self.values = list(options["values"])

    def current(self, index=None):
        if index is not None:
            self.set(self.values[index])
        try:
            return self.values.index(self.get())
        except ValueError:
            return -1

    def get(self):
        return self.variable.get()

    def set(self, value):
        self.variable.set(value)

    def __getitem__(self, key):
        return tuple(self.values) if key == "values" else None
