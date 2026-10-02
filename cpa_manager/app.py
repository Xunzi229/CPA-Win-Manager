"""Tabbed Windows manager for CLIProxyAPI and CPA-Manager-Plus."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
from cpa_manager.backends import cli as cli_backend
from cpa_manager.backends import manager_update
from cpa_manager.ui.pages.installer import InstallerPage
from cpa_manager.ui.pages.portable import PortablePage
from cpa_manager.backends import installer as installer_backend
from cpa_manager.backends.release_cache import ReleaseCache
from cpa_manager.core.version import current_version
from cpa_manager.core.single_instance import SingleInstance, focus_existing_window
from cpa_manager.core.runtime import monitor_work_area, default_download_directory
from cpa_manager.core.window_state import valid_window_state, restore_window_state
from cpa_manager.core.settings_store import read_settings, write_settings, SETTINGS_FILENAME, LEGACY_SETTINGS_FILENAME

from cpa_manager.config import PROJECTS, default_profiles, shared_proxy_settings, has_update
from cpa_manager.core.paths import ROOT, RESOURCE_ROOT
from cpa_manager.ui.pages.project import ProjectPage


class App:
    def __init__(self, smoke_report=None):
        self.window = tk.Tk()
        self.window.withdraw()
        icon = RESOURCE_ROOT / "assets" / "app-icon.ico"
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
        self.settings_file = ROOT / SETTINGS_FILENAME
        self.release_cache = ReleaseCache(ROOT / ".release-cache")
        try:
            saved = read_settings(self.settings_file, migrate=not smoke_report,
                                  legacy_path=ROOT / LEGACY_SETTINGS_FILENAME)
        except OSError as error:
            messagebox.showerror("配置读取失败", str(error), parent=self.window)
            self.window.destroy()
            raise
        try:
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
        self.window_preferences = saved.get("window") if isinstance(saved, dict) else None
        self.window_save_timer = None
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
        legacy_installers = [p for p in self.custom_profiles if p.get("mode") == "安装器"]
        installer_saved = saved.get("installer_software", []) if isinstance(saved, dict) else []
        self.installer_profiles = installer_backend.load_profiles(installer_saved, legacy_installers)
        migrated = {installer_backend.repo_key(p["repository"]) for p in self.installer_profiles}
        self.custom_profiles = [p for p in self.custom_profiles if not (
            p.get("mode") == "安装器" and p.get("repository") and
            self._installer_migrated(p["repository"], migrated))]
        self.pending_legacy_installers = [p for p in self.custom_profiles if p.get("mode") == "安装器"]
        self.custom_profiles = [p for p in self.custom_profiles if p.get("mode") != "安装器"]
        for profile in self.custom_profiles:
            profile.pop("pattern", None)
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
        custom_page = PortablePage(self, notebook, ROOT)
        notebook.add(custom_page.frame, text="  免安装软件  ")
        self.pages.append(custom_page)
        installers = InstallerPage(self, notebook)
        notebook.add(installers.frame, text="  安装向导软件  ")
        self.pages.append(installers)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.update_idletasks()
        pointer_x, pointer_y = self.window.winfo_pointerxy()
        if valid_window_state(self.window_preferences):
            pointer_x = self.window_preferences["x"] + self.window_preferences["width"] // 2
            pointer_y = self.window_preferences["y"] + self.window_preferences["height"] // 2
        left, top, right, bottom = monitor_work_area(pointer_x, pointer_y,
            (0, 0, self.window.winfo_screenwidth(), self.window.winfo_screenheight()))
        self.window_preferences = restore_window_state(self.window_preferences, (left, top, right, bottom))
        width, height = self.window_preferences["width"], self.window_preferences["height"]
        self.window.minsize(min(800, width), min(730, height))
        x, y = self.window_preferences["x"], self.window_preferences["y"]
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        if not smoke_report:
            self.window.deiconify()
            if self.window_preferences["maximized"]:
                self.window.state("zoomed")
            self.window.bind("<Configure>", self.window_changed, add="+")
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

    @staticmethod
    def _installer_migrated(repository, migrated):
        try:
            return installer_backend.repo_key(repository) in migrated
        except ValueError:
            return False

    def capture_window_state(self):
        state = self.window.state()
        if state == "normal":
            self.window_preferences = {"width": self.window.winfo_width(), "height": self.window.winfo_height(),
                                       "x": self.window.winfo_x(), "y": self.window.winfo_y(), "maximized": False}
        elif state == "zoomed":
            self.window_preferences["maximized"] = True

    def window_changed(self, event):
        if event.widget is not self.window:
            return
        self.capture_window_state()
        if self.window_save_timer is not None:
            self.window.after_cancel(self.window_save_timer)
        self.window_save_timer = self.window.after(600, self.save_window_state)

    def save_window_state(self):
        self.window_save_timer = None
        try:
            self.save()
        except OSError:
            self.manager_status.set("窗口位置保存失败，关闭时将重试。")

    def refresh_manager_controls(self):
        if self.manager_check is not None and self.manager_check.winfo_exists():
            self.manager_check.configure(state="disabled" if self.manager_busy else "normal")
        if self.manager_install is not None and self.manager_install.winfo_exists():
            available = self.manager_release and has_update(self.manager_version, self.manager_release[0])
            self.manager_install.configure(state="normal" if available and not self.manager_busy else "disabled")

    def check_manager_update(self):
        if self.manager_busy or getattr(self, "closing", False):
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
        if self.manager_busy or not self.manager_release or getattr(self, "closing", False):
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
        self.release_cache.flush()
        payload = json.dumps({**self.profiles, "proxy_settings": self.proxy_settings,
                              "custom_software": [{k: v for k, v in p.items() if k != "release_catalog"} for p in self.custom_profiles]
                                                 + getattr(self, "pending_legacy_installers", []),
                              "installer_software": self.installer_profiles,
                              "installer_download_directory": self.installer_download_directory,
                              "window": getattr(self, "window_preferences", None)}, ensure_ascii=False, indent=2)
        if payload == getattr(self, "_saved_payload", None):
            return
        write_settings(self.settings_file, payload)
        self._saved_payload = payload

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
        ttk.Label(body, text="所有安装向导软件共用此目录；免安装软件安装到各自已选目录。").pack(anchor="w", pady=(8, 4))
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
        if getattr(self, "_close_timer", None):
            self.window.after_cancel(self._close_timer)
            self._close_timer = None
        if self.manager_busy or any(page.busy for page in self.pages):
            if not getattr(self, "closing", False):
                self.closing = True
                for page in self.pages:
                    if hasattr(page, "request_stop"):
                        page.request_stop()
                self.manager_status.set("正在停止下载；文件写入完成后自动退出…")
            self._close_timer = self.window.after(100, self.close)
            return
        self.closing = False
        if getattr(self, "window_save_timer", None) is not None:
            self.window.after_cancel(self.window_save_timer)
            self.window_save_timer = None
        if hasattr(self, "capture_window_state"):
            self.capture_window_state()
        if self.proxy_dialog and self.proxy_dialog.winfo_exists() and not self.close_proxy_dialog():
            return
        for page in self.pages:
            if not page.persist():
                return
        self.window.destroy()

    def run(self):
        self.window.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-report", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_report:
        App(args.smoke_report).run()
    else:
        executable = Path(sys.executable if getattr(sys, "frozen", False) else ROOT / "manager.py")
        instance = SingleInstance(executable)
        try:
            if instance.is_first:
                App().run()
            else:
                focus_existing_window(executable)
        finally:
            instance.close()
