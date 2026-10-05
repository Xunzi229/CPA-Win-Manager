"""Dedicated table for GitHub installers."""
import copy
import inspect
import os


def _accepts_workers(func):
    fn = getattr(func, "side_effect", None) or func
    if not callable(fn):
        return False
    try:
        sig = inspect.signature(fn)
        return "workers" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except (ValueError, TypeError):
        return False
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext
import uuid
import webbrowser

from cpa_manager.backends import github
from cpa_manager.backends import installer as backend
from cpa_manager.core.download import DownloadControl, DownloadCancelled, size_text
from cpa_manager.ui.widgets.table_choices import TableChoices
from cpa_manager.ui.widgets.table_order import TableOrder
from cpa_manager.ui.theme import style_log_widget
from cpa_manager.ui.widgets.table_resizer import TableResizer, DEFAULT_INSTALLER_ROWS
from cpa_manager.ui.widgets.frozen_actions import FrozenActions
from cpa_manager.ui.widgets.table_badges import TableBadges
from cpa_manager.ui.widgets.dialog_position import center_dialog
from cpa_manager.ui.widgets.software_library import SoftwareLibrary
from cpa_manager.core.models import ChoiceState
from cpa_manager.core.software_tasks import shared_tasks
from cpa_manager.core.installed_software import scan_installed, match_installed, launch_uninstaller, installed_update_available


class InstallerPage:
    key = "installers"

    @property
    def checking(self):
        return self.tasks.has_page(self.key, "check")

    @property
    def pending_downloads(self):
        return self.tasks.has_page(self.key, "download")

    def row_pending(self, row):
        return (self.key, row) in self.tasks.jobs

    def __init__(self, app, notebook):
        self.app, self.window = app, app.window
        self.busy = False
        self.tasks = shared_tasks(app)
        self.events = queue.Queue()
        self.control = DownloadControl()
        self.row_states = {}
        self.installed_records = []
        self.local_scanning = False
        self.local_scanned = False
        self.widgets = []
        self.frame = ttk.Frame(notebook, padding=16)
        self.repository = tk.StringVar()
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=(0, 10))
        for btn_text, action in (("全部检查", lambda: self.run("check", all_rows=True)),
                                 ("刷新本地版本", self.refresh_installed),
                                 ("下载全部待更新", lambda: self.run("download", all_rows=True)),
                                 ("清空全部下载包", lambda: self.clear(True)),
                                 ("打开下载目录", self.open_folder),
                                 ("移除记录", self.remove)):
            self.button(toolbar, btn_text, action)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="GitHub 地址：").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.repository)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _: self.add())
        self.widgets.append(entry)
        button = ttk.Button(row, text="添加软件", command=self.add, style="Primary.TButton")
        button.pack(side="left", padx=(8, 0))
        self.widgets.append(button)
        library_button = ttk.Button(row, text="软件库", command=self.open_software_library)
        library_button.pack(side="left", padx=(8, 0))
        self.widgets.append(library_button)
        ttk.Label(self.frame, text="本地版本来自 Windows 已安装软件记录；右键可关联软件、查看安装目录。双击名称或 GitHub 地址可编辑。",
                  wraplength=880).pack(anchor="w", pady=(0, 8))
        area = ttk.Frame(self.frame)
        area.pack(fill="x", expand=False, pady=(0, 2))
        columns = ("name", "repository", "version", "package", "size", "local")
        installer_rows = getattr(self.app, "table_height_installer", DEFAULT_INSTALLER_ROWS) if self.app else DEFAULT_INSTALLER_ROWS
        self.table = ttk.Treeview(area, columns=columns, show="headings", selectmode="browse", height=installer_rows)
        titles = ("软件", "GitHub 地址", "选择安装版本 ▾", "对应包 ▾", "包大小", "本地安装版本")
        widths = (110, 240, 110, 220, 90, 110)
        stretches = (False, True, False, True, False, False)
        for key, title, width, stretch in zip(columns, titles, widths, stretches):
            self.table.heading(key, text=title, anchor="center")
            self.table.column(key, width=width, minwidth=70, stretch=stretch, anchor="center")
        self.row_actions = FrozenActions(area, self.table,
            (("check", "检查", 52), ("install", "安装", 52), ("uninstall", "卸载", 52)),
            self.run_row_action, lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            lambda: self.inline.close() if hasattr(self, "inline") else None,
            visible=self.row_action_visible,
            enabled=lambda row, action: not self.row_pending(row), label=self.action_label)
        self.update_badges = TableBadges(self.table, "local")
        self.row_actions.badges = self.update_badges
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.row_actions.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=lambda *args: (self.inline.close(), self.table.xview(*args)))
        self.row_actions.scrollbar = vertical
        self.table.configure(xscrollcommand=lambda first, last: (horizontal.set(first, last), self.update_badges.schedule_render()))
        self.table.grid(row=0, column=0, sticky="nsew")
        self.row_actions.tree.grid(row=0, column=1, sticky="ns")
        vertical.grid(row=0, column=2, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=0)
        self.resizer = TableResizer(self.frame, self.table, self.row_actions,
                                    save_height=lambda h: self.app.set_table_height("installer", h) if self.app else None,
                                    default_height=DEFAULT_INSTALLER_ROWS, app=self.app)
        self.resizer.bar.pack(fill="x", pady=(2, 6))
        self.table.bind("<<TreeviewSelect>>", lambda _: self.show_selection(), add="+")
        self.table.bind("<Double-1>", self.rename)
        self.table.bind("<Button-3>", self.context_menu)
        self.inline = TableChoices(self.table, self.inline_choices, self.inline_commit, lambda: not self.busy,
                                   self.address_value, self.address_commit)
        self.table_order = TableOrder(self.table, self.row_actions, lambda: self.app.installer_profiles,
                                      self.persist, lambda: not self.busy and not self.app.manager_busy,
                                      self.inline.close)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="当前行附件：").pack(side="left")
        self.package = ChoiceState(tk.StringVar())
        row.pack_forget()
        ttk.Label(self.frame, text="右侧操作列固定显示，点击即可操作该行；底部横向滚动条可查看软件信息，点击对应包可选择附件。").pack(anchor="w", pady=4)
        self.status = tk.StringVar(value="填写 GitHub 地址添加软件，或选择一行检查、安装、更新。")
        ttk.Label(self.frame, textvariable=self.status, wraplength=880).pack(anchor="w", pady=8)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 6))
        self.progress = ttk.Progressbar(row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.pause = ttk.Button(row, text="⏸", width=3, command=self.toggle_pause)
        self.cancel_button = ttk.Button(row, text="取消任务", command=self.request_stop)

        self.log = scrolledtext.ScrolledText(self.frame, state="disabled", height=4)
        style_log_widget(self.log)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + "安装向导软件管理页面已就绪。\n")
        self.log.configure(state="disabled")
        for profile in app.installer_profiles:
            self.update_row(profile)
        self.window.after(100, self.poll)
        self.window.after(300, self.refresh_installed)
        self.window.after(30000, self.poll_installed)

    def button(self, parent, text, action):
        btn_style = "Primary.TButton" if text == "下载全部待更新" else "TButton"
        button = ttk.Button(parent, text=text, command=action, style=btn_style, padding=(8, 5), width=0)
        button.pack(side="left", padx=(0, 4))
        self.widgets.append(button)

    def row_action_visible(self, row, action):
        if action != "uninstall":
            return True
        profile = next((p for p in self.app.installer_profiles if p["id"] == row), None)
        return profile is not None and match_installed(profile, self.installed_records) is not None

    def action_label(self, row, action, text):
        job = self.tasks.jobs.get((self.key, row))
        if job and action == ("check" if job.kind == "check" else "install"):
            return "检查中" if job.kind == "check" else "下载中" if job.started else "排队中"
        return text

    def run_row_action(self, row, action):
        if self.busy or self.row_pending(row) or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        if not any(p["id"] == row for p in self.app.installer_profiles):
            return
        self.table.selection_set(row)
        self.show_selection()
        self.run(action)

    def context_menu(self, event):
        row = self.table.identify_row(event.y)
        self.inline.close()
        if self.busy or (self.app and self.app.manager_busy):
            return
        menu = tk.Menu(self.table, tearoff=False)
        if row:
            self.table.selection_set(row)
            self.show_selection()
            self.table_order.add_pin_menu(menu, row)
            menu.add_command(label="检查此行", command=lambda: self.run("check"))
            menu.add_command(label="安装", command=lambda: self.run("install"))
            if self.row_action_visible(row, "uninstall"):
                menu.add_command(label="卸载", command=lambda: self.run("uninstall"))
            menu.add_separator()
            menu.add_command(label="打开下载目录", command=self.open_folder)
            menu.add_command(label="关联已安装软件", command=self.associate_installed)
            if self.profile().get("installed_id") or self.row_action_visible(row, "uninstall"):
                menu.add_command(label="取消关联", command=self.unbind_installed)
            menu.add_command(label="刷新本地版本", command=self.refresh_installed)
            menu.add_command(label="打开安装目录", command=self.open_installed_folder)
            menu.add_command(label="复制安装目录", command=self.copy_installed_folder)
            menu.add_command(label="复制 GitHub 地址", command=self.copy_repository)
            menu.add_command(label="查看发布页面", command=lambda: webbrowser.open(self.profile()["repository"] + "/releases"))
            menu.add_separator()
            menu.add_command(label="重命名软件", command=self.rename_selected)
            menu.add_command(label="清空此行下载包", command=lambda: self.clear(False))
            menu.add_command(label="移除记录", command=self.remove)
            menu.add_separator()
        if hasattr(self, "resizer"):
            self.resizer.add_context_menu(menu)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def copy_repository(self):
        profile = self.profile()
        if profile:
            self.window.clipboard_clear()
            self.window.clipboard_append(profile["repository"])

    def refresh_installed(self):
        if self.local_scanning or getattr(self.app, "closing", False):
            return
        self.local_scanning = True
        self.write("正在扫描 Windows 已安装软件记录…")
        events = self.events
        def worker():
            try:
                events.put(("installed", scan_installed()))
            except Exception as error:
                events.put(("installed_error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_installed(self):
        if getattr(self.app, "closing", False):
            return
        self.refresh_installed()
        self.window.after(30000, self.poll_installed)

    def associate_installed(self):
        profile = self.profile()
        if not profile or self.busy or self.app.manager_busy:
            return
        if not self.local_scanned:
            self.refresh_installed()
            self.status.set("正在读取系统软件列表，请稍后再选择关联。")
            return
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title("关联已安装软件 — " + profile["name"])
        dialog.transient(self.window)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="选择本机已安装的软件，关联后自动读取其版本和安装位置。名称不同也可以手动关联。").pack(anchor="w")
        search = tk.StringVar()
        entry = ttk.Entry(body, textvariable=search)
        entry.pack(fill="x", pady=8)
        area = ttk.Frame(body)
        area.pack(fill="both", expand=True)
        table = ttk.Treeview(area, columns=("name", "version", "directory"), show="headings", selectmode="browse")
        for key, label, width in (("name", "已安装软件", 240), ("version", "版本", 100), ("directory", "安装目录", 440)):
            table.heading(key, text=label, anchor="center")
            table.column(key, width=width, anchor="center", stretch=(key == "directory"))
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # Freeze this snapshot while the background scanner continues refreshing the main table.
        records = {record["id"]: record for record in self.installed_records}
        def populate(*_):
            table.delete(*table.get_children())
            query = search.get().strip().casefold()
            for record in records.values():
                if query in record["name"].casefold():
                    table.insert("", "end", iid=record["id"], values=(record["name"], record["version"] or "未知", record["directory"] or "未知"))
        def confirm(*_):
            selection = table.selection()
            if not selection:
                return
            if self.bind_installed(profile, records[selection[0]]):
                dialog.destroy()
        populate()
        search.trace_add("write", populate)
        table.bind("<Double-1>", lambda event: confirm() if table.identify_region(event.x, event.y) == "cell" else None)
        ttk.Button(body, text="关联所选软件", command=confirm, style="Primary.TButton").pack(anchor="e", pady=(8, 0))
        center_dialog(dialog, self.window, (850, 420))
        dialog.deiconify()
        dialog.lift(self.window)
        dialog.grab_set()
        entry.focus_set()

    def bind_installed(self, profile, record):
        old = profile.get("installed_id", "")
        old_auto = profile.get("installed_auto", True)
        profile["installed_id"] = record["id"]
        profile["installed_auto"] = True
        if not self.persist():
            profile["installed_id"] = old
            profile["installed_auto"] = old_auto
            return False
        self.update_row(profile)
        msg = profile["name"] + "：已关联 " + record["name"]
        self.status.set(msg)
        self.write(msg)
        return True

    def unbind_installed(self):
        profile = self.profile()
        if profile is None or self.busy or self.app.manager_busy:
            return
        previous = copy.deepcopy(profile)
        profile["installed_id"] = ""
        profile["installed_auto"] = False
        if not self.persist():
            profile.clear()
            profile.update(previous)
            return
        self.update_row(profile)
        msg = profile["name"] + "：已取消关联，需要时可重新手动关联。"
        self.status.set(msg)
        self.write(profile["name"] + "：已取消关联。")

    def open_installed_folder(self):
        profile = self.profile()
        installed = match_installed(profile, self.installed_records) if profile else None
        directory = installed.get("directory") if installed else None
        if not directory or not Path(directory).is_dir():
            self.status.set("未找到安装目录，请刷新本地版本或手动关联已安装软件。")
            return
        try:
            os.startfile(directory)
        except OSError as error:
            self.status.set("打开安装目录失败：" + str(error))

    def copy_installed_folder(self):
        profile = self.profile()
        installed = match_installed(profile, self.installed_records) if profile else None
        directory = installed.get("directory") if installed else None
        if not directory:
            self.status.set("系统记录中未提供安装目录。")
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(directory)
        self.status.set("安装目录已复制：" + directory)

    def uninstall_selected(self):
        profile = self.profile()
        if profile and self.row_pending(profile["id"]):
            return
        installed = match_installed(profile, self.installed_records) if profile else None
        if installed is None:
            self.status.set("请先关联已安装软件，再执行卸载。")
            return
        if not messagebox.askyesno("卸载软件", "打开“" + installed["name"] + "”的系统卸载程序？", parent=self.window):
            return
        try:
            launch_uninstaller(installed)
            self.status.set(profile["name"] + "：已打开卸载程序，完成后自动刷新本地版本。")
            self.window.after(3000, self.refresh_installed)
        except (OSError, ValueError) as error:
            self.status.set("卸载失败：" + str(error))

    def address_value(self, row, key):
        if key != "repository" or self.busy or self.row_pending(row) or self.app.manager_busy:
            return None
        self.table.selection_set(row)
        self.show_selection()
        return self.profile()["repository"]

    def address_commit(self, row, key, value):
        profile = self.profile()
        if self.busy or self.row_pending(row) or not profile or profile["id"] != row:
            return False
        try:
            repo = github.repository(value)
        except ValueError as error:
            self.status.set(str(error))
            return False
        if any(p is not profile and backend.repo_key(p["repository"]) == backend.repo_key(repo)
               for p in self.app.installer_profiles):
            self.status.set("该 GitHub 软件源已存在，请使用对应的软件记录。")
            return False
        previous = copy.deepcopy(profile)
        if backend.repo_key(profile["repository"]) != backend.repo_key(repo):
            for record in profile.get("history", []):
                record.setdefault("repository", profile["repository"])
            profile.update(repository=repo, release=None, release_catalog=[], selected_asset="")
            profile.pop("installed_id", None)
            profile.pop("installed_auto", None)
        else:
            profile["repository"] = repo
        if not self.persist():
            profile.clear()
            profile.update(previous)
            return False
        self.row_states.pop(row, None)
        self.update_row(profile)
        self.show_selection()
        self.status.set("GitHub 地址已保存，请检查此行获取新软件源的附件。")
        return True

    def target(self):
        return Path(self.app.installer_download_directory).expanduser().resolve()

    def persist(self):
        try:
            self.app.save()
            return True
        except OSError as error:
            self.status.set("保存失败：" + str(error))
            return False

    def profile(self):
        selection = self.table.selection()
        return next((p for p in self.app.installer_profiles if selection and p["id"] == selection[0]), None)

    def update_row(self, profile):
        release = profile.get("release") or {}
        asset = backend.selected_asset(profile)
        package = asset["name"] if asset else "—"
        installed = match_installed(profile, self.installed_records)
        local = (installed["version"] or "版本未知") if installed else ("未检测到" if profile.get("installed_id") else "未关联")
        if not self.local_scanned:
            local = "检测中…"
        values = (profile["name"], profile["repository"], release.get("tag", "—"), package, size_text(asset.get("size")) if asset else "—",
                  local)
        if self.table.exists(profile["id"]):
            self.table.item(profile["id"], values=values)
        else:
            self.table.insert("", "end", iid=profile["id"], values=values)
        catalog = backend.catalog(profile)
        latest = catalog[0] if catalog else release
        self.update_badges.set(profile["id"], bool(installed) and installed_update_available(installed["version"], latest.get("tag")))
        self.row_actions.update(profile["id"])
        if hasattr(self, "table_order"):
            self.table_order.apply()

    def show_selection(self):
        profile = self.profile()
        assets = github.candidates(profile.get("release") or {"assets": []}, "安装器") if profile else []
        self.package.configure(values=[a["name"] for a in assets],
                               state="disabled" if self.busy else "readonly")
        index = next((i for i, a in enumerate(assets) if a["name"] == profile.get("selected_asset")), -1) if profile else -1
        if index >= 0:
            self.package.current(index)
        else:
            self.package.set("")

    def choose_package(self, *_):
        profile = self.profile()
        if self.busy or not profile or self.row_pending(profile["id"]):
            return
        assets = github.candidates(profile.get("release") or {"assets": []}, "安装器")
        index = self.package.current()
        if index >= 0:
            profile["selected_asset"] = assets[index]["name"]
            self.row_states.pop(profile["id"], None)
            self.progress.configure(value=0)
            self.update_row(profile)
            self.persist()

    def inline_choices(self, row, key):
        if key not in ("version", "package") or self.row_pending(row):
            return [], ""
        self.table.selection_set(row)
        self.show_selection()
        if key == "version":
            profile = self.profile()
            return ([r["tag"] for r in backend.catalog(profile)],
                    (profile.get("release") or {}).get("tag", "")) if profile else ([], "")
        return list(self.package["values"]), self.package.get()

    def inline_commit(self, row, key, value):
        if self.busy or self.row_pending(row) or self.app.manager_busy:
            return False
        self.table.selection_set(row)
        self.show_selection()
        if key == "version":
            profile = self.profile()
            if not profile:
                return False
            release = next((r for r in backend.catalog(profile) if r["tag"] == value), None)
            if not release:
                return False
            previous = copy.deepcopy(profile)
            assets = github.candidates(release, "安装器")
            asset = next((a for a in assets if a["name"] == profile.get("selected_asset")), None)
            asset = asset or github.recommended_asset(assets, "安装器")
            profile.update(release=release, selected_asset=asset["name"] if asset else "")
            if not self.persist():
                profile.clear()
                profile.update(previous)
                return False
            self.row_states.pop(row, None)
            self.progress.configure(value=0)
            self.update_row(profile)
            self.show_selection()
            return True
        if key != "package":
            return False
        self.package.set(value)
        self.choose_package()

    def open_software_library(self):
        if self.busy or self.app.manager_busy:
            return
        existing = getattr(self, "software_library", None)
        if existing and existing.window.winfo_exists():
            existing.window.lift(self.window)
            return
        self.software_library = SoftwareLibrary(self.window,
            lambda entry: self.add(entry["repository"], entry["name"], check=False),
            lambda address: any(backend.repo_key(p["repository"]) == backend.repo_key(address) for p in self.app.installer_profiles),
            lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            proxy=self.app.proxy_url)

    def add(self, address=None, name=None, check=True):
        if self.busy or self.app.manager_busy:
            return False
        try:
            repo = github.repository(self.repository.get() if address is None else address)
        except ValueError as error:
            self.status.set(str(error))
            return False
        profile = next((p for p in self.app.installer_profiles if backend.repo_key(p["repository"]) == backend.repo_key(repo)), None)
        created = profile is None
        if profile is None:
            profile = {"id": uuid.uuid4().hex, "name": name or repo.rsplit("/", 1)[1], "repository": repo,
                       "release": None, "selected_asset": "", "history": []}
            self.app.installer_profiles.append(profile)
            self.update_row(profile)
        self.table.selection_set(profile["id"])
        self.table.see(profile["id"])
        self.repository.set("")
        if self.persist():
            if check:
                self.run("check")
            return True
        if created:
            self.app.installer_profiles.remove(profile)
            self.table.delete(profile["id"])
            self.row_actions.remove(profile["id"])
        return False

    def rename(self, event):
        if self.busy or self.table.identify_column(event.x) != "#1":
            return
        self.rename_selected()

    def rename_selected(self):
        if self.busy:
            return
        profile = self.profile()
        if profile and self.row_pending(profile["id"]):
            return
        if profile:
            name = simpledialog.askstring("软件名称", "自定义软件名称：", initialvalue=profile["name"], parent=self.window)
            if name and name.strip():
                profile["name"] = name.strip()
                self.update_row(profile)
                self.persist()

    def remove(self):
        profile = self.profile()
        if self.busy or not profile or self.row_pending(profile["id"]):
            return
        if profile.get("history"):
            self.status.set("请先清空此行下载包，再移除记录。")
            return
        self.app.installer_profiles.remove(profile)
        self.table.delete(profile["id"])
        self.row_actions.remove(profile["id"])
        self.show_selection()
        self.persist()

    def set_busy(self, value):
        self.busy = value
        self.inline.close()
        self.row_actions.set_enabled(not value)
        for widget in self.widgets:
            widget.configure(state="disabled" if value else "normal")
        self.show_selection()

    def toggle_pause(self):
        if self.control.paused.is_set():
            self.control.resume()
            self.pause.configure(text="⏸")
            self.status.set("正在继续下载…")
        else:
            self.control.pause()
            self.pause.configure(text="▶")
            self.progress.stop()
            self.status.set("已暂停下载，点击 ▶ 继续。")

    def run(self, action, all_rows=False):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            self.status.set("请等待当前操作结束。")
            return
        if action == "uninstall":
            self.uninstall_selected()
            return
        rows = self.app.installer_profiles if all_rows else [self.profile()] if self.profile() else []
        if not rows:
            self.status.set("请先添加软件或选择一行。")
            return
        if all_rows:
            action_desc = "检查" if action == "check" else "下载" if action == "download" else "安装/更新"
            self.write(f"开始批量{action_desc}共 {len(rows)} 款安装向导软件…")
        proxy, directory = self.app.proxy_url(), self.target()
        workers = getattr(self.app, "download_workers", 4)
        self.stop_after_current = False
        for snapshot in copy.deepcopy(rows):
            def task(emit, control, profile=snapshot):
                github.transfer.check_cancel(control)
                if action in ("check", "update", "download") or not profile.get("release"):
                    profile = backend.refresh(profile, proxy)
                    github.transfer.check_cancel(control)
                    emit("profile", profile)
                if action == "check":
                    return
                if action == "download" and (backend.selected_asset(profile) is None or backend.cached_download(profile, verify=True)):
                    return
                emit("transfer", None)
                report = lambda progress, text: emit("progress", (progress, profile["name"] + "：" + text))
                if workers is not None and _accepts_workers(backend.prepare):
                    profile, path = backend.prepare(profile, directory, proxy, report, control, workers=workers)
                else:
                    profile, path = backend.prepare(profile, directory, proxy, report, control)
                emit("downloaded", (profile, path, action in ("install", "update")))
            self.tasks.submit((self.key, snapshot["id"]), "check" if action == "check" else "download",
                              task, lambda job, event, value, repo=snapshot["repository"]: self.task_event(job, event, value, repo))

    def task_event(self, job, event, value, repository):
        identifier = job.key[1]
        profile = next((p for p in self.app.installer_profiles if p["id"] == identifier and p["repository"] == repository), None)
        if event == "done":
            self.row_states.pop(identifier, None)
            self.row_actions.schedule_render()
            if not self.tasks.has_page(self.key):
                self.cancel_button.pack_forget()
                self.pause.pack_forget()
                self.progress.stop()
            if job.kind == "download" and job.started:
                self.pause.pack_forget()
                self.progress.stop()
            return
        if not profile:
            return
        if event in ("queued", "started"):
            self.row_states[identifier] = "检查中" if job.kind == "check" else "下载中" if job.started else "排队中"
            self.row_actions.schedule_render()
            self.cancel_button.pack(side="left", padx=(8, 0))
            self.status.set(profile["name"] + "：" + self.row_states[identifier])
            if event == "started":
                self.write(profile["name"] + "：" + ("开始检查更新…" if job.kind == "check" else "开始下载安装包…"))
            if event == "started" and job.kind == "download":
                self.control = job.control
                self.progress.configure(value=0, mode="determinate")
        elif event == "profile":
            profile.update(release=value.get("release"), release_catalog=value.get("release_catalog", []),
                           selected_asset=value.get("selected_asset", ""))
            self.update_row(profile)
            self.persist()
            self.show_selection()
            if job.kind == "check":
                msg = profile["name"] + "：检查完成。"
                self.status.set(msg)
                self.write(msg)
        elif event == "downloaded":
            updated, path, launch = value
            self.replace_profile(updated)
            self.write(updated["name"] + "：下载完成。")
            if self.persist() and launch and not job.control.is_set() and not job.cancel_requested and not getattr(self.app, "closing", False):
                try:
                    os.startfile(path)
                    msg = updated["name"] + "：已打开安装向导。"
                    self.status.set(msg)
                    self.write(msg)
                except OSError as error:
                    msg = "打开安装器失败：" + str(error)
                    self.status.set(msg)
                    self.write(msg)
            self.show_selection()
        elif event == "transfer":
            self.pause.configure(text="⏸")
            self.pause.pack(side="left", padx=(8, 0))
        elif event == "progress":
            progress, text = value
            if not job.control.paused.is_set():
                self.status.set(text)
                if progress is None:
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(15)
                else:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=progress)
                    if progress >= 75:
                        self.pause.pack_forget()
                self.write(text)
        elif event in ("error", "cancelled"):
            msg = profile["name"] + "：" + value
            self.status.set(msg)
            self.write(msg)

    def request_stop(self):
        self.stop_after_current = True
        accepted = self.tasks.cancel_page(self.key)
        if self.busy:
            self.stop_after_current = True
            cancelled = self.control.request_cancel()
            self.status.set("正在取消任务，保留下载缓存…" if cancelled else "正在保存安装包，完成后即可退出。")
            return cancelled
        return accepted

    def clear(self, all_rows):
        if self.busy:
            return
        rows = self.app.installer_profiles if all_rows else [self.profile()] if self.profile() else []
        if any(self.row_pending(profile["id"]) for profile in rows):
            self.status.set("所选软件仍有检查或下载任务，请完成或取消后再清理。")
            return
        if not rows:
            return
        count = sum(len(p.get("history", [])) for p in rows)
        if not messagebox.askyesno("清空下载包", f"删除所选范围内的 {count} 个已记录安装包及当前附件缓存？\n已安装的软件不会被删除。", parent=self.window):
            return
        for profile in list(rows):
            updated, errors = backend.clear_history(profile)
            self.replace_profile(updated)
            self.row_states[updated["id"]] = "部分清理失败" if errors else backend.state(updated)
            self.update_row(updated)
            if errors:
                self.status.set("清理失败：" + "；".join(errors))
        self.persist()
        if not self.pending_downloads:
            self.progress.configure(value=0)
            self.pause.pack_forget()

    def replace_profile(self, profile):
        for index, existing in enumerate(self.app.installer_profiles):
            if existing["id"] == profile["id"]:
                profile["installed_id"] = existing.get("installed_id", "")
                profile["installed_auto"] = existing.get("installed_auto", True)
                self.app.installer_profiles[index] = profile
                self.row_states.pop(profile["id"], None)
                self.update_row(profile)
                return

    def open_folder(self):
        directory = self.target()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(directory)
        except OSError as error:
            self.status.set("打开目录失败：" + str(error))

    def poll(self):
        self.tasks.drain()
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "installed":
                    self.local_scanning = False
                    self.local_scanned = True
                    self.installed_records = value
                    changed = False
                    for profile in self.app.installer_profiles:
                        installed = match_installed(profile, value)
                        if installed and not profile.get("installed_id"):
                            profile["installed_id"] = installed["id"]
                            changed = True
                        self.update_row(profile)
                    if changed:
                        self.persist()
                    self.write(f"Windows 已安装软件扫描完成，已加载 {len(value)} 条记录。")
                elif kind == "installed_error":
                    self.local_scanning = False
                    msg = "读取已安装软件失败：" + value
                    self.status.set(msg)
                    self.write(msg)
                elif kind == "state":
                    identifier, state = value
                    self.row_states[identifier] = state
                    profile = next(p for p in self.app.installer_profiles if p["id"] == identifier)
                    self.update_row(profile)
                elif kind == "profile":
                    self.replace_profile(value)
                    self.persist()
                    self.show_selection()
                elif kind == "transfer":
                    self.pause.configure(text="⏸")
                    self.pause.pack(side="left", padx=(8, 0))
                elif kind == "progress":
                    progress, text = value
                    if self.control.paused.is_set():
                        continue
                    self.status.set(text)
                    if progress is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(15)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=progress)
                        if progress >= 75:
                            self.pause.pack_forget()
                elif kind == "downloaded":
                    profile, path, launch = value
                    self.replace_profile(profile)
                    self.write(profile["name"] + "：下载完成。")
                    if self.persist() and launch and not self.control.is_set() and not self.stop_after_current and not getattr(self.app, "closing", False):
                        try:
                            os.startfile(path)
                            msg = profile["name"] + "：已打开安装向导。"
                            self.status.set(msg)
                            self.write(msg)
                        except OSError as error:
                            msg = "打开安装器失败：" + str(error)
                            self.status.set(msg)
                            self.write(msg)
                    self.show_selection()
                elif kind == "error":
                    identifier, text = value
                    self.row_states[identifier] = "失败"
                    profile = next(p for p in self.app.installer_profiles if p["id"] == identifier)
                    self.update_row(profile)
                    msg = profile["name"] + "：" + text
                    self.status.set(msg)
                    self.write(msg)
                elif kind == "done":
                    self.cancel_button.pack_forget()
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.pause.pack_forget()
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.window.after(100, self.poll)

    def write(self, text):
        if not hasattr(self, "log") or not self.log.winfo_exists():
            return
        if not text:
            return
        lines = str(text).splitlines()
        stamp = time.strftime("%H:%M:%S ")
        self.log.configure(state="normal")
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            if len(line_str) >= 9 and line_str[2] == ":" and line_str[5] == ":" and line_str[8] == " ":
                self.log.insert("end", line_str + "\n")
            else:
                self.log.insert("end", stamp + line_str + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
