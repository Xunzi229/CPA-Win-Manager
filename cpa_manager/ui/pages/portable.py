"""Portable software catalog and installation page."""
import os
import copy
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import urllib.error
import webbrowser

from cpa_manager.backends import portable as backend
from cpa_manager.backends import github
from cpa_manager.core.download import DownloadCancelled, DownloadControl, size_text
from cpa_manager.core.runtime import default_download_directory
from cpa_manager.ui.widgets.table_choices import TableChoices
from cpa_manager.ui.widgets.table_order import TableOrder
from cpa_manager.ui.theme import style_log_widget
from cpa_manager.ui.widgets.frozen_actions import FrozenActions
from cpa_manager.core.models import ChoiceState, ensure_ids
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.backups import backup_directories, clear_backups
from cpa_manager.core.software_tasks import shared_tasks
from cpa_manager.ui.widgets.help_hint import add_help
from cpa_manager.ui.widgets.software_library import SoftwareLibrary


class PortablePage:
    key = "custom"

    @property
    def checking(self):
        return self.tasks.has_page(self.key, "check")

    @property
    def pending_downloads(self):
        return self.tasks.has_page(self.key, "download")

    def row_pending(self, row):
        return (self.key, row) in self.tasks.jobs

    def action_label(self, row, action, text):
        job = self.tasks.jobs.get((self.key, row))
        if job and action == ("check" if job.kind == "check" else "install"):
            return "检查中" if job.kind == "check" else "下载中" if job.started else "排队中"
        return text

    def __init__(self, app, notebook, root):
        self.app, self.window, self.root = app, app.window, root
        self.busy = False
        self.tasks = shared_tasks(app)
        self.save_timer = None
        self.loading_profile = True
        self.release = None
        self.assets = []
        self.catalog = []
        self.row_catalogs = {}
        self.local_versions = {}
        ensure_ids(app.custom_profiles)
        self.cache = getattr(app, "release_cache", None) or ReleaseCache()
        app.release_cache = self.cache
        for profile in app.custom_profiles:
            for key in ("directory", "repository", "preserve"):
                profile.setdefault(key, "")
            cached = profile.get("release_catalog")
            if self.valid_catalog(cached, profile.get("repository", "")):
                self.cache.put(profile["repository"], cached)
            profile.pop("release_catalog", None)
            cached = self.cache.get(profile.get("repository", ""))
            if cached:
                self.row_catalogs[profile["id"]] = cached
                profile["latest_version"] = cached[0]["tag"]
        self.saved_profiles = copy.deepcopy(app.custom_profiles)
        self.events = queue.Queue()
        self.cancel_event = DownloadControl()
        self.generation = 0
        self.downloading = False
        self.frame = ttk.Frame(notebook, padding=16)
        self.names = tk.StringVar()
        self.variables = {key: tk.StringVar() for key in
                          ("repository", "directory", "preserve")}
        if not hasattr(app, "installer_download_directory"):
            app.installer_download_directory = str(default_download_directory())
        self.profile = None
        self.widgets = []
        self.help_hints = []
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="新增免安装软件：").pack(side="left")
        self.selector = ChoiceState(self.names)
        button = ttk.Button(row, text="解压安装根目录", command=self.add)
        button.pack(side="left", padx=(8, 0))
        self.widgets.append((button, "normal"))
        self.help_hints.append(add_help(button, "选择免安装软件的安装目录，ZIP 解压或单文件 EXE 保存到此目录。双击表格中的安装目录可修改，右键也可选择目录。\nEXE / MSI 安装器请使用安装向导软件页，下载目录在设置中统一配置。"))
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 10))
        self.check_all_button = ttk.Button(row, text="全部检查", command=lambda: self.check(all_rows=True))
        self.check_all_button.pack(side="left")
        self.widgets.append((self.check_all_button, "normal"))
        self.help_hints.append(add_help(self.check_all_button, "手动检查所有软件的最新版本和附件。选中软件只使用缓存，不会自动联网检查。检查时其他软件行仍可操作。"))
        button = ttk.Button(row, text="移除记录", command=self.remove)
        button.pack(side="left", padx=(8, 0))
        self.widgets.append((button, "normal"))
        self.help_hints.append(add_help(button, "仅移除软件配置记录，保留已安装文件。"))
        library_button = ttk.Button(row, text="软件库", command=self.open_software_library)
        library_button.pack(side="right")
        self.widgets.append((library_button, "normal"))
        self.help_hints.append(add_help(library_button, "从软件库或 GitHub 搜索选择项目，选择安装目录后添加到免安装软件列表。"))
        area = ttk.Frame(self.frame)
        area.pack(fill="both", expand=True, pady=(0, 8))
        columns = ("directory", "repository", "local", "version", "package", "size")
        self.table = ttk.Treeview(area, columns=columns, show="headings", selectmode="browse", height=7)
        for key, label, width in zip(columns,
                ("软件安装目录", "GitHub 地址", "本地版本", "选择安装版本 ▾", "对应包 ▾", "包大小"),
                (220, 200, 90, 110, 220, 90)):
            self.table.heading(key, text=label, anchor="center")
            self.table.column(key, width=width, minwidth=70, stretch=False, anchor="center")
        self.row_actions = FrozenActions(area, self.table,
            (("check", "检查", 52), ("install", "安装 / 更新", 88), ("open", "打开目录", 76), ("clear", "清空安装备份", 104)),
            self.run_row_action, lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            lambda: self.inline.close() if hasattr(self, "inline") else None,
            enabled=lambda row, action: action == "open" or not self.row_pending(row), label=self.action_label,
            help_text={"check": "手动获取该软件的最新版本和附件。选中软件行不会自动检查。",
                       "install": "安装所选版本和附件到此行的软件目录。仅替换包内同名文件，包外文件不删除；额外保留的文件和目录不会被覆盖。任务按下载队列顺序执行。",
                       "open": "打开此行软件的安装目录。",
                       "clear": "清空此软件目录中的安装备份，不删除已安装软件。同一目录中的软件共用备份，清空后无法恢复旧文件。"})
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.row_actions.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=lambda *args: (self.inline.close(), self.table.xview(*args)))
        self.row_actions.scrollbar = vertical
        self.table.configure(xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        self.row_actions.tree.grid(row=0, column=1, sticky="ns")
        vertical.grid(row=0, column=2, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.table.bind("<<TreeviewSelect>>", self.table_selected, add="+")
        self.table.bind("<Button-3>", self.context_menu)
        self.inline = TableChoices(self.table, self.inline_choices, self.inline_commit,
                                   lambda: not self.busy or self.downloading and self.cancel_event.phase != "committing",
                                   self.address_value, self.address_commit)
        self.table_order = TableOrder(self.table, self.row_actions, lambda: self.app.custom_profiles,
                                      self.persist, lambda: not self.busy and not self.app.manager_busy,
                                      self.inline.close)
        labels = {"repository": "GitHub 地址：",
                  "preserve": "额外保留文件 / 目录："}
        for key, label in labels.items():
            row = ttk.Frame(self.frame)
            row.pack(fill="x", pady=4)
            if key == "preserve":
                self.preserve_row = row
            ttk.Label(row, text=label, width=19).pack(side="left")
            widget = ttk.Entry(row, textvariable=self.variables[key])
            if key == "preserve":
                self.preserve_entry = widget
            widget.pack(side="left", expand=True, fill="x")
            self.widgets.append((widget, "readonly" if key == "repository" else "normal"))
            text_hint = ("双击输入框或表格中的 GitHub 地址后修改，回车保存；表格编辑可按 Esc 取消。软件源地址必须唯一。配置修改后自动保存，无需手动保存。"
                         if key == "repository" else "默认留空。填写安装包内需要避免覆盖的配置或数据的相对路径，用分号分隔，例如 config.yaml;data。配置修改后自动保存，无需手动保存。")
            self.help_hints.append(add_help(widget, text_hint))
            if key == "repository":
                self.repository_entry = widget
                widget.configure(state="readonly")
                widget.bind("<Double-1>", self.edit_repository)
                widget.bind("<Return>", self.finish_repository_edit)
                widget.bind("<FocusOut>", self.finish_repository_edit)
        self.variables["repository"].trace_add("write", lambda *_: self.invalidate())
        for variable in self.variables.values():
            variable.trace_add("write", self.schedule_save)
        self.asset = tk.StringVar()
        self.version = tk.StringVar()
        self.version_selector = ChoiceState(self.version)
        package_row = ttk.Frame(self.frame)
        package_row.pack(fill="x", pady=4)
        ttk.Label(package_row, text="对应包：", width=19).pack(side="left")
        self.asset_selector = ttk.Combobox(package_row, textvariable=self.asset, state="readonly")
        self.asset_selector.pack(side="left", expand=True, fill="x")
        self.asset_selector.bind("<<ComboboxSelected>>", self.asset_changed)
        self.widgets.append((self.asset_selector, "readonly"))
        button = ttk.Button(package_row, text="检查 / 获取附件", command=self.check, style="Primary.TButton")
        self.check_button = button
        button.pack(side="left", padx=(8, 0))
        self.widgets.append((button, "normal"))
        self.help_hints.append(add_help(button, "手动检查当前软件的版本和附件，替换已有缓存。点击表格中的安装版本或对应包可以选择，也可以在此下拉框选择附件。配置会自动保存。"))
        self.save_status = tk.StringVar(value="配置修改后自动保存")
        self.save_notice = ttk.Label(self.frame, textvariable=self.save_status, foreground="#b45309", wraplength=800)
        self.save_status.trace_add("write", self.show_save_notice)
        self.status = tk.StringVar(value="添加软件，填写 GitHub 地址后获取发布附件。")
        self.status_label = ttk.Label(self.frame, textvariable=self.status, wraplength=800)
        self.status_label.pack(anchor="w", pady=8)
        progress_row = ttk.Frame(self.frame)
        progress_row.pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(progress_row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.pause_button = ttk.Button(progress_row, text="⏸", width=3, command=self.toggle_pause)
        self.cancel_button = ttk.Button(progress_row, text="取消任务", command=self.request_stop)
        self.log = scrolledtext.ScrolledText(self.frame, state="disabled", height=4)
        style_log_widget(self.log)
        self.log.pack(fill="x")
        self.refresh_names()
        self.loading_profile = False
        self.window.after(100, self.poll)

    valid_catalog = staticmethod(github.valid_catalog)

    def show_save_notice(self, *_):
        if self.save_status.get().startswith(("未保存", "自动保存失败")):
            self.save_notice.pack(anchor="w", pady=(4, 0), before=self.status_label)
        else:
            self.save_notice.pack_forget()

    @staticmethod
    def profile_label(profile):
        return profile.get("directory") or profile["name"]

    def update_labels(self, only=None):
        self.selector.configure(values=[self.profile_label(p) for p in self.app.custom_profiles])
        if self.profile:
            self.names.set(self.profile_label(self.profile))
        if hasattr(self, "table"):
            desired = {p["id"] for p in self.app.custom_profiles}
            for iid in self.table.get_children():
                if iid not in desired:
                    self.table.delete(iid)
                    self.row_actions.remove(iid)
            for profile in ([only] if only else self.app.custom_profiles):
                directory, repo = profile.get("directory"), profile.get("repository")
                try:
                    stat = (Path(directory) / backend.METADATA).stat() if directory else None
                    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino) if stat else None
                except OSError:
                    signature = None
                key = (directory, repo, signature)
                cached = self.local_versions.get(profile["id"])
                if cached is None or cached[0] != key:
                    local = backend.local_version(directory, repo) if directory and repo else None
                    self.local_versions[profile["id"]] = (key, local)
                else:
                    local = cached[1]
                values = (profile.get("directory", "—"), profile.get("repository", ""), local or "尚无记录",
                          profile.get("selected_version", "—"),
                          profile.get("selected_asset") or "—", self.package_size(profile))
                iid = profile["id"]
                if self.table.exists(iid):
                    self.table.item(iid, values=values)
                else:
                    self.table.insert("", "end", iid=iid, values=values)
                self.row_actions.update(iid)
            if hasattr(self, "table_order"):
                self.table_order.apply()

    def package_size(self, profile):
        catalog = self.row_catalogs.get(profile["id"], [])
        release = next((r for r in catalog if r["tag"] == profile.get("selected_version")), None)
        asset = next((a for a in release["assets"] if a["name"] == profile.get("selected_asset")), None) if release else None
        return size_text(asset.get("size")) if asset else "—"

    def inline_choices(self, row, key):
        if key not in ("version", "package") or self.row_pending(row):
            return [], ""
        self.table.selection_set(row)
        self.table_selected()
        if self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return [], ""
        if key == "version":
            return [r["tag"] for r in self.catalog], self.version.get()
        return list(self.asset_selector["values"]), self.asset.get()

    def address_value(self, row, key):
        if key not in ("repository", "directory") or self.busy or self.row_pending(row) or self.app.manager_busy:
            return None
        self.table.selection_set(row)
        self.table_selected()
        if self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return None
        return self.variables[key].get()

    def edit_repository(self, *_):
        if not self.profile or self.busy or self.row_pending(self.profile["id"]) or self.app.manager_busy:
            return "break"
        self.repository_entry.configure(state="normal")
        self.repository_entry.focus_set()
        return "break"

    def finish_repository_edit(self, *_):
        if str(self.repository_entry.cget("state")) == "normal" and self.persist(quiet=True):
            self.repository_entry.configure(state="readonly")
        return "break"

    def address_commit(self, row, key, value):
        if key not in ("repository", "directory") or self.busy or self.row_pending(row) or self.app.manager_busy or self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return False
        if key == "directory":
            return self.change_directory(value)
        previous = copy.deepcopy(self.profile)
        cached = self.row_catalogs.get(self.profile["id"])
        self.variables["repository"].set(value)
        if self.persist():
            self.status.set("GitHub 地址已保存，请手动获取该软件的版本和附件。")
            return True
        self.profile.clear()
        self.profile.update(previous)
        if cached:
            self.row_catalogs[self.profile["id"]] = cached
        self.variables["repository"].set(previous.get("repository", ""))
        self.update_labels()
        return False

    def change_directory(self, value):
        if not self.profile or self.busy or self.row_pending(self.profile["id"]) or self.app.manager_busy:
            return False
        try:
            if not value.strip():
                raise ValueError("安装目录不能为空。")
            directory = Path(value.strip()).expanduser().resolve()
            if directory.exists() and not directory.is_dir():
                raise ValueError("安装目录不能是文件，请选择文件夹。")
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(str(error))
            return False
        previous = copy.deepcopy(self.profile)
        previous_directory = self.variables["directory"].get()
        self.variables["directory"].set(str(directory))
        if not self.persist():
            self.profile.clear()
            self.profile.update(previous)
            self.variables["directory"].set(previous_directory)
            self.update_labels(self.profile)
            return False
        self.clear_download_state()
        self.status.set("安装目录已保存，后续安装使用新目录；原目录文件未移动。")
        return True

    def choose_directory(self):
        if not self.profile or self.busy or self.app.manager_busy:
            return
        initial = self.target() if self.target().is_dir() else self.root
        directory = filedialog.askdirectory(parent=self.window, initialdir=str(initial), title="更改软件安装目录")
        if directory:
            self.change_directory(directory)

    def inline_commit(self, row, key, value):
        if self.row_pending(row) or self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return
        if self.busy and not self.cancel_event.request_cancel():
            return
        if key == "version":
            self.version.set(value)
            self.version_changed()
        else:
            self.asset.set(value)
            self.asset_changed()

    def table_selected(self, *_):
        selection = self.table.selection()
        if not selection:
            return
        index = next((i for i, p in enumerate(self.app.custom_profiles) if p["id"] == selection[0]), -1)
        if index < 0:
            return
        if self.profile is self.app.custom_profiles[index]:
            return
        if self.busy and (not self.downloading or self.cancel_event.phase == "committing"):
            if self.profile in self.app.custom_profiles:
                self.table.selection_set(self.profile["id"])
            return
        self.selector.current(index)
        self.select(index=index)

    def run_row_action(self, row, action):
        if self.busy or (action != "open" and self.row_pending(row)) or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        if not self.table.exists(row):
            return
        self.table.selection_set(row)
        self.table_selected()
        if not self.profile or self.profile["id"] != row:
            return
        commands = {"check": self.check, "install": self.install, "open": self.open_folder, "clear": self.clear_backups}
        if action in commands:
            commands[action]()

    def context_menu(self, event):
        row = self.table.identify_row(event.y)
        self.inline.close()
        if not row or self.busy or self.app.manager_busy:
            return
        self.table.selection_set(row)
        self.table_selected()
        menu = tk.Menu(self.table, tearoff=False)
        self.table_order.add_pin_menu(menu, row)
        menu.add_command(label="检查更新 / 获取版本", command=self.check)
        menu.add_command(label="安装所选版本", command=self.install,
                         state="normal" if self.release and self.asset.get() else "disabled")
        menu.add_command(label="选择最新版本", command=self.choose_latest,
                         state="normal" if self.catalog else "disabled")
        menu.add_separator()
        menu.add_command(label="更改安装目录", command=self.choose_directory)
        menu.add_command(label="打开安装目录", command=self.open_folder)
        menu.add_command(label="清空安装备份", command=self.clear_backups)
        menu.add_command(label="复制安装目录", command=lambda: self.copy_text(str(self.target())))
        menu.add_command(label="复制 GitHub 地址", command=lambda: self.copy_text(self.variables["repository"].get()))
        menu.add_command(label="查看发布页面", command=self.open_releases,
                         state="normal" if self.variables["repository"].get().strip() else "disabled")
        menu.add_separator()
        menu.add_command(label="移除记录", command=self.remove)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def choose_latest(self):
        if self.catalog:
            self.version.set(self.catalog[0]["tag"])
            self.version_changed()

    def copy_text(self, text):
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def open_releases(self):
        try:
            webbrowser.open(github.repository(self.variables["repository"].get()) + "/releases")
        except ValueError as error:
            self.status.set(str(error))

    def refresh_names(self):
        self.update_labels()
        if self.app.custom_profiles:
            self.selector.current(0)
            self.select()
        else:
            self.profile = None
            self.names.set("")
            for key, variable in self.variables.items():
                variable.set("")
            self.invalidate()

    def select(self, index=None):
        index = self.selector.current() if index is None else index
        if self.busy and not self.cancel_event.request_cancel():
            if self.profile:
                self.table.selection_set(self.profile["id"])
            return
        if self.profile and not self.persist():
            self.names.set(self.profile_label(self.profile))
            previous = next((i for i, p in enumerate(self.app.custom_profiles) if p is self.profile), None)
            if previous is not None:
                self.table.selection_set(self.profile["id"])
            return
        self.profile = self.app.custom_profiles[index] if 0 <= index < len(self.app.custom_profiles) else None
        self.repository_entry.configure(state="disabled" if self.busy else "readonly")
        if self.profile:
            self.names.set(self.profile_label(self.profile))
            self.loading_profile = True
            try:
                for key, variable in self.variables.items():
                    variable.set(self.profile.get(key, ""))
            finally:
                self.loading_profile = False
            self.table.selection_set(self.profile["id"])
            self.table.see(self.profile["id"])
        self.invalidate()
        if self.profile:
            self.catalog = self.row_catalogs.get(self.profile["id"]) or self.cache.get(self.profile.get("repository", ""))
            if self.catalog:
                self.version_selector.configure(values=[r["tag"] for r in self.catalog])
                selected = next((r for r in self.catalog if r["tag"] == self.profile.get("selected_version")), self.catalog[0])
                self.display_release(selected)
                self.status.set("")
        self.set_widget_states()

    def choose_portable(self, directory):
        directory = str(Path(directory).expanduser().resolve())
        self.add_profile({"name": Path(directory).name, "mode": "便携安装",
                          "repository": "", "directory": directory, "preserve": ""})

    def contains_repository(self, address):
        source = github.repository(address).casefold()
        for profile in self.app.custom_profiles:
            try:
                if github.repository(profile.get("repository", "")).casefold() == source:
                    return True
            except ValueError:
                continue
        return False

    def open_software_library(self):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        existing = getattr(self, "software_library", None)
        if existing and existing.window.winfo_exists():
            existing.window.lift(self.window)
            return
        self.software_library = SoftwareLibrary(self.window, self.add_library_entry, self.contains_repository,
            lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            proxy=self.app.proxy_url, portable=True)

    def add_library_entry(self, entry):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            return False
        if self.contains_repository(entry["repository"]) or not self.persist(quiet=True):
            return False
        directory = filedialog.askdirectory(parent=self.software_library.window, initialdir=str(self.root),
                                            title="选择该软件的安装目录")
        if not directory:
            return False
        previous = self.profile
        profile = {"name": entry["name"], "mode": "便携安装", "repository": github.repository(entry["repository"]),
                   "directory": str(Path(directory).expanduser().resolve()), "preserve": ""}
        self.app.custom_profiles.append(profile)
        ensure_ids(self.app.custom_profiles)
        self.profile = None
        self.update_labels()
        self.select(index=len(self.app.custom_profiles) - 1)
        if self.persist(quiet=True):
            return True
        self.app.custom_profiles.remove(profile)
        self.row_catalogs.pop(profile["id"], None)
        self.local_versions.pop(profile["id"], None)
        self.profile = None
        self.update_labels()
        if previous in self.app.custom_profiles:
            self.select(index=self.app.custom_profiles.index(previous))
        else:
            self.refresh_names()
        self.save_status.set("自动保存失败：软件库添加未保存，请重试。")
        return False

    def add_profile(self, profile):
        self.app.custom_profiles.append(profile)
        ensure_ids(self.app.custom_profiles)
        self.profile = None
        self.update_labels()
        self.selector.current(len(self.app.custom_profiles) - 1)
        self.select(index=len(self.app.custom_profiles) - 1)
        self.persist()

    def add(self):
        if not self.persist():
            return
        directory = filedialog.askdirectory(parent=self.window, title="选择解压安装根目录")
        if directory:
            self.choose_portable(directory)

    def remove(self):
        if not self.profile or self.row_pending(self.profile["id"]):
            return
        if not messagebox.askyesno("移除记录", "仅移除软件配置，保留已安装文件。", parent=self.window):
            return
        index = next(i for i, p in enumerate(self.app.custom_profiles) if p is self.profile)
        del self.app.custom_profiles[index]
        self.profile = None
        self.refresh_names()
        self.persist()

    def schedule_save(self, *_):
        if self.loading_profile or not self.profile:
            return
        self.cancel_save()
        self.save_status.set("正在等待自动保存…")
        self.save_timer = self.window.after(600, self.auto_save)

    def cancel_save(self):
        if self.save_timer is not None:
            self.window.after_cancel(self.save_timer)
            self.save_timer = None

    def auto_save(self):
        self.save_timer = None
        if self.busy or self.app.manager_busy:
            self.save_timer = self.window.after(600, self.auto_save)
            return
        self.persist(quiet=True)

    def persist(self, quiet=False):
        self.cancel_save()
        if self.profile:
            repo = self.variables["repository"].get().strip()
            if repo:
                try:
                    key = github.repository(repo).lower()
                except ValueError as error:
                    self.status.set(str(error))
                    self.save_status.set("未保存：请填写有效的 GitHub 地址")
                    return False
                for other in self.app.custom_profiles:
                    if other is self.profile:
                        continue
                    try:
                        duplicate = github.repository(other.get("repository", "")).lower() == key
                    except ValueError:
                        continue
                    if duplicate:
                        self.status.set("该 GitHub 软件源已存在，请使用对应的软件记录；安装根目录可以重复。")
                        self.save_status.set("未保存：GitHub 软件源重复")
                        return False
            self.profile.pop("pattern", None)
            if self.profile.get("repository", "") != self.variables["repository"].get().strip():
                self.row_catalogs.pop(self.profile["id"], None)
                for key in ("latest_version", "selected_version", "selected_asset", "release_catalog"):
                    self.profile.pop(key, None)
            self.profile.update({k: v.get().strip() for k, v in self.variables.items()})
        try:
            if self.app.custom_profiles != self.saved_profiles or self.cache.dirty:
                self.cache.flush()
                self.app.save()
                self.saved_profiles = copy.deepcopy(self.app.custom_profiles)
            self.update_labels(self.profile)
            self.save_status.set("配置已自动保存")
            return True
        except OSError as error:
            self.save_status.set("自动保存失败：" + str(error))
            if not quiet:
                messagebox.showerror("保存失败", str(error), parent=self.window)
            return False

    def target(self):
        return Path(self.variables["directory"].get() or self.root / "CustomApps").expanduser().resolve()

    def open_folder(self):
        if self.target().is_dir():
            os.startfile(self.target())
        else:
            self.status.set("目录尚未创建，请先安装或选择已有目录。")

    def clear_backups(self):
        if not self.profile or self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        if self.pending_downloads:
            self.status.set("请等待下载队列完成或取消任务后，再清理安装备份。")
            return
        target = self.target()
        try:
            count = len(backup_directories(target, "portable"))
        except OSError as error:
            self.status.set("读取安装备份失败：" + str(error))
            return
        if not count:
            self.status.set("没有可清理的安装备份。")
            return
        if messagebox.askyesno("清空安装备份", f"删除以下安装目录中的 {count} 个安装备份？\n{target}\n\n同目录的软件共用这些备份。清空后无法使用它们恢复旧文件。", parent=self.window):
            self.start(lambda emit, control: clear_backups(target, "portable",
                       lambda progress, text: emit("progress", (progress, text)), control))

    def invalidate(self):
        self.clear_download_state()
        self.release, self.assets = None, []
        self.catalog = []
        if hasattr(self, "version_selector"):
            self.version_selector.configure(values=[])
            self.version.set("")
        if hasattr(self, "asset_selector"):
            self.asset_selector.configure(values=[])
            self.asset.set("")
            self.status.set("")

    def clear_download_state(self):
        if self.pending_downloads:
            return True
        if self.busy and not self.cancel_event.request_cancel():
            return False
        self.generation += 1
        self.cancel_event.set()
        self.cancel_event.resume()
        self.downloading = False
        if hasattr(self, "progress"):
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.pause_button.pack_forget()
            self.pause_button.configure(text="⏸")
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
        return True

    def asset_changed(self, *_):
        if self.profile and self.row_pending(self.profile["id"]):
            self.asset.set(self.profile.get("selected_asset", ""))
            return
        if not self.clear_download_state():
            self.asset.set(self.profile.get("selected_asset", "") if self.profile else "")
            return
        index = self.asset_selector.current()
        if self.profile and 0 <= index < len(self.assets):
            self.profile["selected_asset"] = self.assets[index]["name"]
            self.persist()
        self.status.set("已切换附件，可安装所选包。")

    def version_changed(self, *_):
        if self.profile and self.row_pending(self.profile["id"]):
            self.version.set(self.profile.get("selected_version", ""))
            return
        if not self.clear_download_state():
            return
        release = next((r for r in self.catalog if r["tag"] == self.version.get()), None)
        if release:
            self.display_release(release)
            self.persist()

    def display_release(self, release):
        self.release = release
        self.version.set(release["tag"])
        self.assets = github.candidates(release, "便携安装")
        names = [a["name"] for a in self.assets]
        self.asset_selector.configure(values=names)
        selected = next((a for a in self.assets if self.profile and a["name"] == self.profile.get("selected_asset")), None)
        selected = selected or github.recommended_asset(self.assets, "便携安装")
        self.asset.set(names[self.assets.index(selected)] if selected else "")
        if self.profile:
            self.profile.update(selected_version=release["tag"], selected_asset=selected["name"] if selected else "")
            self.update_labels(self.profile)
        self.status.set("已选择版本 " + release["tag"] + "，请选择对应附件安装。" if names else "此版本没有支持的 ZIP / EXE 附件。")
        self.write(release["notes"])

    def toggle_pause(self):
        if not self.downloading:
            return
        if self.cancel_event.paused.is_set():
            self.cancel_event.resume()
            self.pause_button.configure(text="⏸")
            self.status.set("正在继续下载…")
        else:
            self.cancel_event.pause()
            self.progress.stop()
            self.pause_button.configure(text="▶")
            self.status.set("已暂停下载，点击 ▶ 继续。")

    def request_stop(self):
        accepted = self.tasks.cancel_page(self.key)
        if self.busy:
            cancelled = self.cancel_event.request_cancel()
            self.status.set("正在取消任务，保留下载缓存…" if cancelled else "正在写入安装文件，完成后即可退出。")
            return cancelled
        return accepted

    def set_widget_states(self, downloading=False):
        self.row_actions.set_enabled(not self.busy)
        if self.busy:
            self.inline.close()
        for widget, state in self.widgets:
            allow_switch = downloading and widget in (self.selector, self.asset_selector, self.version_selector)
            locked = self.profile and self.row_pending(self.profile["id"]) and widget in (self.repository_entry, self.preserve_entry, self.asset_selector, self.check_button)
            widget.configure(state=state if (not self.busy or allow_switch) and not locked else "disabled")

    def start(self, task, downloading=False):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            self.status.set("请等待当前操作完成。")
            return
        self.busy = True
        self.cancel_event = DownloadControl()
        self.downloading = downloading
        self.cancel_button.pack(side="left", padx=(8, 0))
        token = self.generation
        control = self.cancel_event
        if downloading:
            self.pause_button.configure(text="⏸")
            self.pause_button.pack(side="left", padx=(8, 0))
        self.set_widget_states(downloading)
        events = self.events
        emit = lambda kind, value: events.put((kind, value, token))
        def worker():
            try:
                task(emit, control)
            except DownloadCancelled as error:
                emit("cancelled", str(error))
            except Exception as error:
                text = "GitHub 请求失败（可能没有正式 Release、网络不可用或 API 限流）：" + str(error) if isinstance(error, urllib.error.HTTPError) else str(error)
                emit("error", text)
            finally:
                control.finish()
                emit("done", None)
        threading.Thread(target=worker, daemon=True).start()

    def check(self, all_rows=False):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            self.status.set("请等待当前操作完成。")
            return
        if not self.app.custom_profiles or not all_rows and not self.profile:
            self.status.set("请先添加软件。")
            return
        if not self.persist():
            return
        proxy = self.app.proxy_url()
        rows = self.app.custom_profiles if all_rows else [self.profile]
        batch = {"pending": 0, "success": 0, "failed": 0, "skipped": 0, "cancelled": 0}
        for profile in rows:
            try:
                github.repository(profile.get("repository", ""))
            except ValueError:
                batch["skipped"] += 1
                self.status.set("请先填写有效的 GitHub 地址。")
                continue
            if self.row_pending(profile["id"]):
                batch["skipped"] += 1
                continue
            repo = profile["repository"]
            def task(emit, control, repo=repo):
                catalog = github.release_catalog(repo, proxy)
                if control.is_set():
                    raise DownloadCancelled("版本检查已取消。")
                emit("catalog", catalog)
            def receive(job, event, value, repo=repo):
                self.task_event(job, event, value, repo)
                if event == "catalog":
                    batch["success"] += 1
                elif event == "error":
                    batch["failed"] += 1
                elif event == "cancelled":
                    batch["cancelled"] += 1
                elif event == "done":
                    batch["pending"] -= 1
                    if all_rows and not batch["pending"]:
                        text = f"全部检查完成：成功 {batch['success']}，失败 {batch['failed']}，跳过 {batch['skipped']}。"
                        if batch["cancelled"]:
                            text += f"已取消 {batch['cancelled']} 项。"
                        self.status.set(text)
                        self.write(text)
            batch["pending"] += 1
            self.tasks.submit((self.key, profile["id"]), "check", task, receive)

    def apply_catalog(self, profile, catalog):
        self.row_catalogs[profile["id"]] = catalog
        self.cache.put(profile["repository"], catalog)
        self.local_versions.pop(profile["id"], None)
        profile["latest_version"] = catalog[0]["tag"]
        selected = catalog[0]
        if profile is self.profile:
            self.catalog = catalog
            self.version_selector.configure(values=[release["tag"] for release in catalog])
            self.display_release(selected)
        else:
            assets = github.candidates(selected, "便携安装")
            asset = next((asset for asset in assets if asset["name"] == profile.get("selected_asset")), None)
            asset = asset or github.recommended_asset(assets, "便携安装")
            profile.update(selected_version=selected["tag"], selected_asset=asset["name"] if asset else "")
            self.update_labels(profile)
        self.persist()

    def install(self):
        if self.busy:
            self.status.set("请等待当前下载或安装操作结束。")
            return
        if not self.release or not self.asset.get():
            self.status.set("请先获取附件并选择安装包。")
            return
        if not self.variables["directory"].get().strip():
            self.status.set("请选择独立的软件安装目录。")
            return
        index = self.asset_selector.current()
        asset = self.assets[index] if 0 <= index < len(self.assets) else None
        if not asset:
            return
        text = "仅覆盖安装包内同名文件，包外文件不删除。请先关闭目标软件；额外保留路径不会覆盖。"
        if not messagebox.askyesno("确认安装", f"{self.profile['name']} · {self.release['tag']}\n附件：{asset['name']}\n大小：{size_text(asset.get('size'))}\n目录：{self.target()}\n\n{text}", parent=self.window):
            return
        if not self.persist():
            return
        if self.row_pending(self.profile["id"]) or self.app.manager_busy or getattr(self.app, "closing", False):
            self.status.set("该软件已有检查或下载任务。")
            return
        release, target, proxy = self.release, self.target(), self.app.proxy_url()
        preserve = self.variables["preserve"].get()
        profile = self.profile
        def task(emit, control):
            report = lambda progress, text: emit("progress", (progress, text))
            backend.install(release, asset, target, preserve, proxy, report, control)
            emit("installed", None)
        self.tasks.submit((self.key, profile["id"]), "download", task,
                          lambda job, event, value: self.task_event(job, event, value, profile["repository"]))

    def task_event(self, job, event, value, repository):
        identity = job.key[1]
        profile = next((p for p in self.app.custom_profiles if p["id"] == identity and p["repository"] == repository), None)
        if event == "done":
            self.row_actions.schedule_render()
            self.set_widget_states()
            if not self.tasks.has_page(self.key):
                self.cancel_button.pack_forget()
            if job.kind == "download" and job.started:
                self.pause_button.pack_forget()
                self.downloading = False
                self.progress.stop()
            return
        if not profile:
            return
        label = self.profile_label(profile)
        if event in ("queued", "started"):
            self.row_actions.schedule_render()
            self.set_widget_states()
            self.cancel_button.pack(side="left", padx=(8, 0))
            self.status.set(label + "：" + ("检查中" if job.kind == "check" else "下载中" if job.started else "排队中"))
            if event == "started" and job.kind == "download":
                self.cancel_event = job.control
                self.downloading = True
                self.progress.configure(value=0, mode="determinate")
                self.pause_button.configure(text="⏸")
                self.pause_button.pack(side="left", padx=(8, 0))
        elif event == "catalog":
            self.apply_catalog(profile, value)
            self.status.set(label + "：检查完成。")
        elif event == "progress":
            progress, text = value
            if not job.control.paused.is_set():
                self.status.set(label + "：" + text)
                if progress is None:
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(15)
                else:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=progress)
                    if progress >= 75:
                        self.pause_button.pack_forget()
                self.write(label + "：" + text)
        elif event == "installed":
            self.local_versions.pop(identity, None)
            self.update_labels(profile)
            self.persist()
            self.status.set(label + "：安装完成。")
        elif event in ("error", "cancelled"):
            self.status.set(label + "：" + value)
            self.write(label + "：" + value)

    def poll(self):
        self.tasks.drain()
        try:
            while True:
                event = self.events.get_nowait()
                kind, value = event[:2]
                token = event[2] if len(event) == 3 else self.generation
                if kind == "done":
                    self.busy = False
                    self.downloading = False
                    self.pause_button.pack_forget()
                    self.cancel_button.pack_forget()
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.set_widget_states()
                elif token != self.generation:
                    continue
                elif kind in ("release", "catalog", "checked_catalog", "check_progress", "check_error", "check_finished") and self.cancel_event.is_set():
                    continue
                elif kind in ("release", "catalog"):
                    if self.profile:
                        self.apply_catalog(self.profile, value if kind == "catalog" else [value])
                elif kind == "checked_catalog":
                    identity, repo, catalog = value
                    profile = next((profile for profile in self.app.custom_profiles
                                    if profile["id"] == identity and profile["repository"] == repo), None)
                    if profile:
                        self.apply_catalog(profile, catalog)
                elif kind == "check_progress":
                    progress, text = value
                    self.progress.configure(mode="determinate", value=progress)
                    self.status.set(text)
                elif kind == "check_error":
                    self.write(value)
                elif kind == "check_finished":
                    self.progress.configure(value=100)
                    self.status.set(value)
                    self.write(value)
                elif kind == "progress":
                    progress, text = value
                    if self.cancel_event.is_set() or self.cancel_event.paused.is_set():
                        continue
                    if progress is not None and progress >= 75:
                        self.downloading = False
                        self.pause_button.pack_forget()
                        self.set_widget_states()
                    self.status.set(text)
                    if progress is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(15)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=progress)
                    self.write(text)
                elif kind == "installed":
                    if self.profile:
                        self.local_versions.pop(self.profile["id"], None)
                    self.persist()
                elif kind in ("error", "cancelled"):
                    self.status.set(value)
                    self.write(value)
        except queue.Empty:
            pass
        self.window.after(100, self.poll)

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
