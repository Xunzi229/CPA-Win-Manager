"""Dedicated table for GitHub installers."""
import copy
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import uuid
import webbrowser

from cpa_manager.backends import github
from cpa_manager.backends import installer as backend
from cpa_manager.core.download import DownloadControl, DownloadCancelled, size_text
from cpa_manager.ui.widgets.table_choices import TableChoices
from cpa_manager.ui.widgets.table_order import TableOrder
from cpa_manager.ui.widgets.frozen_actions import FrozenActions
from cpa_manager.ui.widgets.table_badges import TableBadges
from cpa_manager.ui.widgets.dialog_position import center_dialog
from cpa_manager.ui.widgets.software_library import SoftwareLibrary
from cpa_manager.core.models import ChoiceState
from cpa_manager.core.installed_software import scan_installed, match_installed, launch_uninstaller, installed_update_available


class InstallerPage:
    key = "installers"
    checking = False

    def __init__(self, app, notebook):
        self.app, self.window = app, app.window
        self.busy = False
        self.events = queue.Queue()
        self.control = DownloadControl()
        self.row_states = {}
        self.installed_records = []
        self.local_scanning = False
        self.local_scanned = False
        self.widgets = []
        self.frame = ttk.Frame(notebook, padding=16)
        self.repository = tk.StringVar()
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
        area.pack(fill="both", expand=True)
        columns = ("name", "repository", "package", "size", "latest", "local")
        self.table = ttk.Treeview(area, columns=columns, show="headings", selectmode="browse", height=9)
        for key, title, width in zip(columns, ("软件", "GitHub 地址", "对应包 ▾", "包大小", "最新版本", "本地安装版本"),
                                     (110, 220, 240, 90, 120, 110)):
            self.table.heading(key, text=title, anchor="center")
            self.table.column(key, width=width, minwidth=70, stretch=False, anchor="center")
        self.row_actions = FrozenActions(area, self.table,
            (("check", "检查", 52), ("install", "安装", 52), ("uninstall", "卸载", 52)),
            self.run_row_action, lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False),
            lambda: self.inline.close() if hasattr(self, "inline") else None,
            visible=self.row_action_visible)
        self.update_badges = TableBadges(self.table, "latest")
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
        area.rowconfigure(0, weight=1)
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
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=4)
        for text, action in (("检查全部", lambda: self.run("check", all_rows=True)),
                             ("刷新本地版本", self.refresh_installed),
                             ("下载全部待更新", lambda: self.run("download", all_rows=True))):
            self.button(row, text, action)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=4)
        self.button(row, "清空此行下载包", lambda: self.clear(False))
        self.button(row, "清空全部下载包", lambda: self.clear(True))
        self.button(row, "打开下载目录", self.open_folder)
        self.button(row, "移除记录", self.remove)
        self.status = tk.StringVar(value="填写 GitHub 地址添加软件，或选择一行检查、安装、更新。")
        ttk.Label(self.frame, textvariable=self.status, wraplength=880).pack(anchor="w", pady=8)
        row = ttk.Frame(self.frame)
        row.pack(fill="x")
        self.progress = ttk.Progressbar(row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.pause = ttk.Button(row, text="⏸", width=3, command=self.toggle_pause)
        self.cancel_button = ttk.Button(row, text="取消任务", command=self.request_stop)
        for profile in app.installer_profiles:
            self.update_row(profile)
        self.window.after(100, self.poll)
        self.window.after(300, self.refresh_installed)
        self.window.after(30000, self.poll_installed)

    def button(self, parent, text, action):
        btn_style = "Primary.TButton" if text == "下载全部待更新" else "TButton"
        button = ttk.Button(parent, text=text, command=action, style=btn_style)
        button.pack(side="left", padx=(0, 8))
        self.widgets.append(button)

    def row_action_visible(self, row, action):
        if action != "uninstall":
            return True
        profile = next((p for p in self.app.installer_profiles if p["id"] == row), None)
        return profile is not None and match_installed(profile, self.installed_records) is not None

    def run_row_action(self, row, action):
        if self.busy or self.app.manager_busy or getattr(self.app, "closing", False):
            return
        if not any(p["id"] == row for p in self.app.installer_profiles):
            return
        self.table.selection_set(row)
        self.show_selection()
        self.run(action)

    def context_menu(self, event):
        row = self.table.identify_row(event.y)
        self.inline.close()
        if not row or self.busy or self.app.manager_busy:
            return
        self.table.selection_set(row)
        self.show_selection()
        menu = tk.Menu(self.table, tearoff=False)
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
            table.heading(key, text=label)
            table.column(key, width=width, anchor="center")
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
        self.status.set(profile["name"] + "：已关联 " + record["name"])
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
        self.status.set(profile["name"] + "：已取消关联，需要时可重新手动关联。")

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
        if key != "repository" or self.busy or self.app.manager_busy:
            return None
        self.table.selection_set(row)
        self.show_selection()
        return self.profile()["repository"]

    def address_commit(self, row, key, value):
        profile = self.profile()
        if self.busy or not profile or profile["id"] != row:
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
            profile.update(repository=repo, release=None, selected_asset="")
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
        values = (profile["name"], profile["repository"], package, size_text(asset.get("size")) if asset else "—",
                  release.get("tag", "—"), local)
        if self.table.exists(profile["id"]):
            self.table.item(profile["id"], values=values)
        else:
            self.table.insert("", "end", iid=profile["id"], values=values)
        self.update_badges.set(profile["id"], bool(installed) and installed_update_available(installed["version"], release.get("tag")))
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
        if self.busy or not profile:
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
        if key != "package":
            return [], ""
        self.table.selection_set(row)
        self.show_selection()
        return list(self.package["values"]), self.package.get()

    def inline_commit(self, row, key, value):
        self.table.selection_set(row)
        self.show_selection()
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
            lambda: not self.busy and not self.app.manager_busy and not getattr(self.app, "closing", False))

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
        if profile:
            name = simpledialog.askstring("软件名称", "自定义软件名称：", initialvalue=profile["name"], parent=self.window)
            if name and name.strip():
                profile["name"] = name.strip()
                self.update_row(profile)
                self.persist()

    def remove(self):
        profile = self.profile()
        if self.busy or not profile:
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
        snapshots = copy.deepcopy(rows)
        proxy, directory = self.app.proxy_url(), self.target()
        self.control = DownloadControl()
        control = self.control
        self.stop_after_current = False
        self.set_busy(True)
        self.cancel_button.pack(side="left", padx=(8, 0))
        self.progress.configure(value=0, mode="determinate")
        self.status.set("正在处理软件列表…")
        def worker():
            for profile in snapshots:
                if self.stop_after_current:
                    break
                try:
                    control.next_stage()
                    if action in ("check", "update", "download") or not profile.get("release"):
                        self.events.put(("state", (profile["id"], "检查中")))
                        profile = backend.refresh(profile, proxy)
                        github.transfer.check_cancel(control)
                        self.events.put(("profile", profile))
                    if action == "check":
                        continue
                    if action == "download" and backend.selected_asset(profile) is None:
                        continue
                    if action == "download" and backend.cached_download(profile, verify=True):
                        continue
                    github.transfer.check_cancel(control)
                    self.events.put(("state", (profile["id"], "下载中")))
                    self.events.put(("transfer", None))
                    report = lambda progress, text, name=profile["name"]: self.events.put(("progress", (progress, name + "：" + text)))
                    profile, path = backend.prepare(profile, directory, proxy, report, control)
                    self.events.put(("downloaded", (profile, path, action in ("install", "update"))))
                except DownloadCancelled:
                    self.events.put(("error", (profile["id"], "任务已停止")))
                    break
                except Exception as error:
                    self.events.put(("error", (profile["id"], str(error))))
            control.finish()
            self.events.put(("done", None))
        threading.Thread(target=worker, daemon=True).start()

    def request_stop(self):
        if self.busy:
            self.stop_after_current = True
            cancelled = self.control.request_cancel()
            self.status.set("正在取消任务，保留下载缓存…" if cancelled else "正在保存安装包，完成后即可退出。")
            return cancelled
        return True

    def clear(self, all_rows):
        if self.busy:
            return
        rows = self.app.installer_profiles if all_rows else [self.profile()] if self.profile() else []
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
        self.progress.configure(value=0)
        self.pause.pack_forget()

    def replace_profile(self, profile):
        for index, existing in enumerate(self.app.installer_profiles):
            if existing["id"] == profile["id"]:
                if existing.get("installed_id"):
                    profile["installed_id"] = existing["installed_id"]
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
                elif kind == "installed_error":
                    self.local_scanning = False
                    self.status.set("读取已安装软件失败：" + value)
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
                    if self.persist() and launch and not self.control.is_set() and not self.stop_after_current and not getattr(self.app, "closing", False):
                        try:
                            os.startfile(path)
                            self.status.set(profile["name"] + "：已打开安装向导。")
                        except OSError as error:
                            self.status.set("打开安装器失败：" + str(error))
                    self.show_selection()
                elif kind == "error":
                    identifier, text = value
                    self.row_states[identifier] = "失败"
                    profile = next(p for p in self.app.installer_profiles if p["id"] == identifier)
                    self.update_row(profile)
                    self.status.set(profile["name"] + "：" + text)
                elif kind == "done":
                    self.cancel_button.pack_forget()
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.pause.pack_forget()
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.window.after(100, self.poll)
