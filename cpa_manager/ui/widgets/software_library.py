"""Searchable bundled library for adding GitHub software to the installer list."""
import tkinter as tk
from tkinter import ttk
import webbrowser
import queue
import threading

from cpa_manager.backends.github import repository, search_repositories, SEARCH_INTERVAL
from cpa_manager.core.software_catalog import catalog_entries, filter_entries, GROUPS
from cpa_manager.ui.widgets.dialog_position import center_dialog
from cpa_manager.ui.widgets.frozen_actions import FrozenActions


class SoftwareLibrary:
    def __init__(self, owner, add, contains, allowed, proxy=lambda: "", portable=False):
        self.add, self.contains, self.allowed = add, contains, allowed
        self.target_name = "免安装软件" if portable else "安装向导软件"
        self.proxy = proxy
        self.remote_entries, self.remote_total = [], 0
        self.search_cache = {}
        self.search_events = queue.Queue()
        self.search_token = 0
        self.search_timer = self.poll_timer = None
        self.search_running = False
        self.pending_search = None
        self.entries = catalog_entries()
        self.by_id = {entry["id"]: entry for entry in self.entries}
        self.window = tk.Toplevel(owner)
        self.window.withdraw()
        self.window.title("软件库")
        self.window.transient(owner)
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(body)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="分类：").pack(side="left")
        self.category = tk.StringVar(value="全部")
        combo = ttk.Combobox(toolbar, textvariable=self.category, values=["全部", *GROUPS], state="readonly", width=16)
        combo.pack(side="left")
        self.category_selector = combo
        ttk.Label(toolbar, text="搜索：").pack(side="left", padx=(10, 0))
        self.github_search = tk.BooleanVar(value=False)
        ttk.Button(toolbar, text="搜索", command=lambda: self.search_changed(immediate=True)).pack(side="right")
        ttk.Checkbutton(toolbar, text="搜索 GitHub", variable=self.github_search, command=self.search_changed).pack(side="right", padx=(8, 8))
        self.query = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda _: self.search_changed(immediate=True))
        description = ("添加时选择安装目录，自动保存到免安装软件列表；点击检查获取附件。需要项目提供适用的 ZIP / 单文件 EXE 便携包。"
                       if portable else "添加后会保存到安装向导软件列表；点击检查获取附件。能否安装取决于项目是否提供 Windows EXE / MSI 安装包。")
        ttk.Label(body, text=description,
                  wraplength=980).grid(row=1, column=0, sticky="w", pady=(0, 8))
        area = ttk.Frame(body)
        area.grid(row=2, column=0, sticky="nsew")
        self.table = ttk.Treeview(area, columns=("name", "description", "repository"), show="headings", selectmode="browse", height=15)
        for key, label, width, stretch in (("name", "软件名称", 180, False), ("description", "说明", 300, False), ("repository", "地址", 450, True)):
            self.table.heading(key, text=label, anchor="center")
            self.table.column(key, width=width, minwidth=100, anchor="center", stretch=stretch)
        self.actions = FrozenActions(area, self.table, (("add", "添加", 76),),
                                     self.invoke, allowed, lambda: None,
                                     visible=lambda row, _: self.addable(self.by_id[row]) and not contains(self.by_id[row]["repository"]))
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.actions.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.table.xview)
        self.actions.scrollbar = vertical
        self.table.configure(xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        self.actions.tree.grid(row=0, column=1, sticky="ns")
        vertical.grid(row=0, column=2, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.status = tk.StringVar()
        ttk.Label(body, textvariable=self.status, wraplength=980).grid(row=3, column=0, sticky="w", pady=8)
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="ew")
        self.add_button = ttk.Button(buttons, text="添加所选软件", command=self.add_selected, style="Primary.TButton")
        self.add_button.pack(side="left")
        ttk.Button(buttons, text="打开项目页面", command=self.open_repository).pack(side="left", padx=8)
        ttk.Button(buttons, text="关闭", command=self.window.destroy).pack(side="right")
        self.table.tag_configure("added", foreground="#64748b")
        self.table.tag_configure("unavailable", foreground="#94a3b8")
        self.table.bind("<<TreeviewSelect>>", self.selected, add="+")
        combo.bind("<<ComboboxSelected>>", lambda _: self.populate())
        self.query.trace_add("write", self.search_changed)
        self.window.bind("<Escape>", lambda _: self.window.destroy())
        self.window.bind("<Destroy>", self.destroyed, add="+")
        self.populate()
        self.poll_timer = self.window.after(100, self.poll_search)
        center_dialog(self.window, owner, (min(1080, owner.winfo_screenwidth() - 64), min(620, owner.winfo_screenheight() - 96)))
        self.window.deiconify()
        self.window.lift(owner)
        self.window.grab_set()
        entry.focus_set()

    @staticmethod
    def addable(entry):
        try:
            repository(entry["repository"])
            return True
        except ValueError:
            return False

    def populate(self):
        selected = self.table.selection()
        for row in self.table.get_children():
            self.table.delete(row)
            self.actions.remove(row)
        entries = self.remote_entries if self.github_search.get() else filter_entries(self.entries, self.category.get(), self.query.get())
        self.by_id = {entry["id"]: entry for entry in self.entries + self.remote_entries}
        for entry in entries:
            added = self.addable(entry) and self.contains(entry["repository"])
            tag = "added" if added else "unavailable" if not self.addable(entry) else ""
            self.table.insert("", "end", iid=entry["id"],
                              values=(entry["name"] + ("（已添加）" if added else ""), entry["description"], entry["repository"] or "未提供"),
                              tags=(tag,) if tag else ())
            self.actions.update(entry["id"])
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        if self.github_search.get():
            self.status.set(f"GitHub 搜索找到 {self.remote_total} 个项目，显示前 {len(entries)} 个；可细化关键词，已添加的项目不会重复加入。" if self.query.get().strip() else "请输入关键词搜索 GitHub 项目。")
        else:
            self.status.set(f"显示 {len(entries)} / {len(self.entries)} 款软件；已添加的软件不会重复添加。")
        self.selected()

    def search_changed(self, *_, immediate=False):
        self.search_token += 1
        self.pending_search = None
        if self.search_timer is not None:
            self.window.after_cancel(self.search_timer)
            self.search_timer = None
        self.category_selector.configure(state="disabled" if self.github_search.get() else "readonly")
        query = self.query.get().strip()
        self.remote_entries, self.remote_total = [], 0
        if not self.github_search.get() or not query:
            self.populate()
            return
        if query in self.search_cache and not immediate:
            self.remote_entries, self.remote_total = self.search_cache[query]
            self.populate()
            return
        self.populate()
        self.status.set("正在搜索 GitHub 项目…")
        token = self.search_token
        if immediate:
            self.start_search(query, token)
        else:
            self.search_timer = self.window.after(int(SEARCH_INTERVAL * 1000), lambda: self.start_search(query, token))

    def start_search(self, query, token):
        self.search_timer = None
        if token != self.search_token:
            return
        if self.search_running:
            self.pending_search = (query, token)
            return
        self.search_running = True
        events, proxy = self.search_events, self.proxy()
        def worker():
            try:
                events.put((token, query, search_repositories(query, proxy), None))
            except Exception as error:
                events.put((token, query, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_search(self):
        self.poll_timer = None
        try:
            while True:
                token, query, result, error = self.search_events.get_nowait()
                self.search_running = False
                if token == self.search_token and self.github_search.get():
                    if error:
                        self.status.set("GitHub 搜索失败：" + error)
                    else:
                        self.search_cache[query] = result
                        self.remote_entries, self.remote_total = result
                        self.populate()
                if self.pending_search:
                    pending = self.pending_search
                    self.pending_search = None
                    self.start_search(*pending)
        except queue.Empty:
            pass
        self.poll_timer = self.window.after(100, self.poll_search)

    def destroyed(self, event):
        if event.widget is self.window:
            self.search_token += 1
            for timer in (self.search_timer, self.poll_timer):
                if timer is not None:
                    self.window.after_cancel(timer)
            self.search_timer = self.poll_timer = None

    def selected(self, *_):
        selected = self.table.selection()
        entry = self.by_id.get(selected[0]) if selected else None
        enabled = entry and self.addable(entry) and not self.contains(entry["repository"]) and self.allowed()
        self.add_button.configure(state="normal" if enabled else "disabled")
        if entry and not self.addable(entry):
            self.status.set(entry["name"] + "：没有可用的 GitHub 仓库地址，可打开项目页面查看。")

    def invoke(self, row, action):
        if action != "add" or not self.allowed():
            return
        entry = self.by_id[row]
        if not self.addable(entry) or self.contains(entry["repository"]):
            return
        if self.add(entry):
            self.populate()
            self.status.set(entry["name"] + "已添加到" + self.target_name + "列表，点击检查获取附件。")
        else:
            self.status.set("添加未成功，请查看主窗口提示后重试。")

    def add_selected(self):
        selected = self.table.selection()
        if selected:
            self.invoke(selected[0], "add")

    def open_repository(self):
        selected = self.table.selection()
        if selected:
            address = self.by_id[selected[0]]["repository"]
            if address:
                webbrowser.open(address)
