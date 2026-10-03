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


class PortablePage:
    key = "custom"
    checking = False

    def __init__(self, app, notebook, root):
        self.app, self.window, self.root = app, app.window, root
        self.busy = False
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
        self.fetch_timer = None
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
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="免安装软件：").pack(side="left")
        self.selector = ChoiceState(self.names)
        for text, action in (("解压安装根目录", self.add), ("移除记录", self.remove)):
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(8, 0))
            self.widgets.append((button, "normal"))
        area = ttk.Frame(self.frame)
        area.pack(fill="both", expand=True, pady=(0, 8))
        columns = ("directory", "repository", "local", "latest", "version", "package", "size")
        self.table = ttk.Treeview(area, columns=columns, show="headings", selectmode="browse", height=7)
        for key, label, width in zip(columns,
                ("软件安装目录", "GitHub 地址", "本地版本", "最新版本", "选择安装版本 ▾", "对应包 ▾", "包大小"),
                (220, 200, 90, 90, 110, 220, 90)):
            self.table.heading(key, text=label, anchor="center")
            self.table.column(key, width=width, minwidth=70, stretch=False, anchor="center")
        self.row_actions = FrozenActions(area, self.table,
            (("install", "安装 / 更新", 88), ("open", "打开目录", 76), ("clear", "清空安装备份", 104)),
            self.run_row_action, lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            lambda: self.inline.close() if hasattr(self, "inline") else None)
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
            widget.pack(side="left", expand=True, fill="x")
            self.widgets.append((widget, "readonly" if key == "repository" else "normal"))
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
        button.pack(side="left", padx=(8, 0))
        self.widgets.append((button, "normal"))
        self.help_label = ttk.Label(self.frame, text="免安装软件直接安装到已选软件目录；EXE / MSI 安装器请使用“安装向导软件”页，下载目录在顶部“设置”中统一配置。\n"
                  "仅覆盖包内同名文件，包外文件不删除。额外保留路径默认留空，用分号分隔，例如 config.yaml;data。",
                  wraplength=800)
        self.help_label.pack(anchor="w", pady=8)
        self.save_status = tk.StringVar(value="配置修改后自动保存")
        ttk.Label(self.frame, textvariable=self.save_status).pack(anchor="w", pady=(0, 4))
        ttk.Label(self.frame, text="双击安装目录或 GitHub 地址可编辑，回车保存、Esc 取消；右键可选择安装目录。点击版本或对应包可直接选择。",
                  wraplength=800).pack(anchor="w")
        self.status = tk.StringVar(value="添加软件，填写 GitHub 地址后获取发布附件。")
        ttk.Label(self.frame, textvariable=self.status, wraplength=800).pack(anchor="w", pady=8)
        progress_row = ttk.Frame(self.frame)
        progress_row.pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(progress_row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.pause_button = ttk.Button(progress_row, text="⏸", width=3, command=self.toggle_pause)
        self.cancel_button = ttk.Button(progress_row, text="取消任务", command=self.request_stop)
        self.log = scrolledtext.ScrolledText(self.frame, state="disabled", height=4)
        style_log_widget(self.log)
        self.log.pack(fill="x")
        self.refresh_names(auto_fetch=False)
        self.loading_profile = False
        self.window.after(100, self.poll)

    valid_catalog = staticmethod(github.valid_catalog)

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
                          profile.get("latest_version", "—"), profile.get("selected_version", "—"),
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
        if key not in ("version", "package"):
            return [], ""
        self.table.selection_set(row)
        self.table_selected()
        if self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return [], ""
        if self.catalog:
            # Do not let the delayed automatic refresh close an open cell editor.
            self.cancel_auto_check()
        if key == "version":
            return [r["tag"] for r in self.catalog], self.version.get()
        return list(self.asset_selector["values"]), self.asset.get()

    def address_value(self, row, key):
        if key not in ("repository", "directory") or self.busy or self.app.manager_busy:
            return None
        self.table.selection_set(row)
        self.table_selected()
        if self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
            return None
        return self.variables[key].get()

    def edit_repository(self, *_):
        if not self.profile or self.busy or self.app.manager_busy:
            return "break"
        self.repository_entry.configure(state="normal")
        self.repository_entry.focus_set()
        return "break"

    def finish_repository_edit(self, *_):
        if str(self.repository_entry.cget("state")) == "normal" and self.persist(quiet=True):
            self.repository_entry.configure(state="readonly")
        return "break"

    def address_commit(self, row, key, value):
        if key not in ("repository", "directory") or self.busy or self.app.manager_busy or self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
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
        if not self.profile or self.busy or self.app.manager_busy:
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
        if self.profile is not next((p for p in self.app.custom_profiles if p["id"] == row), None):
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
        self.select(auto_fetch=False, index=index)

    def run_row_action(self, row, action):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        if not self.table.exists(row):
            return
        self.table.selection_set(row)
        self.table_selected()
        if not self.profile or self.profile["id"] != row:
            return
        commands = {"install": self.install, "open": self.open_folder, "clear": self.clear_backups}
        if action in commands:
            commands[action]()

    def context_menu(self, event):
        row = self.table.identify_row(event.y)
        self.inline.close()
        if not row or self.busy or self.app.manager_busy:
            return
        self.table.selection_set(row)
        self.table_selected()
        self.cancel_auto_check()
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

    def refresh_names(self, auto_fetch=False):
        self.update_labels()
        if self.app.custom_profiles:
            self.selector.current(0)
            self.select(auto_fetch=auto_fetch)
        else:
            self.profile = None
            self.names.set("")
            for key, variable in self.variables.items():
                variable.set("")
            self.invalidate()

    def select(self, auto_fetch=False, index=None):
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
                self.status.set("已使用缓存的版本和附件，可手动检查更新以获取最新数据。")
        if auto_fetch and not self.catalog and self.profile and self.variables["repository"].get().strip():
            self.status.set("正在准备获取当前软件的附件…")
            self.fetch_timer = self.window.after(250, self.auto_check)

    def auto_check(self):
        self.fetch_timer = None
        if self.busy or self.app.manager_busy:
            self.fetch_timer = self.window.after(250, self.auto_check)
            return
        self.check()

    def choose_portable(self, directory):
        directory = str(Path(directory).expanduser().resolve())
        self.add_profile({"name": Path(directory).name, "mode": "便携安装",
                          "repository": "", "directory": directory, "preserve": ""})

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
        if not self.profile:
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
        self.cancel_auto_check()
        self.clear_download_state()
        self.release, self.assets = None, []
        self.catalog = []
        if hasattr(self, "version_selector"):
            self.version_selector.configure(values=[])
            self.version.set("")
        if hasattr(self, "asset_selector"):
            self.asset_selector.configure(values=[])
            self.asset.set("")
            self.status.set("配置已切换，请重新检查发布附件。")

    def cancel_auto_check(self):
        if self.fetch_timer is not None:
            self.window.after_cancel(self.fetch_timer)
            self.fetch_timer = None

    def clear_download_state(self):
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
        if not self.clear_download_state():
            self.asset.set(self.profile.get("selected_asset", "") if self.profile else "")
            return
        index = self.asset_selector.current()
        if self.profile and 0 <= index < len(self.assets):
            self.profile["selected_asset"] = self.assets[index]["name"]
            self.persist()
        self.status.set("已切换附件，可安装所选包。")

    def version_changed(self, *_):
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
        self.cancel_auto_check()
        if self.busy:
            cancelled = self.cancel_event.request_cancel()
            self.status.set("正在取消任务，保留下载缓存…" if cancelled else "正在写入安装文件，完成后即可退出。")
            return cancelled
        return True

    def set_widget_states(self, downloading=False):
        self.row_actions.set_enabled(not self.busy)
        if self.busy:
            self.inline.close()
        for widget, state in self.widgets:
            allow_switch = downloading and widget in (self.selector, self.asset_selector, self.version_selector)
            widget.configure(state=state if not self.busy or allow_switch else "disabled")

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
        emit = lambda kind, value: self.events.put((kind, value, token))
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

    def check(self):
        self.cancel_auto_check()
        if self.busy or self.app.manager_busy:
            self.status.set("请等待当前操作完成。")
            return
        if not self.profile:
            self.status.set("请先添加软件。")
            return
        if not self.persist():
            return
        repo, proxy = self.variables["repository"].get(), self.app.proxy_url()
        self.clear_download_state()
        self.status.set("正在查询最新正式 Release…")
        self.start(lambda emit, _control: emit("catalog", github.release_catalog(repo, proxy)))

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
        release, target, proxy = self.release, self.target(), self.app.proxy_url()
        preserve = self.variables["preserve"].get()
        self.clear_download_state()
        def task(emit, control):
            report = lambda progress, text: emit("progress", (progress, text))
            backend.install(release, asset, target, preserve, proxy, report, control)
            emit("installed", None)
        self.start(task, downloading=True)

    def poll(self):
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
                elif kind in ("release", "catalog") and self.cancel_event.is_set():
                    continue
                elif kind in ("release", "catalog"):
                    self.catalog = value if kind == "catalog" else [value]
                    if self.profile:
                        self.row_catalogs[self.profile["id"]] = self.catalog
                        self.cache.put(self.profile["repository"], self.catalog)
                        self.local_versions.pop(self.profile["id"], None)
                    self.version_selector.configure(values=[r["tag"] for r in self.catalog])
                    if self.profile:
                        self.profile["latest_version"] = self.catalog[0]["tag"]
                    selected = next((r for r in self.catalog if self.profile and r["tag"] == self.profile.get("selected_version")), self.catalog[0])
                    self.display_release(selected)
                    self.persist()
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
