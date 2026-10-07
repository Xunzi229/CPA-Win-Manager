"""Tabbed Windows manager for CLIProxyAPI and CPA-Manager-Plus."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time
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

from cpa_manager.config import (
    PROJECTS, default_profiles, shared_proxy_settings, has_update,
    DEFAULT_DOWNLOAD_WORKERS, MIN_DOWNLOAD_WORKERS, MAX_DOWNLOAD_WORKERS, parse_download_workers,
    DEFAULT_TABLE_ROW_HEIGHT, DEFAULT_PORTABLE_ROWS, DEFAULT_INSTALLER_ROWS,
    MIN_ROW_HEIGHT, MAX_ROW_HEIGHT, parse_table_row_height, parse_table_rows,
)
from cpa_manager.core.paths import ROOT, RESOURCE_ROOT
from cpa_manager.ui.pages.project import ProjectPage
from cpa_manager.ui.theme import setup_theme
from cpa_manager.ui.widgets.dialog_position import center_dialog


class App:
    def __init__(self, smoke_report=None):
        self.window = tk.Tk()
        self.window.withdraw()
        setup_theme(self.window)
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
        self.include_prerelease = bool(saved.get("include_prerelease", False)) if isinstance(saved, dict) else False
        self.window_preferences = saved.get("window") if isinstance(saved, dict) else None
        self.window_save_timer = None
        saved_workers = saved.get("download_workers") if isinstance(saved, dict) else None
        try:
            self.download_workers = parse_download_workers(saved_workers)
        except ValueError:
            self.download_workers = DEFAULT_DOWNLOAD_WORKERS
        self.table_height_portable = parse_table_rows(saved.get("table_height_portable"), DEFAULT_PORTABLE_ROWS) if isinstance(saved, dict) else DEFAULT_PORTABLE_ROWS
        self.table_height_installer = parse_table_rows(saved.get("table_height_installer"), DEFAULT_INSTALLER_ROWS) if isinstance(saved, dict) else DEFAULT_INSTALLER_ROWS
        self.table_row_height = parse_table_row_height(saved.get("table_row_height"), DEFAULT_TABLE_ROW_HEIGHT) if isinstance(saved, dict) else DEFAULT_TABLE_ROW_HEIGHT
        style = ttk.Style(self.window)
        style.configure("FrozenRows.Treeview", rowheight=self.table_row_height)
        style.configure("FrozenActions.Treeview", rowheight=self.table_row_height)
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
        header = ttk.Frame(self.window, style="Window.TFrame")
        header.pack(fill="x", padx=20, pady=(14, 10))
        identity = ttk.Frame(header, style="Window.TFrame")
        identity.pack(side="left")
        ttk.Label(identity, text="CPA 统一管理器", font=("Microsoft YaHei UI", 16, "bold"), foreground="#0f172a", style="Window.TLabel").pack(anchor="w")
        self.manager_status = tk.StringVar(value="管理器版本：v" + self.manager_version)
        ttk.Label(identity, textvariable=self.manager_status, foreground="#64748b", style="Window.TLabel", wraplength=300).pack(anchor="w", pady=(3, 0))
        tools = ttk.Frame(header, style="Window.TFrame")
        tools.pack(side="right")
        proxy_tools = ttk.Frame(tools, style="Window.TFrame")
        proxy_tools.pack(side="left")
        ttk.Label(proxy_tools, textvariable=self.proxy_status, foreground="#64748b", style="Window.TLabel").pack(side="left", padx=(0, 10))
        self.proxy_button = ttk.Button(proxy_tools, text="设置", width=8, command=self.open_proxy_settings)
        self.proxy_button.pack(side="left")
        ttk.Separator(self.window).pack(fill="x", padx=16, pady=(0, 10))
        self.manager_events = queue.Queue()
        self.manager_busy = False
        self.manager_last_check = saved.get("manager_last_check") if isinstance(saved, dict) else None
        if not isinstance(self.manager_last_check, (int, float)):
            self.manager_last_check = None
        cached_release = saved.get("manager_release") if isinstance(saved, dict) else None
        if isinstance(cached_release, (list, tuple)) and len(cached_release) >= 3 and all(isinstance(x, str) for x in cached_release[:3]):
            self.manager_release = tuple(cached_release[:3])
        else:
            self.manager_release = None
        if self.manager_release and has_update(self.manager_version, self.manager_release[0]):
            self.manager_status.set("管理器版本：v" + self.manager_version + " · 可更新至 " + self.manager_release[0])
        self.manager_check = self.manager_install = None
        self.manager_progress = None
        self.update_actions = None
        self.manager_progress_value = 0
        self._manager_check_timer = None
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.pages = []
        for key in PROJECTS:
            page = ProjectPage(self, notebook, key, bool(smoke_report))
            notebook.add(page.frame, text=PROJECTS[key][0])
            page.update_badge()
            self.pages.append(page)
        custom_page = PortablePage(self, notebook, ROOT)
        notebook.add(custom_page.frame, text="免安装软件")
        self.custom_page = custom_page
        self.pages.append(custom_page)
        installers = InstallerPage(self, notebook)
        notebook.add(installers.frame, text="安装向导软件")
        self.installer_page = installers
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
            self.window.after(2000, self.auto_check_manager_update)
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
            self.manager_install.configure(text="正在更新…" if self.manager_busy else "更新管理器")
        bar = getattr(self, "manager_progress", None)
        if bar is not None and bar.winfo_exists():
            if self.manager_busy:
                if not bar.winfo_ismapped():
                    actions = getattr(self, "update_actions", None)
                    if actions is not None and actions.winfo_exists():
                        bar.pack(fill="x", pady=(0, 8), before=actions)
                    else:
                        bar.pack(fill="x", pady=(0, 8))
            else:
                if bar.winfo_ismapped():
                    bar.stop()
                    bar.pack_forget()
        self.set_manager_progress(getattr(self, "manager_progress_value", 0))

    def set_manager_progress(self, value):
        self.manager_progress_value = value
        bar = getattr(self, "manager_progress", None)
        if bar is not None and bar.winfo_exists():
            if value is None:
                if str(bar["mode"]) != "indeterminate":
                    bar.configure(mode="indeterminate")
                    bar.start(15)
            else:
                bar.stop()
                bar.configure(mode="determinate", value=value)

    MANAGER_CHECK_INTERVAL = 5 * 3600

    def manager_check_due(self, now=None):
        if now is None:
            now = time.time()
        last = getattr(self, "manager_last_check", None)
        if last is None:
            return True
        elapsed = now - last
        return elapsed >= self.MANAGER_CHECK_INTERVAL or elapsed < -86400

    def auto_check_manager_update(self):
        self._manager_check_timer = None
        if getattr(self, "closing", False) or self.manager_busy:
            return
        if self.manager_check_due():
            self.check_manager_update()
        else:
            self.schedule_next_manager_auto_check()

    def schedule_next_manager_auto_check(self):
        if getattr(self, "closing", False):
            return
        if getattr(self, "_manager_check_timer", None) is not None:
            try:
                self.window.after_cancel(self._manager_check_timer)
            except Exception:
                pass
            self._manager_check_timer = None
        now = time.time()
        last = getattr(self, "manager_last_check", None)
        if last is None or self.manager_check_due(now):
            delay_ms = 2000
        else:
            remaining = max(1, self.MANAGER_CHECK_INTERVAL - (now - last))
            delay_ms = int(min(remaining, 300) * 1000)
        self._manager_check_timer = self.window.after(delay_ms, self.auto_check_manager_update)

    def check_manager_update(self):
        if self.manager_busy or getattr(self, "closing", False):
            return
        if getattr(self, "_manager_check_timer", None) is not None:
            try:
                self.window.after_cancel(self._manager_check_timer)
            except Exception:
                pass
            self._manager_check_timer = None
        self.manager_busy = True
        self.refresh_manager_controls()
        self.set_manager_progress(None)
        self.manager_status.set("管理器 v" + self.manager_version + "：正在检查更新…")
        proxy = self.proxy_url()

        prerelease = getattr(self, "include_prerelease", False)

        def worker():
            try:
                self.manager_events.put(("release", manager_update.latest_release(proxy, include_prerelease=prerelease)))
            except Exception as error:
                self.manager_events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def install_manager_update(self):
        if self.manager_busy or not self.manager_release or getattr(self, "closing", False):
            return
        dialog_parent = self.proxy_dialog if self.proxy_dialog and self.proxy_dialog.winfo_exists() else self.window
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("源码运行", "源码运行不覆盖 Python 文件，请从发布页面下载新版管理器。", parent=dialog_parent)
            webbrowser.open(manager_update.REPOSITORY + "/releases/latest")
            return
        if any(page.busy or page.checking or getattr(page, "pending_downloads", False) for page in self.pages):
            messagebox.showinfo("操作进行中", "请等待项目操作完成后更新管理器。", parent=dialog_parent)
            return
        if not messagebox.askyesno("更新管理器", f"更新到 {self.manager_release[0]}？\n下载校验后管理器将自动关闭并重启。配置和两个项目的服务保持不变。\n\n是否立即开始异步下载并更新？", parent=dialog_parent):
            return
        self.manager_busy = True
        self.refresh_manager_controls()
        self.set_manager_progress(None)
        release, proxy = self.manager_release, self.proxy_url()

        def worker():
            try:
                stage = manager_update.prepare_update(ROOT, release, proxy,
                    lambda progress, text: self.manager_events.put(("progress", (progress, text))))
                self.manager_events.put(("ready", stage))
            except Exception as error:
                self.manager_events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_manager_update(self):
        try:
            while True:
                kind, value = self.manager_events.get_nowait()
                if kind == "progress":
                    if isinstance(value, tuple):
                        prog, text = value
                    else:
                        prog, text = None, value
                    self.manager_status.set(text)
                    self.set_manager_progress(prog)
                    continue
                self.manager_busy = False
                if kind == "release":
                    self.manager_release = value
                    self.manager_last_check = time.time()
                    available = has_update(self.manager_version, value[0])
                    self.manager_status.set("管理器版本：v" + self.manager_version + (" · 可更新至 " + value[0] if available else ""))
                    self.set_manager_progress(0)
                    try:
                        self.save()
                    except OSError:
                        pass
                    self.schedule_next_manager_auto_check()
                elif kind == "error":
                    self.manager_last_check = time.time()
                    self.manager_status.set("管理器更新失败：" + value)
                    self.set_manager_progress(0)
                    try:
                        self.save()
                    except OSError:
                        pass
                    self.schedule_next_manager_auto_check()
                elif kind == "ready":
                    self.set_manager_progress(100)
                    try:
                        if any(page.busy or page.checking or getattr(page, "pending_downloads", False) for page in self.pages):
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
                        self.set_manager_progress(0)
                self.refresh_manager_controls()
        except queue.Empty:
            pass
        self.window.after(100, self.poll_manager_update)

    def save(self):
        self.release_cache.flush()
        manager_release = getattr(self, "manager_release", None)
        payload = json.dumps({**self.profiles, "proxy_settings": self.proxy_settings,
                              "include_prerelease": getattr(self, "include_prerelease", False),
                              "download_workers": getattr(self, "download_workers", DEFAULT_DOWNLOAD_WORKERS),
                              "table_height_portable": getattr(self, "table_height_portable", DEFAULT_PORTABLE_ROWS),
                              "table_height_installer": getattr(self, "table_height_installer", DEFAULT_INSTALLER_ROWS),
                              "table_row_height": getattr(self, "table_row_height", DEFAULT_TABLE_ROW_HEIGHT),
                              "custom_software": [{k: v for k, v in p.items() if k != "release_catalog"} for p in self.custom_profiles]
                                                 + getattr(self, "pending_legacy_installers", []),
                              "installer_software": self.installer_profiles,
                              "installer_download_directory": self.installer_download_directory,
                              "window": getattr(self, "window_preferences", None),
                              "manager_last_check": getattr(self, "manager_last_check", None),
                              "manager_release": list(manager_release) if manager_release else None}, ensure_ascii=False, indent=2)
        if payload == getattr(self, "_saved_payload", None):
            return
        write_settings(self.settings_file, payload)
        self._saved_payload = payload

    def set_table_row_height(self, height, persist=True):
        self.table_row_height = parse_table_row_height(height, DEFAULT_TABLE_ROW_HEIGHT)
        try:
            style = ttk.Style(self.window)
            style.configure("FrozenRows.Treeview", rowheight=self.table_row_height)
            style.configure("FrozenActions.Treeview", rowheight=self.table_row_height)
        except Exception:
            pass
        if hasattr(self, "custom_page") and hasattr(self.custom_page, "row_actions"):
            self.custom_page.row_actions.schedule_render()
        if hasattr(self, "installer_page") and hasattr(self.installer_page, "row_actions"):
            self.installer_page.row_actions.schedule_render()
        if persist:
            try:
                self.save()
            except OSError:
                pass

    def set_table_height(self, page_name, rows):
        if page_name == "portable":
            self.table_height_portable = parse_table_rows(rows, DEFAULT_PORTABLE_ROWS)
        elif page_name == "installer":
            self.table_height_installer = parse_table_rows(rows, DEFAULT_INSTALLER_ROWS)
        try:
            self.save()
        except OSError:
            pass

    def proxy_url(self):
        return self.proxy_settings["url"].strip() if self.proxy_settings["enabled"] else ""

    def set_proxy(self, enabled, url):
        self.set_settings(enabled, url, getattr(self, "installer_download_directory", ""),
                          getattr(self, "download_workers", DEFAULT_DOWNLOAD_WORKERS))

    def set_settings(self, enabled, url, download_directory, download_workers=None, include_prerelease=None):
        url = url.strip()
        if enabled:
            if not url:
                raise ValueError("启用代理时请填写地址。")
            cli_backend.network(url)
        old = self.proxy_settings
        old_directory = getattr(self, "installer_download_directory", "")
        old_workers = getattr(self, "download_workers", DEFAULT_DOWNLOAD_WORKERS)
        old_prerelease = getattr(self, "include_prerelease", False)
        directory = Path(download_directory.strip() or default_download_directory()).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            raise ValueError("下载目录不能是文件，请选择文件夹。")
        workers = parse_download_workers(download_workers, default=old_workers)
        self.proxy_settings = {"enabled": bool(enabled), "url": url}
        self.installer_download_directory = str(directory)
        self.download_workers = workers
        if include_prerelease is not None:
            new_prerelease = bool(include_prerelease)
            if new_prerelease != old_prerelease:
                self.release_cache.clear()
                self.manager_release = None
                self.manager_last_check = None
                for page in getattr(self, "pages", []):
                    if hasattr(page, "row_catalogs"):
                        page.row_catalogs.clear()
            self.include_prerelease = new_prerelease
        try:
            self.save()
        except OSError:
            self.proxy_settings = old
            self.installer_download_directory = old_directory
            self.download_workers = old_workers
            self.include_prerelease = old_prerelease
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
        dialog.configure(bg="#f8fafc")
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
        ttk.Label(body, text="所有安装向导软件共用此目录；免安装软件安装到各自已选目录。", foreground="#64748b").pack(anchor="w", pady=(4, 10))
        ttk.Separator(body).pack(fill="x", pady=6)

        header_row = ttk.Frame(body)
        header_row.pack(fill="x", pady=(0, 6))
        ttk.Label(header_row, text="并发下载分块数").pack(side="left")
        ttk.Label(header_row, text=f"（范围 {MIN_DOWNLOAD_WORKERS} ~ {MAX_DOWNLOAD_WORKERS} · 自动限制）", foreground="#64748b").pack(side="left", padx=(4, 0))

        workers_frame = ttk.Frame(body)
        workers_frame.pack(fill="x", pady=(2, 4))
        workers_var = tk.StringVar(value=str(getattr(self, "download_workers", DEFAULT_DOWNLOAD_WORKERS)))

        def adjust_workers(delta):
            try:
                val = int(workers_var.get())
            except ValueError:
                val = getattr(self, "download_workers", DEFAULT_DOWNLOAD_WORKERS)
            val = max(MIN_DOWNLOAD_WORKERS, min(MAX_DOWNLOAD_WORKERS, val + delta))
            workers_var.set(str(val))

        stepper = ttk.Frame(workers_frame)
        stepper.pack(side="left")
        minus_btn = ttk.Button(stepper, text="−", style="Stepper.TButton", command=lambda: adjust_workers(-1))
        minus_btn.pack(side="left")
        workers_entry = ttk.Entry(stepper, textvariable=workers_var, width=4, justify="center", font=("Microsoft YaHei UI", 10, "bold"))
        workers_entry.pack(side="left", padx=4)
        plus_btn = ttk.Button(stepper, text="+", style="Stepper.TButton", command=lambda: adjust_workers(1))
        plus_btn.pack(side="left")
        ttk.Label(stepper, text="分块", foreground="#64748b").pack(side="left", padx=(6, 12))

        presets = ttk.Frame(workers_frame)
        presets.pack(side="left")
        preset_chips = []
        for label, val in [("1", 1), ("2", 2), ("4 (默认)", 4), ("8", 8), ("16 (最大)", 16)]:
            btn = ttk.Button(presets, text=label, style="Chip.TButton", command=lambda v=val: workers_var.set(str(v)))
            btn.pack(side="left", padx=2)
            preset_chips.append(btn)

        ttk.Label(body, text=f"每个大文件多线程分段加速下载；大于 {MAX_DOWNLOAD_WORKERS} 自动限制为 {MAX_DOWNLOAD_WORKERS}，小于 {MIN_DOWNLOAD_WORKERS} 自动限制为 {MIN_DOWNLOAD_WORKERS}。",
                  foreground="#64748b").pack(anchor="w", pady=(6, 8))
        prerelease_area = ttk.LabelFrame(body, text="预发版本设置", padding=12)
        prerelease_area.pack(fill="x", pady=(0, 12))
        prerelease_var = tk.BooleanVar(value=getattr(self, "include_prerelease", False))
        prerelease_check = ttk.Checkbutton(prerelease_area, text="包含预发布版本 (Pre-release)", variable=prerelease_var, command=lambda: changed())
        prerelease_check.pack(anchor="w", pady=(0, 2))
        ttk.Label(prerelease_area, text="勾选后在检查更新与拉取新版本时，将包含并优先升级到最新预发版本（支持所有软件及管理器）。",
                  foreground="#64748b").pack(anchor="w")

        update_area = ttk.LabelFrame(body, text="管理器更新", padding=12)
        update_area.pack(fill="x", pady=(0, 14))
        ttk.Label(update_area, textvariable=self.manager_status, wraplength=480).pack(anchor="w", pady=(0, 8))
        self.manager_progress = ttk.Progressbar(update_area, maximum=100)
        self.update_actions = ttk.Frame(update_area)
        self.update_actions.pack(fill="x")
        self.manager_check = ttk.Button(self.update_actions, text="检查更新",
            command=lambda: self.check_manager_update() if persist(quiet=False) else None)
        self.manager_check.pack(side="left")
        self.manager_install = ttk.Button(self.update_actions, text="更新管理器",
            command=lambda: self.install_manager_update() if persist(quiet=False) else None, style="Primary.TButton")
        self.manager_install.pack(side="left", padx=8)
        release_button = ttk.Button(self.update_actions, text="发布页面",
            command=lambda: webbrowser.open(manager_update.REPOSITORY + ("/releases" if getattr(self, "include_prerelease", False) else "/releases/latest")))
        release_button.pack(side="left")
        self.refresh_manager_controls()

        footer = ttk.Frame(body)
        footer.pack(fill="x", side="bottom")
        notice = tk.StringVar(value="已自动保存")
        notice_label = ttk.Label(footer, textvariable=notice, foreground="#16a34a", wraplength=360)
        notice_label.pack(side="left")
        timer = None

        def persist(quiet=True):
            nonlocal timer
            if timer:
                dialog.after_cancel(timer)
                timer = None
            try:
                self.set_settings(enabled.get(), address.get(), directory.get(), workers_var.get(), prerelease_var.get())
                if str(self.download_workers) != workers_var.get():
                    workers_var.set(str(self.download_workers))
                notice.set("已保存")
                notice_label.configure(foreground="#16a34a")
                return True
            except (OSError, ValueError) as error:
                notice.set(str(error))
                notice_label.configure(foreground="#ef4444")
                if not quiet:
                    if "下载分块数" in str(error):
                        workers_entry.focus_set()
                    else:
                        entry.focus_set()
                return False

        def changed(*_):
            nonlocal timer
            entry.configure(state="normal" if enabled.get() else "disabled")
            if timer:
                dialog.after_cancel(timer)
            notice.set("保存中…")
            notice_label.configure(foreground="#64748b")
            timer = dialog.after(500, persist)

        def close():
            if persist(quiet=False):
                if hasattr(dialog, "grab_release"):
                    try:
                        dialog.grab_release()
                    except Exception:
                        pass
                dialog.destroy()
                self.proxy_dialog = None
                self.manager_check = self.manager_install = None
                self.manager_progress = None
                self.update_actions = None
                if previous_focus and previous_focus.winfo_exists():
                    previous_focus.focus_set()
                return True
            return False

        enabled.trace_add("write", changed)
        address.trace_add("write", changed)
        directory.trace_add("write", changed)
        workers_var.trace_add("write", changed)
        entry.configure(state="normal" if enabled.get() else "disabled")
        done = ttk.Button(footer, text="完成", command=close, style="Primary.TButton")
        done.pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close)
        self.close_proxy_dialog = close
        focus_order = [toggle, entry, download_entry, browse, minus_btn, workers_entry, plus_btn] + preset_chips + [
            self.manager_check, self.manager_install, release_button, done
        ]

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
        center_dialog(dialog, self.window)
        dialog.deiconify()
        dialog.lift(self.window)
        (entry if enabled.get() else toggle).focus_set()

    def close(self):
        if getattr(self, "_close_timer", None):
            self.window.after_cancel(self._close_timer)
            self._close_timer = None
        if getattr(self, "_manager_check_timer", None) is not None:
            try:
                self.window.after_cancel(self._manager_check_timer)
            except Exception:
                pass
            self._manager_check_timer = None
        if self.manager_busy or any(page.busy or getattr(page, "checking", False) or getattr(page, "pending_downloads", False) for page in self.pages):
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
