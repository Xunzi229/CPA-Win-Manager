"""Custom GitHub software installation page."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import urllib.error
import uuid

import generic_backend as backend
from resumable_download import DownloadCancelled, size_text


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
        self.cancel_event = threading.Event()
        self.frame = ttk.Frame(notebook, padding=16)
        self.names = tk.StringVar()
        self.variables = {key: tk.StringVar() for key in
                          ("repository", "directory", "mode", "preserve")}
        self.profile = None
        self.widgets = []
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="已保存软件：").pack(side="left")
        self.selector = ttk.Combobox(row, textvariable=self.names, state="readonly")
        self.selector.pack(side="left", fill="x", expand=True)
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.select())
        self.widgets.append((self.selector, "readonly"))
        for text, action in (("添加软件", self.add), ("移除记录", self.remove)):
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(8, 0))
            self.widgets.append((button, "normal"))
        labels = {"repository": "GitHub 地址：", "directory": "安装 / 下载目录：",
                  "mode": "安装方式：", "preserve": "额外保留文件 / 目录："}
        for key, label in labels.items():
            row = ttk.Frame(self.frame)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=19).pack(side="left")
            if key == "mode":
                widget = ttk.Combobox(row, textvariable=self.variables[key],
                                      values=("便携安装", "安装器"), state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _: self.invalidate())
            else:
                widget = ttk.Entry(row, textvariable=self.variables[key])
            widget.pack(side="left", expand=True, fill="x")
            self.widgets.append((widget, "readonly" if key == "mode" else "normal"))
            if key == "repository":
                button = ttk.Button(row, text="检查 / 获取附件", command=self.check)
                button.pack(side="left", padx=(8, 0))
                self.widgets.append((button, "normal"))
            if key == "directory":
                button = ttk.Button(row, text="选择目录", command=self.browse)
                button.pack(side="left", padx=(8, 0))
                self.widgets.append((button, "normal"))
        self.variables["repository"].trace_add("write", lambda *_: self.invalidate())
        ttk.Label(self.frame, text="便携安装支持 ZIP / 单 EXE；安装器支持 EXE / MSI，由软件安装向导决定安装位置。\n"
                  "仅覆盖包内同名文件，包外文件不删除。额外保留路径默认留空，用分号分隔，例如 config.yaml;data。",
                  wraplength=800).pack(anchor="w", pady=8)
        row = ttk.Frame(self.frame)
        row.pack(fill="x", pady=6)
        for text, action in (("保存配置", self.persist),
                             ("安装 / 更新", self.install), ("打开目录", self.open_folder)):
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(0, 10))
            self.widgets.append((button, "normal"))
        self.cancel_button = ttk.Button(row, text="中断下载", command=self.cancel_download, state="disabled")
        self.cancel_button.pack(side="left")
        self.asset = tk.StringVar()
        self.asset_selector = ttk.Combobox(self.frame, textvariable=self.asset, state="readonly")
        self.asset_selector.pack(fill="x", pady=6)
        self.widgets.append((self.asset_selector, "readonly"))
        self.status = tk.StringVar(value="添加软件，填写 GitHub 地址后获取发布附件。")
        ttk.Label(self.frame, textvariable=self.status, wraplength=800).pack(anchor="w", pady=8)
        self.progress = ttk.Progressbar(self.frame, maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))
        self.log = scrolledtext.ScrolledText(self.frame, state="disabled", height=10)
        self.log.pack(fill="both", expand=True)
        self.refresh_names(auto_fetch=False)
        self.window.after(100, self.poll)

    def refresh_names(self, auto_fetch=True):
        self.selector.configure(values=[p["name"] for p in self.app.custom_profiles])
        if self.app.custom_profiles:
            self.names.set(self.app.custom_profiles[0]["name"])
            self.select(auto_fetch=auto_fetch)
        else:
            self.profile = None
            self.names.set("")
            for key, variable in self.variables.items():
                variable.set("便携安装" if key == "mode" else "")
            self.invalidate()

    def select(self, auto_fetch=True):
        if self.profile and not self.persist():
            self.names.set(self.profile["name"])
            return
        self.profile = next((p for p in self.app.custom_profiles if p["name"] == self.names.get()), None)
        if self.profile:
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

    def add(self):
        if not self.persist():
            return
        name = simpledialog.askstring("添加软件", "软件名称：", parent=self.window)
        if not name or not name.strip():
            return
        name = name.strip()
        if any(p["name"] == name for p in self.app.custom_profiles):
            messagebox.showerror("名称重复", "请使用不同的软件名称。", parent=self.window)
            return
        profile = {"name": name, "mode": "便携安装", "repository": "",
                   "directory": str(self.root / "CustomApps" / uuid.uuid4().hex[:8]),
                   "preserve": ""}
        self.app.custom_profiles.append(profile)
        self.profile = None
        self.selector.configure(values=[p["name"] for p in self.app.custom_profiles])
        self.names.set(name)
        self.select()
        self.persist()

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
        try:
            self.app.save()
            return True
        except OSError as error:
            messagebox.showerror("保存失败", str(error), parent=self.window)
            return False

    def target(self):
        return Path(self.variables["directory"].get() or self.root / "CustomApps").expanduser().resolve()

    def browse(self):
        directory = filedialog.askdirectory(parent=self.window)
        if directory:
            self.variables["directory"].set(directory)

    def open_folder(self):
        if self.target().is_dir():
            os.startfile(self.target())
        else:
            self.status.set("目录尚未创建，请先安装或选择已有目录。")

    def invalidate(self):
        self.cancel_auto_check()
        self.release, self.assets = None, []
        if hasattr(self, "asset_selector"):
            self.asset_selector.configure(values=[])
            self.asset.set("")
            self.status.set("配置已切换，请重新检查发布附件。")

    def cancel_auto_check(self):
        if self.fetch_timer is not None:
            self.window.after_cancel(self.fetch_timer)
            self.fetch_timer = None

    def cancel_download(self):
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在中断下载，分段将保留以便下次继续…")

    def start(self, task, downloading=False):
        if self.busy or self.app.manager_busy:
            self.status.set("请等待当前操作完成。")
            return
        self.busy = True
        self.cancel_event.clear()
        self.cancel_button.configure(state="normal" if downloading else "disabled")
        for widget, _ in self.widgets:
            widget.configure(state="disabled")
        def worker():
            try:
                task()
            except DownloadCancelled as error:
                self.events.put(("cancelled", str(error)))
            except Exception as error:
                text = "GitHub 请求失败（可能没有正式 Release、网络不可用或 API 限流）：" + str(error) if isinstance(error, urllib.error.HTTPError) else str(error)
                self.events.put(("error", text))
            finally:
                self.events.put(("done", None))
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
        self.status.set("正在查询最新正式 Release…")
        self.start(lambda: self.events.put(("release", backend.releases(repo, proxy))))

    def install(self):
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
        mode = self.variables["mode"].get()
        text = ("将下载安装器，完成后可打开安装向导。" if mode == "安装器" else
                "仅覆盖安装包内的同名文件，其他已有文件不覆盖、不删除。请先关闭目标软件；额外保留路径不会覆盖。")
        if not messagebox.askyesno("确认安装", f"{self.profile['name']} · {self.release['tag']}\n附件：{asset['name']}\n大小：{size_text(asset.get('size'))}\n目录：{self.target()}\n\n{text}", parent=self.window):
            return
        if not self.persist():
            return
        release, target, proxy = self.release, self.target(), self.app.proxy_url()
        preserve = self.variables["preserve"].get()
        report = lambda progress, text: self.events.put(("progress", (progress, text)))
        def task():
            if mode == "安装器":
                path = backend.download_installer(release, asset, target, proxy, report, self.cancel_event)
                self.events.put(("installer", path))
            else:
                backend.install(release, asset, target, preserve, proxy, report, self.cancel_event)
                self.events.put(("installed", None))
        self.start(task, downloading=True)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self.busy = False
                    self.cancel_button.configure(state="disabled")
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    for widget, state in self.widgets:
                        widget.configure(state=state)
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
                    if self.cancel_event.is_set():
                        continue
                    if progress is not None and progress >= 75:
                        self.cancel_button.configure(state="disabled")
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
