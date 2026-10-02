"""Tabbed Windows manager for CLIProxyAPI and CPA-Manager-Plus."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import webbrowser
import uuid
import cli_backend
import plus_backend
import manager_update
from generic_page import GenericPage
from app_version import current_version
from single_instance import SingleInstance, focus_existing_window
from runtime_utils import VersionCache, monitor_work_area, default_download_directory

ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
PROJECTS = {
    "cli": ("CLIProxyAPI", "cli-proxy-api.exe", cli_backend),
    "plus": ("CPA-Manager-Plus", "cpa-manager-plus.exe", plus_backend),
}


def has_update(local, latest):
    local_key = cli_backend.version_key(local) if local else None
    latest_key = cli_backend.version_key(latest) if latest else None
    return local_key is not None and latest_key is not None and latest_key > local_key


def shared_proxy_settings(saved, profiles):
    shared = saved.get("proxy_settings") if isinstance(saved, dict) else None
    if isinstance(shared, dict) and type(shared.get("enabled")) is bool and isinstance(shared.get("url"), str):
        return {"enabled": shared["enabled"], "url": shared["url"]}
    # Migrate an enabled legacy proxy first; CLI wins when both had different proxies.
    candidates = [profiles[key] for key in PROJECTS]
    chosen = next((item for item in candidates if item.get("proxy_enabled") and item.get("proxy")), candidates[0])
    return {"enabled": bool(chosen.get("proxy_enabled", False)), "url": chosen.get("proxy", "http://127.0.0.1:7890")}


def default_profiles():
    profiles = {}
    for key, (name, executable, _) in PROJECTS.items():
        directory = ROOT / name
        proxy, enabled = "http://127.0.0.1:7890", False
        # Import settings from either existing standalone manager without changing it.
        prefix = "CLIProxyAPI*" if key == "cli" else "cpa-manager-plus*"
        for candidate in sorted(ROOT.parent.glob(prefix)):
            if not (candidate / executable).is_file():
                continue
            directory = candidate
            settings = candidate / "update" / "update-settings.json"
            try:
                old = json.loads(settings.read_text(encoding="utf-8"))
                directory = Path(old.get("server_dir") or candidate)
                proxy = old.get("proxy", proxy)
                enabled = old.get("proxy_enabled", False)
            except (OSError, ValueError, TypeError):
                pass
            break
        profiles[key] = {"directory": str(directory), "proxy": proxy, "proxy_enabled": enabled, "checks": {}, "latest": {}}
    return profiles


class App:
    def __init__(self, smoke_report=None):
        self.window = tk.Tk()
        self.window.withdraw()
        icon = Path(getattr(sys, "_MEIPASS", ROOT)) / "assets" / "app-icon.ico"
        if icon.is_file():
            self.window.iconbitmap(default=str(icon))
        self.manager_version = current_version()
        self.window.title("CPA 统一管理器 v" + self.manager_version)
        self.window.geometry("940x820")
        self.window.minsize(800, 730)
        self.update_dot = tk.PhotoImage(master=self.window, width=12, height=12)
        for y in range(12):
            for x in range(12):
                if (x - 5.5) ** 2 + (y - 5.5) ** 2 <= 22:
                    self.update_dot.put("#e53935", (x, y))
        self.profiles = default_profiles()
        self.settings_file = ROOT / "manager-settings.json"
        saved = {}
        try:
            saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
            for key in PROJECTS:
                value = saved.get(key, {})
                if (isinstance(value, dict) and isinstance(value.get("directory"), str)
                        and isinstance(value.get("checks", {}), dict)):
                    self.profiles[key].update(value)
                if not isinstance(self.profiles[key].get("latest"), dict):
                    self.profiles[key]["latest"] = {}
        except (OSError, ValueError, AttributeError):
            pass
        self.proxy_settings = shared_proxy_settings(saved, self.profiles)
        custom = saved.get("custom_software", []) if isinstance(saved, dict) else []
        self.custom_profiles = [p for p in custom if isinstance(p, dict)
                                and isinstance(p.get("name"), str)
                                and all(isinstance(p.get(k, ""), str) for k in
                                        ("repository", "directory", "mode", "preserve"))] if isinstance(custom, list) else []
        shared_directory = saved.get("installer_download_directory") if isinstance(saved, dict) else None
        legacy_directory = next((p["directory"] for p in self.custom_profiles
                                 if p.get("mode") == "安装器" and p.get("directory")), str(default_download_directory()))
        self.installer_download_directory = (shared_directory if isinstance(shared_directory, str)
                                            and shared_directory.strip() else legacy_directory)
        for profile in self.custom_profiles:
            profile.pop("pattern", None)
            if profile.get("mode") == "安装器":
                profile.pop("directory", None)
        for profile in self.profiles.values():
            profile.pop("proxy", None)
            profile.pop("proxy_enabled", None)
        self.proxy_dialog = None
        self.proxy_status = tk.StringVar(value="代理：已启用" if self.proxy_settings["enabled"] else "代理：直连")
        header = ttk.Frame(self.window)
        header.pack(fill="x", padx=20, pady=(12, 12))
        identity = ttk.Frame(header)
        identity.pack(side="left")
        ttk.Label(identity, text="CPA 统一管理器", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        self.manager_status = tk.StringVar(value="管理器版本：v" + self.manager_version)
        ttk.Label(identity, textvariable=self.manager_status, foreground="#666666", wraplength=220).pack(anchor="w", pady=(3, 0))
        tools = ttk.Frame(header)
        tools.pack(side="right")
        proxy_tools = ttk.Frame(tools)
        proxy_tools.pack(side="left")
        ttk.Label(proxy_tools, textvariable=self.proxy_status, foreground="#666666").pack(side="left", padx=(0, 8))
        self.proxy_button = ttk.Button(proxy_tools, text="设置", width=9, command=self.open_proxy_settings)
        self.proxy_button.pack(side="left")
        self.manager_events = queue.Queue()
        self.manager_busy = False
        self.manager_release = None
        self.manager_check = self.manager_install = None
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.pages = []
        for key in PROJECTS:
            page = ProjectPage(self, notebook, key, bool(smoke_report))
            notebook.add(page.frame, text="  " + PROJECTS[key][0] + "  ")
            page.update_badge()
            self.pages.append(page)
        custom_page = GenericPage(self, notebook, ROOT)
        notebook.add(custom_page.frame, text="  通用安装  ")
        self.pages.append(custom_page)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.update_idletasks()
        pointer_x, pointer_y = self.window.winfo_pointerxy()
        left, top, right, bottom = monitor_work_area(pointer_x, pointer_y,
            (0, 0, self.window.winfo_screenwidth(), self.window.winfo_screenheight()))
        width = min(940, max(1, right - left - 32))
        height = min(820, max(1, bottom - top - 56))
        self.window.minsize(min(800, width), min(730, height))
        x = left + (right - left - width - 16) // 2
        y = top + (bottom - top - height - 40) // 2
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        if not smoke_report:
            self.window.deiconify()
            self.window.after(2000, self.check_manager_update)
        self.window.after(100, self.poll_manager_update)
        if smoke_report:
            self.window.withdraw()
            def finish():
                Path(smoke_report).write_text(json.dumps({
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "tabs": [notebook.tab(tab, "text").strip() for tab in notebook.tabs()],
                    "directories": {page.key: str(page.target()) for page in self.pages},
                    "key_page": "plus", "root": str(ROOT),
                    "manager_version": self.manager_version,
                }, ensure_ascii=False), encoding="utf-8")
                self.window.destroy()
            self.window.after(1200, finish)

    def refresh_manager_controls(self):
        if self.manager_check is not None and self.manager_check.winfo_exists():
            self.manager_check.configure(state="disabled" if self.manager_busy else "normal")
        if self.manager_install is not None and self.manager_install.winfo_exists():
            available = self.manager_release and has_update(self.manager_version, self.manager_release[0])
            self.manager_install.configure(state="normal" if available and not self.manager_busy else "disabled")

    def check_manager_update(self):
        if self.manager_busy:
            return
        self.manager_busy = True
        self.refresh_manager_controls()
        self.manager_status.set("管理器 v" + self.manager_version + "：正在检查更新…")
        proxy = self.proxy_url()

        def worker():
            try:
                self.manager_events.put(("release", manager_update.latest_release(proxy)))
            except Exception as error:
                self.manager_events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def install_manager_update(self):
        if self.manager_busy or not self.manager_release:
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("源码运行", "源码运行不覆盖 Python 文件，请从发布页面下载新版管理器。", parent=self.window)
            webbrowser.open(manager_update.REPOSITORY + "/releases/latest")
            return
        if any(page.busy or page.checking for page in self.pages):
            messagebox.showinfo("操作进行中", "请等待项目操作完成后更新管理器。", parent=self.window)
            return
        if not messagebox.askyesno("更新管理器", f"更新到 {self.manager_release[0]}？\n下载校验后管理器将自动关闭并重启。配置和两个项目的服务保持不变。", parent=self.window):
            return
        self.manager_busy = True
        self.refresh_manager_controls()
        release, proxy = self.manager_release, self.proxy_url()

        def worker():
            try:
                stage = manager_update.prepare_update(ROOT, release, proxy,
                    lambda _, text: self.manager_events.put(("progress", text)))
                self.manager_events.put(("ready", stage))
            except Exception as error:
                self.manager_events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_manager_update(self):
        try:
            while True:
                kind, value = self.manager_events.get_nowait()
                if kind == "progress":
                    self.manager_status.set(value)
                    continue
                self.manager_busy = False
                if kind == "release":
                    self.manager_release = value
                    available = has_update(self.manager_version, value[0])
                    self.manager_status.set("管理器版本：v" + self.manager_version + (" · 可更新至 " + value[0] if available else ""))
                elif kind == "error":
                    self.manager_status.set("管理器更新失败：" + value)
                elif kind == "ready":
                    try:
                        if any(page.busy or page.checking for page in self.pages):
                            raise RuntimeError("项目操作正在进行，请完成后重新更新管理器。")
                        if self.proxy_dialog and self.proxy_dialog.winfo_exists() and not self.close_proxy_dialog():
                            raise RuntimeError("设置尚未保存，请处理后重试。")
                        if not all(page.persist() for page in self.pages):
                            raise RuntimeError("设置保存失败，已取消更新。")
                        manager_update.launch_update(sys.executable, value)
                        self.window.destroy()
                        return
                    except Exception as error:
                        self.manager_status.set("管理器更新失败：" + str(error))
                self.refresh_manager_controls()
        except queue.Empty:
            pass
        self.window.after(100, self.poll_manager_update)

    def save(self):
        temporary = self.settings_file.with_name(".manager-settings-" + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({**self.profiles, "proxy_settings": self.proxy_settings,
                                            "custom_software": self.custom_profiles,
                                            "installer_download_directory": self.installer_download_directory}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.settings_file)
        finally:
            temporary.unlink(missing_ok=True)

    def proxy_url(self):
        return self.proxy_settings["url"].strip() if self.proxy_settings["enabled"] else ""

    def set_proxy(self, enabled, url):
        self.set_settings(enabled, url, self.installer_download_directory)

    def set_settings(self, enabled, url, download_directory):
        url = url.strip()
        if enabled:
            if not url:
                raise ValueError("启用代理时请填写地址。")
            cli_backend.network(url)
        old = self.proxy_settings
        old_directory = self.installer_download_directory
        directory = Path(download_directory.strip() or default_download_directory()).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            raise ValueError("下载目录不能是文件，请选择文件夹。")
        self.proxy_settings = {"enabled": bool(enabled), "url": url}
        self.installer_download_directory = str(directory)
        try:
            self.save()
        except OSError:
            self.proxy_settings = old
            self.installer_download_directory = old_directory
            raise
        self.proxy_status.set("代理：已启用" if enabled else "代理：直连")

    def open_proxy_settings(self):
        if self.proxy_dialog and self.proxy_dialog.winfo_exists():
            self.proxy_dialog.lift()
            return
        previous_focus = self.window.focus_get()
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title("设置")
        dialog.transient(self.window)
        dialog.resizable(False, False)
        self.proxy_dialog = dialog
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        enabled = tk.BooleanVar(value=self.proxy_settings["enabled"])
        address = tk.StringVar(value=self.proxy_settings["url"])
        toggle = ttk.Checkbutton(body, text="启用代理", variable=enabled)
        toggle.pack(anchor="w", pady=(0, 6))
        entry = ttk.Entry(body, textvariable=address, width=58)
        entry.pack(fill="x")
        ttk.Label(body, text="HTTP/HTTPS · 例如 http://127.0.0.1:7890").pack(anchor="w", pady=(8, 4))
        ttk.Separator(body).pack(fill="x", pady=12)
        ttk.Label(body, text="统一安装器下载目录").pack(anchor="w", pady=(0, 6))
        directory = tk.StringVar(value=self.installer_download_directory)
        download_row = ttk.Frame(body)
        download_row.pack(fill="x")
        download_entry = ttk.Entry(download_row, textvariable=directory)
        download_entry.pack(side="left", fill="x", expand=True)

        def choose_directory():
            value = filedialog.askdirectory(parent=dialog, title="选择统一安装器下载目录")
            if value:
                directory.set(value)

        browse = ttk.Button(download_row, text="选择目录", command=choose_directory)
        browse.pack(side="left", padx=(8, 0))
        ttk.Label(body, text="所有安装器软件共用此目录；便携软件安装到各自已选目录。").pack(anchor="w", pady=(8, 4))
        notice = tk.StringVar(value="自动保存")
        ttk.Label(body, textvariable=notice, wraplength=520).pack(anchor="w", pady=(10, 8))
        timer = None

        def persist(quiet=True):
            nonlocal timer
            if timer:
                dialog.after_cancel(timer)
                timer = None
            try:
                self.set_settings(enabled.get(), address.get(), directory.get())
                notice.set("已保存")
                return True
            except (OSError, ValueError) as error:
                notice.set(str(error))
                if not quiet:
                    entry.focus_set()
                return False

        def changed(*_):
            nonlocal timer
            entry.configure(state="normal" if enabled.get() else "disabled")
            if timer:
                dialog.after_cancel(timer)
            notice.set("保存中…")
            timer = dialog.after(500, persist)

        update_area = ttk.LabelFrame(body, text="管理器更新", padding=12)
        update_area.pack(fill="x", pady=(8, 12))
        ttk.Label(update_area, textvariable=self.manager_status, wraplength=480).pack(anchor="w", pady=(0, 8))
        update_actions = ttk.Frame(update_area)
        update_actions.pack(fill="x")
        self.manager_check = ttk.Button(update_actions, text="检查更新",
            command=lambda: self.check_manager_update() if persist(quiet=False) else None)
        self.manager_check.pack(side="left")
        self.manager_install = ttk.Button(update_actions, text="更新管理器",
            command=lambda: self.install_manager_update() if persist(quiet=False) else None)
        self.manager_install.pack(side="left", padx=8)
        release_button = ttk.Button(update_actions, text="发布页面",
            command=lambda: webbrowser.open(manager_update.REPOSITORY + "/releases/latest"))
        release_button.pack(side="left")
        self.refresh_manager_controls()

        def close():
            if persist(quiet=False):
                dialog.grab_release()
                dialog.destroy()
                self.proxy_dialog = None
                self.manager_check = self.manager_install = None
                if previous_focus and previous_focus.winfo_exists():
                    previous_focus.focus_set()
                return True
            return False

        enabled.trace_add("write", changed)
        address.trace_add("write", changed)
        directory.trace_add("write", changed)
        entry.configure(state="normal" if enabled.get() else "disabled")
        done = ttk.Button(body, text="完成", command=close)
        done.pack(anchor="e")
        dialog.protocol("WM_DELETE_WINDOW", close)
        self.close_proxy_dialog = close
        focus_order = [toggle, entry, download_entry, browse, self.manager_check,
                       self.manager_install, release_button, done]

        def cycle_focus(event, direction):
            active = [widget for widget in focus_order if not widget.instate(["disabled"])]
            index = active.index(event.widget) if event.widget in active else 0
            active[(index + direction) % len(active)].focus_set()
            return "break"

        def escape(event):
            close()
            return "break"

        for widget in focus_order:
            widget.bind("<Tab>", lambda event: cycle_focus(event, 1))
            widget.bind("<Shift-Tab>", lambda event: cycle_focus(event, -1))
            widget.bind("<Escape>", escape)
        dialog.update_idletasks()
        width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        center_x = self.window.winfo_rootx() + self.window.winfo_width() // 2
        center_y = self.window.winfo_rooty() + self.window.winfo_height() // 2
        left, top, right, bottom = monitor_work_area(center_x, center_y,
            (0, 0, self.window.winfo_screenwidth(), self.window.winfo_screenheight()))
        # Center the decorated modal over its owner, while keeping it on-screen.
        outer_width, outer_height = width + 16, height + 40
        x = max(left + 8, min(center_x - outer_width // 2, right - outer_width - 8))
        y = max(top + 8, min(center_y - outer_height // 2, bottom - outer_height - 8))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()
        dialog.lift(self.window)
        dialog.grab_set()
        (entry if enabled.get() else toggle).focus_set()

    def close(self):
        if self.manager_busy or any(page.busy for page in self.pages):
            messagebox.showinfo("操作进行中", "请等待各页面中的操作完成后关闭。", parent=self.window)
            return
        if self.proxy_dialog and self.proxy_dialog.winfo_exists() and not self.close_proxy_dialog():
            return
        for page in self.pages:
            if not page.persist():
                return
        self.window.destroy()

    def run(self):
        self.window.mainloop()


class ProjectPage:
    def __init__(self, app, notebook, key, smoke):
        self.app, self.window, self.key = app, app.window, key
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
        settings = app.profiles[key]
        self.directory = tk.StringVar(value=settings["directory"])
        self.remote_version = settings.get("latest", {}).get(str(self.target()).lower())
        self.status = tk.StringVar(value="可检查版本，或在空目录安装最新版。")
        self.versions = tk.StringVar(value=f"本地版本：正在检测…  最新版本：{self.remote_version or '尚未检查'}")
        self.service = tk.StringVar(value="正在检测…")
        self.saved = tk.StringVar(value="目录设置自动保存")
        link = ttk.Label(self.frame, text="项目主页：" + self.backend.REPO, foreground="#0969da", cursor="hand2", takefocus=True)
        link.pack(anchor="w", pady=(0, 10))
        link.bind("<Button-1>", lambda _: webbrowser.open(self.backend.REPO))
        link.bind("<Return>", lambda _: webbrowser.open(self.backend.REPO))
        row = ttk.Frame(self.frame)
        row.pack(fill="x")
        ttk.Label(row, text="项目目录：").pack(side="left")
        self.folder_entry = ttk.Entry(row, textvariable=self.directory)
        self.folder_entry.pack(side="left", expand=True, fill="x")
        self.browse = ttk.Button(row, text="选择文件夹", command=self.choose_folder)
        self.browse.pack(side="left", padx=(8, 0))
        ttk.Label(self.frame, textvariable=self.versions).pack(anchor="w", pady=10)
        services = ttk.LabelFrame(self.frame, text="服务控制", padding=12)
        services.pack(fill="x")
        ttk.Label(services, textvariable=self.service, wraplength=380).pack(side="left", expand=True, fill="x")
        self.start = ttk.Button(services, text="启动", command=lambda: self.run("start"))
        self.stop = ttk.Button(services, text="停止", command=lambda: self.run("stop"))
        self.restart = ttk.Button(services, text="重启", command=lambda: self.run("restart"))
        for button in (self.start, self.stop, self.restart):
            button.pack(side="left", padx=(8, 0))
        self.admin_key = tk.StringVar()
        if key == "plus":
            area = ttk.LabelFrame(self.frame, text="管理员登录 Key", padding=10)
            area.pack(fill="x", pady=(10, 0))
            ttk.Entry(area, textvariable=self.admin_key, state="readonly").pack(side="left", fill="x", expand=True)
            self.copy = ttk.Button(area, text="复制 Key", command=self.copy_key)
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
        self.install = ttk.Button(actions, text="安装 / 更新最新版", command=lambda: self.run("install"))
        self.check.pack(side="left")
        self.install.pack(side="left", padx=10)
        self.update_indicator = ttk.Label(actions)
        self.update_indicator.pack(side="left")
        ttk.Label(self.frame, textvariable=self.status, wraplength=820).pack(anchor="w", pady=6)
        self.progress = ttk.Progressbar(self.frame, maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))
        self.log = scrolledtext.ScrolledText(self.frame, height=10, state="disabled", font=("Microsoft YaHei UI", 9))
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
        self.versions.set(f"本地版本：正在检测…  最新版本：{self.remote_version or '尚未检查'}")
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
        self.install.configure(text="更新最新版" if installed else "安装最新版")
        self.start.configure(state="normal" if installed and not self.busy and self.running is False else "disabled")
        for button in (self.stop, self.restart):
            button.configure(state="normal" if not self.busy and self.running is True else "disabled")
        for widget in (self.check, self.install, self.browse, self.folder_entry):
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

    def run(self, action):
        if self.busy or not self.persist():
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
                else:
                    if action == "install":
                        target.mkdir(parents=True, exist_ok=True)
                    self.backend.update(proxy, report, action == "check", root=target,
                                        versions=lambda local, latest: self.events.put(("versions", local, latest)))
                    self.events.put(("daily", None, str(target).lower()))
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
                        self.versions.set(f"本地版本：{local or '未安装或无法识别'}  最新版本：{self.remote_version or '尚未检查'}")
                        self.admin_key.set(key)
                        if self.key == "plus":
                            self.key_notice.set(key_error)
                        self.button_states()
                    continue
                if kind == "versions":
                    self.local_version = value
                    if text:
                        self.remote_version = text
                    self.versions.set(f"本地版本：{value or '未安装或无法识别'}  最新版本：{text or '检查中…'}")
                    self.update_badge()
                    continue
                if kind == "daily":
                    self.app.profiles[self.key]["checks"][text] = time.strftime("%Y-%m-%d")
                    if self.remote_version:
                        self.app.profiles[self.key].setdefault("latest", {})[text] = self.remote_version
                    try:
                        self.app.save()
                    except OSError:
                        self.saved.set("检查日期保存失败")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-report", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_report:
        App(args.smoke_report).run()
    else:
        executable = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        instance = SingleInstance(executable)
        try:
            if instance.is_first:
                App().run()
            else:
                focus_existing_window(executable)
        finally:
            instance.close()
