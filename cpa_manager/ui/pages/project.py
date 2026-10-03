"""Service management page shared by CLIProxyAPI and CPA-Manager-Plus."""
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import webbrowser
from cpa_manager.ui.theme import style_log_widget

from cpa_manager.config import PROJECTS, has_update
from cpa_manager.core.paths import ROOT
from cpa_manager.core.runtime import VersionCache
from cpa_manager.core.backups import backup_directories, clear_backups


class ProjectPage:
    def __init__(self, app, notebook, key, smoke):
        self.app, self.window, self.key = app, app.window, key
        self.smoke = smoke
        self.notebook = notebook
        self.local_version = None
        self.name, self.executable, self.backend = PROJECTS[key]
        self.frame = ttk.Frame(notebook, padding=16)
        self.busy = self.checking = False
        self.generation = 0
        self.running = None
        self.save_timer = None
        self.events = queue.Queue()
        self.last_download_log = 0
        self.version_cache = VersionCache()
        self.stale_refresh_attempts = set()
        self.remote_cached = True
        settings = app.profiles[key]
        self.directory = tk.StringVar(value=settings["directory"])
        self.remote_version = settings.get("latest", {}).get(str(self.target()).lower())
        self.status = tk.StringVar(value="可检查版本，或在空目录安装最新版。")
        self.versions = tk.StringVar(value=f"本地版本：正在检测…  {self.remote_label()}")
        self.service = tk.StringVar(value="正在检测…")
        self.saved = tk.StringVar(value="目录设置自动保存")
        link = ttk.Label(self.frame, text="项目主页：" + self.backend.REPO, foreground="#0969da", cursor="hand2", takefocus=True)
        link.pack(anchor="w", pady=(0, 10))
        link.bind("<Button-1>", lambda _: webbrowser.open(self.backend.REPO))
        link.bind("<Return>", lambda _: webbrowser.open(self.backend.REPO))
        row = ttk.Frame(self.frame)
        row.pack(fill="x")
        ttk.Label(row, text="解压目录：").pack(side="left")
        self.folder_entry = ttk.Entry(row, textvariable=self.directory)
        self.folder_entry.pack(side="left", expand=True, fill="x")
        self.browse = ttk.Button(row, text="选择文件夹", command=self.choose_folder)
        self.browse.pack(side="left", padx=(8, 0))
        ttk.Label(self.frame, textvariable=self.versions).pack(anchor="w", pady=10)
        services = ttk.LabelFrame(self.frame, text="服务控制", padding=12)
        services.pack(fill="x")
        self.service_label = ttk.Label(services, textvariable=self.service, wraplength=380, font=("Microsoft YaHei UI", 9, "bold"))
        self.service_label.pack(side="left", expand=True, fill="x")
        self.start = ttk.Button(services, text="启动", command=lambda: self.run("start"), style="Success.TButton")
        self.stop = ttk.Button(services, text="停止", command=lambda: self.run("stop"), style="Danger.TButton")
        self.restart = ttk.Button(services, text="重启", command=lambda: self.run("restart"))
        for button in (self.start, self.stop, self.restart):
            button.pack(side="left", padx=(8, 0))
        self.admin_key = tk.StringVar()
        if key == "plus":
            area = ttk.LabelFrame(self.frame, text="管理员登录 Key", padding=10)
            area.pack(fill="x", pady=(10, 0))
            ttk.Entry(area, textvariable=self.admin_key, state="readonly").pack(side="left", fill="x", expand=True)
            self.copy = ttk.Button(area, text="复制 Key", command=self.copy_key, style="Primary.TButton")
            self.copy.pack(side="left", padx=8)
            self.import_key = ttk.Button(area, text="录入已有 Key", command=self.enter_key)
            self.import_key.pack(side="left")
            ttk.Label(self.frame, text="首次启动自动捕获 Key；已有 Key 可录入，使用 Windows 当前用户加密保存。").pack(anchor="w", pady=(4, 0))
            self.key_notice = tk.StringVar()
            ttk.Label(self.frame, textvariable=self.key_notice).pack(anchor="w")
        else:
            ttk.Label(self.frame, text="首次安装自动创建本机监听配置；使用前请在 config.yaml 配置上游账号。", wraplength=800).pack(anchor="w", pady=(10, 0))
        ttk.Label(self.frame, textvariable=self.saved).pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=8)
        self.check = ttk.Button(actions, text="检查最新版", command=lambda: self.run("check"))
        self.install = ttk.Button(actions, text="安装 / 升级", command=lambda: self.run("install"), style="Primary.TButton")
        self.check.pack(side="left")
        self.install.pack(side="left", padx=10)
        self.clear_backup_button = ttk.Button(actions, text="清空安装备份", command=self.clear_backups)
        self.clear_backup_button.pack(side="left", padx=(0, 10))
        self.update_indicator = ttk.Label(actions)
        self.update_indicator.pack(side="left")
        ttk.Label(self.frame, textvariable=self.status, wraplength=820).pack(anchor="w", pady=6)
        self.progress = ttk.Progressbar(self.frame, maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))
        self.log = scrolledtext.ScrolledText(self.frame, height=10, state="disabled")
        style_log_widget(self.log)
        self.log.pack(fill="both", expand=True)
        self.directory.trace_add("write", self.folder_changed)
        self.button_states()
        self.poll()
        self.schedule_refresh()
        if not smoke:
            self.window.after(900 if key == "cli" else 1200, self.daily_check)

    def target(self):
        path = Path(self.directory.get().strip() or str(ROOT / self.name)).expanduser()
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

    def remote_label(self):
        label = "最新版本（缓存）" if self.remote_version and self.remote_cached else "最新版本"
        return f"{label}：{self.remote_version or '尚未检查'}"

    def refresh_stale_cache(self):
        if self.busy or self.smoke or getattr(self.app, "closing", False):
            return
        if has_update(self.remote_version, self.local_version):
            key = (str(self.target()).lower(), self.local_version, self.remote_version)
            if key not in self.stale_refresh_attempts:
                self.stale_refresh_attempts.add(key)
                self.run("check")

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self.window, initialdir=str(self.target()) if self.target().is_dir() else str(ROOT))
        if value:
            self.directory.set(value)

    def changed(self, *_):
        if self.save_timer:
            self.window.after_cancel(self.save_timer)
        self.saved.set("正在等待自动保存…")
        self.save_timer = self.window.after(500, lambda: self.persist(quiet=True))

    def folder_changed(self, *_):
        self.generation += 1
        self.running = None
        self.local_version = None
        self.admin_key.set("")
        self.remote_version = self.app.profiles[self.key].get("latest", {}).get(str(self.target()).lower())
        self.remote_cached = True
        self.versions.set(f"本地版本：正在检测…  {self.remote_label()}")
        self.service.set("正在检测…")
        self.changed()
        self.button_states()

    def update_badge(self):
        available = has_update(self.local_version, self.remote_version)
        image = self.app.update_dot if available else ""
        self.update_indicator.configure(image=image, text=" 有新版本" if available else "", compound="left", foreground="#d32f2f")
        if str(self.frame) in self.notebook.tabs():
            self.notebook.tab(self.frame, image=image, compound="right")

    def persist(self, quiet=False):
        if self.save_timer:
            self.window.after_cancel(self.save_timer)
            self.save_timer = None
        self.app.profiles[self.key].update(directory=self.directory.get())
        try:
            self.app.save()
            self.saved.set("配置已自动保存")
            return True
        except OSError as error:
            self.saved.set("自动保存失败")
            if not quiet:
                messagebox.showerror("保存失败", str(error), parent=self.window)
            return False

    def button_states(self):
        self.update_badge()
        installed = (self.target() / self.executable).is_file()
        available = has_update(self.local_version, self.remote_version)
        self.install.configure(text="升级" if installed else "安装最新版")
        if not installed:
            install_enabled = not self.busy
        else:
            install_enabled = available and not self.busy
        self.install.configure(state="normal" if install_enabled else "disabled")
        self.start.configure(state="normal" if installed and not self.busy and self.running is False else "disabled")
        for button in (self.stop, self.restart):
            button.configure(state="normal" if not self.busy and self.running is True else "disabled")
        for widget in (self.check, self.browse, self.folder_entry, self.clear_backup_button):
            widget.configure(state="disabled" if self.busy else "normal")
        if self.key == "plus":
            self.copy.configure(state="normal" if self.admin_key.get() else "disabled")
            self.import_key.configure(state="disabled" if self.busy else "normal")

    def copy_key(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.admin_key.get())
        self.status.set("登录 Key 已复制。")

    def enter_key(self):
        value = simpledialog.askstring("录入已有 Key", "填写已有登录 Key，不会重置服务凭据：", parent=self.window, show="*")
        if value:
            try:
                self.target().mkdir(parents=True, exist_ok=True)
                self.backend.save_admin_key(self.target(), value)
                self.admin_key.set(value.strip())
                self.button_states()
            except (OSError, ValueError) as error:
                messagebox.showerror("保存失败", str(error), parent=self.window)

    def snapshot(self, target):
        installed = (target / self.executable).is_file()
        if installed:
            servers = self.backend.running_servers(target)
            running, service = self.backend.server_status(target, servers)
            version = self.version_cache.read(target, self.executable, self.backend.local_version)
        else:
            running, service, version = False, "尚未安装，请点击“安装最新版”", None
        key, key_error = "", ""
        if self.key == "plus" and target.is_dir():
            try:
                key = self.backend.find_admin_key(target)
            except (OSError, ValueError):
                key_error = "登录 Key 无法读取，可重新录入；服务控制仍可正常使用。"
        return running, service, version, key, key_error

    def schedule_refresh(self):
        if not self.busy and not self.checking:
            self.checking = True
            generation, target = self.generation, self.target()
            def worker():
                try:
                    self.events.put(("snapshot", generation, self.snapshot(target)))
                except Exception:
                    self.events.put(("snapshot", generation, (None, "状态检测失败，将自动重试", None, "", "")))
                finally:
                    self.events.put(("checked", generation, None))
            threading.Thread(target=worker, daemon=True).start()
        self.window.after(3000, self.schedule_refresh)

    def daily_check(self):
        if self.busy:
            self.window.after(1000, self.daily_check)
            return
        today = time.strftime("%Y-%m-%d")
        if self.app.profiles[self.key]["checks"].get(str(self.target()).lower()) != today or not self.remote_version:
            self.run("check")
        else:
            self.refresh_stale_cache()

    def clear_backups(self):
        if self.busy or getattr(self.app, "closing", False):
            return
        target = self.target()
        try:
            count = len(backup_directories(target, "project"))
        except OSError as error:
            self.status.set("读取安装备份失败：" + str(error))
            return
        if not count:
            self.status.set("没有可清理的安装备份。")
            return
        if messagebox.askyesno("清空安装备份", f"删除此目录中的 {count} 个安装备份？\n{target}\n\n清空后无法使用它们恢复旧文件。", parent=self.window):
            self.run("clear_backups")

    def run(self, action):
        if self.busy or getattr(self.app, "closing", False) or not self.persist():
            return
        target = self.target()
        proxy = self.app.proxy_url()
        if action in ("check", "install"):
            try:
                if self.app.proxy_settings["enabled"] and not proxy:
                    raise ValueError("请填写代理地址。")
                self.backend.network(proxy)
            except ValueError as error:
                messagebox.showerror("代理设置", str(error), parent=self.window)
                return
        self.busy = True
        self.generation += 1
        generation = self.generation
        self.button_states()
        self.progress.configure(value=0)
        def worker():
            try:
                report = lambda value, text: self.events.put(("progress", value, text))
                if action in ("start", "stop", "restart"):
                    self.backend.control_server(action, report, root=target)
                elif action == "clear_backups":
                    clear_backups(target, "project", report)
                else:
                    if action == "install":
                        target.mkdir(parents=True, exist_ok=True)
                    self.backend.update(proxy, report, action == "check", root=target,
                                        versions=lambda local, latest: self.events.put(
                                            ("versions", (generation, str(target).lower(), local), latest)))
                self.events.put(("snapshot", generation, self.snapshot(target)))
                self.events.put(("done", None, None))
            except Exception as error:
                try:
                    self.events.put(("snapshot", generation, self.snapshot(target)))
                except Exception:
                    pass
                text = self.backend.re.sub(r"(https?://)[^/@\s]+@", r"\1***@", str(error))
                self.events.put(("error", None, text))
        threading.Thread(target=worker, daemon=False).start()

    def poll(self):
        try:
            while True:
                kind, value, text = self.events.get_nowait()
                if kind == "checked":
                    self.checking = False
                    continue
                if kind == "snapshot":
                    if value == self.generation:
                        self.running, service, local, key, key_error = text
                        self.local_version = local
                        self.service.set(service)
                        if getattr(self, "service_label", None) is not None and self.service_label.winfo_exists():
                            if self.running is True:
                                self.service_label.configure(foreground="#16a34a")
                            elif self.running is False:
                                self.service_label.configure(foreground="#dc2626")
                            else:
                                self.service_label.configure(foreground="#64748b")
                        self.versions.set(f"本地版本：{local or '未安装或无法识别'}  {self.remote_label()}")
                        self.admin_key.set(key)
                        if self.key == "plus":
                            self.key_notice.set(key_error)
                        self.button_states()
                        self.refresh_stale_cache()
                    continue
                if kind == "versions":
                    generation, target_key, local = value
                    if text:
                        settings = self.app.profiles[self.key]
                        settings.setdefault("latest", {})[target_key] = text
                        settings["checks"][target_key] = time.strftime("%Y-%m-%d")
                        try:
                            self.app.save()
                        except OSError:
                            self.saved.set("最新版本缓存保存失败")
                    if generation == self.generation and target_key == str(self.target()).lower():
                        self.local_version = local
                        if text:
                            self.remote_version = text
                            self.remote_cached = False
                        remote = self.remote_label() if text else "最新版本：检查中…"
                        self.versions.set(f"本地版本：{local or '未安装或无法识别'}  {remote}")
                        self.update_badge()
                        self.button_states()
                    continue
                if kind == "progress":
                    self.status.set(text)
                    if value is None:
                        if str(self.progress["mode"]) != "indeterminate":
                            self.progress.configure(mode="indeterminate")
                            self.progress.start(15)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=value)
                    if text.startswith("正在下载"):
                        if time.monotonic() - self.last_download_log < 2:
                            continue
                        self.last_download_log = time.monotonic()
                else:
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.button_states()
                    if kind == "error":
                        self.status.set("操作失败，请查看日志。")
                        messagebox.showerror(self.name + " 操作失败", text, parent=self.window)
                if text:
                    self.log.configure(state="normal")
                    self.log.insert("end", time.strftime("%H:%M:%S ") + text + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.window.after(80, self.poll)
