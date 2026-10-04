"""Delayed contextual hints attached directly to existing controls."""
import tkinter as tk
from cpa_manager.core.runtime import monitor_work_area


class HelpHint:
    def __init__(self, widget, text):
        self.text = text
        self.owner = widget.winfo_toplevel()
        self.widget = widget
        self.timer = None
        self.popup = None
        for event, handler in (("<Enter>", self.schedule), ("<Leave>", self.hide),
                               ("<ButtonPress>", self.hide), ("<FocusOut>", self.hide),
                               ("<Escape>", self.hide), ("<Destroy>", self.hide)):
            self.widget.bind(event, handler, add="+")

    def schedule(self, *_):
        self.hide()
        self.timer = self.owner.after(800, self.show)

    def show(self, *_):
        self.hide()
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        self.popup = tk.Toplevel(self.owner)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        label = tk.Label(self.popup, text=self.text, justify="left", wraplength=380,
                         background="#f8fafc", foreground="#334155", padx=10, pady=8,
                         relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 9))
        label.pack()
        self.popup.update_idletasks()
        width, height = self.popup.winfo_reqwidth(), self.popup.winfo_reqheight()
        left, top, right, bottom = monitor_work_area(self.widget.winfo_rootx(), self.widget.winfo_rooty(),
                                                   (0, 0, self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()))
        x = max(left + 8, min(self.widget.winfo_rootx(), right - width - 8))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if y + height > bottom:
            y = max(top + 8, self.widget.winfo_rooty() - height - 4)
        self.popup.geometry(f"+{x}+{y}")
        self.popup.deiconify()

    def hide(self, *_):
        if self.timer is not None:
            self.owner.after_cancel(self.timer)
            self.timer = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None


def add_help(widget, text):
    return HelpHint(widget, text)
