"""Custom GitHub software installation page."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import urllib.error

import generic_backend as backend
from resumable_download import DownloadCancelled, DownloadControl, size_text
from runtime_utils import default_download_directory


class GenericPage:
    key = "custom"
    checking = False

    def __init__(self, app, notebook, root):
        self.app, self.window, self.root = app, app.window, root
        self.busy = False
        self.release = None
        self.assets = []
        self.events = queue.Queue()
        self.fetch_timer = None
        self.cancel_event = DownloadControl()
        self.generation = 0
        self.downloading = False
        self.frame = ttk.Frame(notebook, padding=16)
        self.names = tk.StringVar()
        self.variables = {key: tk.StringVar() for key in
                          ("repository", "directory", "mode", "preserve")}
        if not hasattr(app, "installer_download_directory"):
            app.installer_download_directory = str(default_download_directory())
        self.profile = None
        self.widgets = []
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="已保存软件：").pack(side="left")
        self.selector = ttk.Combobox(row, textvariable=self.names, state="readonly")
        self.selector.pack(side="left", fill="x", expand=True)
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.select())
        self.widgets.append((self.selector, "readonly"))
        for text, action in (("添加便携软件", self.add),
                             ("添加安装器软件", lambda: self.add("安装器")),
                             ("移除记录", self.remove)):
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(8, 0))
            self.widgets.append((button, "normal"))
        labels = {"repository": "GitHub 地址：",
                  "mode": "安装方式：", "preserve": "额外保留文件 / 目录："}
        for key, label in labels.items():
            row = ttk.Frame(self.frame)
            row.pack(fill="x", pady=4)
            if key == "preserve":
                self.preserve_row = row
            ttk.Label(row, text=label, width=19).pack(side="left")
            if key == "mode":
                widget = ttk.Combobox(row, textvariable=self.variables[key],
                                      values=("便携安装", "安装器"), state="readonly")
                self.mode_selector = widget
            else:
                widget = ttk.Entry(row, textvariable=self.variables[key])
            widget.pack(side="left", expand=True, fill="x")
            self.widgets.append((widget, "readonly" if key == "mode" else "normal"))
            if key == "repository":
                button = ttk.Button(row, text="检查 / 获取附件", command=self.check)
                button.pack(side="left", padx=(8, 0))
                self.widgets.append((button, "normal"))
        self.variables["repository"].trace_add("write", lambda *_: self.invalidate())
        self.help_label = ttk.Label(self.frame, text="便携软件直接安装到已选软件目录；安装器下载目录在顶部“设置”中统一配置，实际安装位置由安装向导决定。\n"
                  "仅覆盖包内同名文件，包外文件不删除。额外保留路径默认留空，用分号分隔，例如 config.yaml;data。",
                  wraplength=800)
        self.help_label.pack(anchor="w", pady=8)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=6)
        for text, action in (("保存配置", self.persist),
                             ("安装 / 更新", self.install), ("打开目录", self.open_folder)):
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(0, 10))
            self.widgets.append((button, "normal"))
        self.asset = tk.StringVar()
        self.asset_selector = ttk.Combobox(self.frame, textvariable=self.asset, state="readonly")
        self.asset_selector.pack(fill="x", pady=6)
        self.asset_selector.bind("<<ComboboxSelected>>", self.asset_changed)
        self.widgets.append((self.asset_selector, "readonly"))
        self.status = tk.StringVar(value="添加软件，填写 GitHub 地址后获取发布附件。")
        ttk.Label(self.frame, textvariable=self.status, wraplength=800).pack(anchor="w", pady=8)
        progress_row = ttk.Frame(self.frame)
        progress_row.pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(progress_row, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.pause_button = ttk.Button(progress_row, text="⏸", width=3, command=self.toggle_pause)
        self.log = scrolledtext.ScrolledText(self.frame, state="disabled", height=10)
        self.log.pack(fill="both", expand=True)
        self.variables["mode"].trace_add("write", self.mode_changed)
        self.refresh_names(auto_fetch=False)
        self.window.after(100, self.poll)

    @staticmethod
    def profile_label(profile):
        if profile.get("mode", "便携安装") == "便携安装":
            return profile.get("directory") or profile["name"]
        return profile["name"]

    def update_labels(self):
        self.selector.configure(values=[self.profile_label(p) for p in self.app.custom_profiles])
        if self.profile:
            self.names.set(self.profile_label(self.profile))

    def refresh_names(self, auto_fetch=True):
        self.update_labels()
        if self.app.custom_profiles:
            self.selector.current(0)
            self.select(auto_fetch=auto_fetch)
        else:
            self.profile = None
            self.names.set("")
            for key, variable in self.variables.items():
                variable.set("便携安装" if key == "mode" else "")
            self.invalidate()

    def select(self, auto_fetch=True):
        index = self.selector.current()
        if self.profile and not self.persist():
            self.names.set(self.profile_label(self.profile))
            return
        self.profile = self.app.custom_profiles[index] if 0 <= index < len(self.app.custom_profiles) else None
        if self.profile:
            self.names.set(self.profile_label(self.profile))
            for key, variable in self.variables.items():
                variable.set(self.profile.get(key, "便携安装" if key == "mode" else ""))
        self.invalidate()
        if auto_fetch and self.profile and self.variables["repository"].get().strip():
            self.status.set("正在准备获取当前软件的附件…")
            self.fetch_timer = self.window.after(250, self.auto_check)

    def auto_check(self):
        self.fetch_timer = None
        if self.busy or self.app.manager_busy:
            self.fetch_timer = self.window.after(250, self.auto_check)
            return
        self.check()

    def mode_changed(self, *_):
        if self.variables["mode"].get() == "安装器":
            self.preserve_row.pack_forget()
        else:
            if not self.preserve_row.winfo_manager():
                self.preserve_row.pack(fill="x", pady=4, before=self.help_label)
        self.invalidate()

    @staticmethod
    def directory_key(directory):
        return os.path.normcase(str(Path(directory).expanduser().resolve()))

    def choose_portable(self, directory):
        directory = str(Path(directory).expanduser().resolve())
        for index, profile in enumerate(self.app.custom_profiles):
            if (profile.get("mode", "便携安装") == "便携安装" and profile.get("directory")
                    and self.directory_key(profile["directory"]) == self.directory_key(directory)):
                self.selector.current(index)
                self.select()
                return
        self.add_profile({"name": Path(directory).name, "mode": "便携安装",
                          "repository": "", "directory": directory, "preserve": ""})

    def add_profile(self, profile):
        self.app.custom_profiles.append(profile)
        self.profile = None
        self.update_labels()
        self.selector.current(len(self.app.custom_profiles) - 1)
        self.select()
        self.persist()

    def add(self, mode="便携安装"):
        if not self.persist():
            return
        if mode == "便携安装":
            directory = filedialog.askdirectory(parent=self.window, title="选择便携软件的安装目录")
            if directory:
                self.choose_portable(directory)
            return
        name = simpledialog.askstring("添加安装器软件", "自定义软件名称：", parent=self.window)
        if not name or not name.strip():
            return
        name = name.strip()
        if any(p.get("mode") == "安装器" and p["name"] == name for p in self.app.custom_profiles):
            messagebox.showerror("名称重复", "请使用不同的软件名称。", parent=self.window)
            return
        profile = {"name": name, "mode": "安装器", "repository": "",
                   "preserve": ""}
        self.add_profile(profile)

    def remove(self):
        if not self.profile:
            return
        if not messagebox.askyesno("移除记录", "仅移除软件配置，保留已安装文件。", parent=self.window):
            return
        self.app.custom_profiles.remove(self.profile)
        self.profile = None
        self.refresh_names()
        self.persist()

    def persist(self):
        if self.profile:
            self.profile.pop("pattern", None)
            self.profile.update({k: v.get().strip() for k, v in self.variables.items()})
            if self.variables["mode"].get() == "安装器":
                self.profile.pop("directory", None)
        try:
            self.app.save()
            self.update_labels()
            return True
        except OSError as error:
            messagebox.showerror("保存失败", str(error), parent=self.window)
            return False

    def target(self):
        if self.variables["mode"].get() == "安装器":
            return Path(self.app.installer_download_directory or default_download_directory()).expanduser().resolve()
        return Path(self.variables["directory"].get() or self.root / "CustomApps").expanduser().resolve()

    def open_folder(self):
        if self.target().is_dir():
            os.startfile(self.target())
        else:
            self.status.set("目录尚未创建，请先安装或选择已有目录。")

    def invalidate(self):
        self.cancel_auto_check()
        self.clear_download_state()
        self.release, self.assets = None, []
        if hasattr(self, "asset_selector"):
            self.asset_selector.configure(values=[])
            self.asset.set("")
            self.status.set("配置已切换，请重新检查发布附件。")

    def cancel_auto_check(self):
        if self.fetch_timer is not None:
            self.window.after_cancel(self.fetch_timer)
            self.fetch_timer = None

    def clear_download_state(self):
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

    def asset_changed(self, *_):
        self.clear_download_state()
        self.status.set("已切换附件，可安装所选包。")

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

    def set_widget_states(self, downloading=False):
        for widget, state in self.widgets:
            allow_switch = downloading and widget in (self.selector, self.asset_selector, self.mode_selector)
            widget.configure(state=state if not self.busy or allow_switch else "disabled")

    def start(self, task, downloading=False):
        if self.busy or self.app.manager_busy:
            self.status.set("请等待当前操作完成。")
            return
        self.busy = True
        self.cancel_event = DownloadControl()
        self.downloading = downloading
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
        self.start(lambda emit, _control: emit("release", backend.releases(repo, proxy)))

    def install(self):
        if self.busy:
            self.status.set("请等待当前下载或安装操作结束。")
            return
        if not self.release or not self.asset.get():
            self.status.set("请先获取附件并选择安装包。")
            return
        if self.variables["mode"].get() == "便携安装" and not self.variables["directory"].get().strip():
            self.status.set("请选择独立的软件安装目录。")
            return
        index = self.asset_selector.current()
        asset = self.assets[index] if 0 <= index < len(self.assets) else None
        if not asset:
            return
        mode = self.variables["mode"].get()
        text = ("将下载安装器，完成后可打开安装向导。" if mode == "安装器" else
                "仅覆盖安装包内的同名文件，其他已有文件不覆盖、不删除。请先关闭目标软件；额外保留路径不会覆盖。")
        if not messagebox.askyesno("确认安装", f"{self.profile['name']} · {self.release['tag']}\n附件：{asset['name']}\n大小：{size_text(asset.get('size'))}\n目录：{self.target()}\n\n{text}", parent=self.window):
            return
        if not self.persist():
            return
        release, target, proxy = self.release, self.target(), self.app.proxy_url()
        preserve = self.variables["preserve"].get()
        self.clear_download_state()
        def task(emit, control):
            report = lambda progress, text: emit("progress", (progress, text))
            if mode == "安装器":
                path = backend.download_installer(release, asset, target, proxy, report, control)
                emit("installer", path)
            else:
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
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.set_widget_states()
                elif token != self.generation:
                    continue
                elif kind == "release":
                    self.release = value
                    self.assets = backend.candidates(value, self.variables["mode"].get())
                    names = [f"{a['name']}  ·  {size_text(a.get('size'))}" for a in self.assets]
                    self.asset_selector.configure(values=names)
                    self.asset.set(names[0] if len(names) == 1 else "")
                    local = backend.local_version(self.target(), value["repository"])
                    self.status.set(f"安装记录：{local or '尚无记录'}；最新发布：{value['tag']}；请选择附件。" if names else "没有支持的附件，请切换安装方式或查看项目发布页面。")
                    self.write(value["notes"])
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
                elif kind == "installer":
                    self.status.set("安装器下载完成，安装结果由软件自己的安装向导管理。")
                    if messagebox.askyesno("打开安装向导", f"已下载：{value}\n是否打开？", parent=self.window):
                        try:
                            os.startfile(value)
                        except OSError as error:
                            self.status.set("打开安装器失败：" + str(error))
                elif kind == "installed":
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
