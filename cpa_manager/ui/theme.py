"""Centralized modern visual theme configuration for CPA Unified Manager."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk


def enable_high_dpi_awareness():
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def setup_theme(root: tk.Tk):
    enable_high_dpi_awareness()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    BG = "#f8fafc"          # Slate 50
    CARD_BG = "#ffffff"     # Pure white card
    BORDER = "#cbd5e1"      # Slate 300
    BORDER_LIGHT = "#e2e8f0"# Slate 200
    TEXT_MAIN = "#0f172a"   # Deep black / Slate 900
    TEXT_BODY = "#334155"   # Slate 700
    TEXT_MUTED = "#64748b"  # Slate 500
    PRIMARY = "#2563eb"     # Blue 600
    PRIMARY_HOVER = "#1d4ed8"
    PRIMARY_ACTIVE = "#1e40af"
    SUCCESS = "#16a34a"     # Green 600
    SUCCESS_HOVER = "#15803d"
    DANGER = "#ef4444"      # Red 500
    DANGER_HOVER = "#dc2626"
    SELECTED_BG = "#dbeafe" # Soft Sky/Blue 100 (Gentle selection highlight)
    SELECTED_FG = "#1e3a8a" # Deep readable blue on selection

    try:
        root.configure(bg=BG)
    except Exception:
        pass

    style.configure(".", font=("Microsoft YaHei UI", 9), background=BG, foreground=TEXT_BODY)
    style.configure("TFrame", background=CARD_BG)
    style.configure("Card.TFrame", background=CARD_BG)
    style.configure("Window.TFrame", background=BG)

    style.configure("TLabel", background=CARD_BG, foreground=TEXT_BODY, font=("Microsoft YaHei UI", 9))
    style.configure("Window.TLabel", background=BG, foreground=TEXT_BODY, font=("Microsoft YaHei UI", 9))
    style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT_BODY, font=("Microsoft YaHei UI", 9))
    style.configure("Muted.TLabel", background=CARD_BG, foreground=TEXT_MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("Header.TLabel", font=("Microsoft YaHei UI", 16, "bold"), foreground=TEXT_MAIN, background=BG)
    style.configure("Subheader.TLabel", font=("Microsoft YaHei UI", 10, "bold"), foreground=TEXT_MAIN, background=CARD_BG)

    style.configure("TCheckbutton", background=CARD_BG, foreground=TEXT_BODY, font=("Microsoft YaHei UI", 9))
    style.map("TCheckbutton", background=[("active", CARD_BG)])

    # Standard Button (Refined soft surface with crisp border)
    style.configure("TButton", font=("Microsoft YaHei UI", 9), background="#f1f5f9", foreground=TEXT_BODY,
                    bordercolor=BORDER, lightcolor="#ffffff", darkcolor=BORDER, relief="flat", padding=[14, 5])
    style.map("TButton",
              background=[("pressed", "#cbd5e1"), ("active", "#e2e8f0"), ("disabled", "#f8fafc")],
              foreground=[("active", TEXT_MAIN), ("disabled", "#94a3b8")],
              bordercolor=[("pressed", PRIMARY), ("active", "#94a3b8"), ("disabled", BORDER_LIGHT)])

    # Stepper and Preset Chip Buttons
    style.configure("Stepper.TButton", font=("Microsoft YaHei UI", 10, "bold"), background="#f1f5f9",
                    foreground=TEXT_BODY, bordercolor=BORDER, lightcolor="#ffffff", darkcolor=BORDER,
                    relief="flat", padding=[2, 2], width=3)
    style.map("Stepper.TButton",
              background=[("pressed", "#cbd5e1"), ("active", "#e2e8f0")],
              foreground=[("active", TEXT_MAIN)],
              bordercolor=[("active", "#94a3b8")])

    style.configure("Chip.TButton", font=("Microsoft YaHei UI", 9), background="#f1f5f9",
                    foreground=TEXT_BODY, bordercolor=BORDER, lightcolor="#ffffff", darkcolor=BORDER,
                    relief="flat", padding=[6, 3], width=0)
    style.map("Chip.TButton",
              background=[("pressed", "#cbd5e1"), ("active", "#e2e8f0")],
              foreground=[("active", TEXT_MAIN)],
              bordercolor=[("active", "#94a3b8")])

    # Primary Button (Filled Tech Blue)
    style.configure("Primary.TButton", font=("Microsoft YaHei UI", 9, "bold"), background=PRIMARY, foreground="#ffffff",
                    bordercolor=PRIMARY, lightcolor=PRIMARY, darkcolor=PRIMARY, relief="flat", padding=[14, 5])
    style.map("Primary.TButton",
              background=[("pressed", PRIMARY_ACTIVE), ("active", PRIMARY_HOVER), ("disabled", "#f1f5f9")],
              bordercolor=[("pressed", PRIMARY_ACTIVE), ("active", PRIMARY_HOVER), ("disabled", BORDER_LIGHT)],
              foreground=[("disabled", "#94a3b8")])

    # Success Button (Green, for 启动)
    style.configure("Success.TButton", font=("Microsoft YaHei UI", 9, "bold"), background=SUCCESS, foreground="#ffffff",
                    bordercolor=SUCCESS, lightcolor=SUCCESS, darkcolor=SUCCESS, relief="flat", padding=[14, 5])
    style.map("Success.TButton",
              background=[("pressed", "#15803d"), ("active", SUCCESS_HOVER), ("disabled", "#f1f5f9")],
              bordercolor=[("pressed", "#15803d"), ("active", SUCCESS_HOVER), ("disabled", BORDER_LIGHT)],
              foreground=[("disabled", "#94a3b8")])

    # Danger Button (Red, for 停止)
    style.configure("Danger.TButton", font=("Microsoft YaHei UI", 9, "bold"), background=DANGER, foreground="#ffffff",
                    bordercolor=DANGER, lightcolor=DANGER, darkcolor=DANGER, relief="flat", padding=[14, 5])
    style.map("Danger.TButton",
              background=[("pressed", "#b91c1c"), ("active", DANGER_HOVER), ("disabled", "#f1f5f9")],
              bordercolor=[("pressed", "#b91c1c"), ("active", DANGER_HOVER), ("disabled", BORDER_LIGHT)],
              foreground=[("disabled", "#94a3b8")])

    # Modern Seamless Tabs (No ugly nested gray boxes)
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=[0, 4, 0, 0])
    style.configure("TNotebook.client", background=CARD_BG, bordercolor=BORDER_LIGHT, lightcolor=BORDER_LIGHT, darkcolor=BORDER_LIGHT)
    style.configure("TNotebook.Tab", background=BG, foreground=TEXT_MUTED,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG,
                    font=("Microsoft YaHei UI", 10, "bold"), padding=[20, 8])
    style.map("TNotebook.Tab",
              background=[("selected", CARD_BG), ("active", "#f1f5f9")],
              foreground=[("selected", PRIMARY), ("active", TEXT_MAIN)],
              bordercolor=[("selected", BORDER_LIGHT), ("active", BG)],
              lightcolor=[("selected", CARD_BG)],
              darkcolor=[("selected", CARD_BG)])

    # Entry
    style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT_MAIN,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=[6, 4])
    style.map("TEntry", bordercolor=[("focus", PRIMARY), ("hover", "#94a3b8")])

    # Flat Sleek Scrollbars
    style.configure("TScrollbar",
                    background="#cbd5e1",
                    troughcolor="#f8fafc",
                    bordercolor="#f8fafc",
                    lightcolor="#cbd5e1",
                    darkcolor="#cbd5e1",
                    arrowcolor="#64748b",
                    arrowsize=11,
                    relief="flat",
                    borderwidth=0)
    style.map("TScrollbar",
              background=[("active", "#94a3b8"), ("pressed", "#64748b")],
              arrowcolor=[("active", "#334155"), ("pressed", "#0f172a")])

    # Combobox
    style.configure("TCombobox", fieldbackground=CARD_BG, foreground=TEXT_MAIN,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=[5, 4],
                    arrowcolor="#64748b", arrowsize=11)
    style.map("TCombobox", bordercolor=[("focus", PRIMARY), ("hover", "#94a3b8")])

    # Labelframe (Card style)
    style.configure("TLabelframe", background=CARD_BG, bordercolor=BORDER_LIGHT, borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=CARD_BG, foreground=TEXT_MAIN,
                    font=("Microsoft YaHei UI", 9, "bold"))

    # Separator
    style.configure("TSeparator", background=BORDER_LIGHT)

    # Treeview & unified heading colors across all tables
    HEADING_BG = "#f1f5f9"
    HEADING_FG = "#334155"
    HEADING_ACTIVE = "#e2e8f0"

    style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG, foreground=TEXT_MAIN,
                    bordercolor=BORDER_LIGHT, borderwidth=1, font=("Microsoft YaHei UI", 9), rowheight=32)
    style.map("Treeview", background=[("selected", SELECTED_BG)], foreground=[("selected", SELECTED_FG)])

    for heading_style in ("Treeview.Heading", "FrozenRows.Treeview.Heading", "FrozenActions.Treeview.Heading"):
        style.configure(heading_style, background=HEADING_BG, foreground=HEADING_FG,
                        font=("Microsoft YaHei UI", 9, "bold"), bordercolor=BORDER_LIGHT, relief="flat", padding=[4, 6])
        style.map(heading_style, background=[("active", HEADING_ACTIVE)])

    # Progressbar
    style.configure("TProgressbar", troughcolor="#f1f5f9", background=PRIMARY, bordercolor=BORDER_LIGHT, relief="flat")


def style_log_widget(widget):
    widget.configure(
        bg="#ffffff",
        fg="#1e293b",
        insertbackground="#0f172a",
        selectbackground="#dbeafe",
        selectforeground="#1e3a8a",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground="#e2e8f0",
        highlightcolor="#3b82f6",
        font=("Microsoft YaHei UI", 9),
        padx=10,
        pady=8,
        spacing1=2,
        spacing3=2,
    )
