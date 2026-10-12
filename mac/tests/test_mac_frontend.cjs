const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function element(dataset = {}) {
  const classes = new Set(["hidden"]);
  const listeners = {};
  return {
    dataset, listeners, style: {}, value: "", checked: false, textContent: "", innerHTML: "", focus: () => {},
    classList: {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, hidden) => hidden ? classes.add(name) : classes.delete(name)
    },
    addEventListener: (name, callback) => {
      const previous = listeners[name];
      listeners[name] = previous ? async event => {
        await previous(event);
        await callback(event);
      } : callback;
    }
  };
}

async function loadFrontend(directory, native = true, fixtures = {}) {
  const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], element()]));
  Object.entries(nodes).forEach(([id, node]) => { node.id = id; });
  (fixtures.packages || []).forEach(pkg => {
    nodes[`pkg-version-${pkg.id}`] = element({rowVersion: "package", id: pkg.id});
    nodes[`pkg-asset-${pkg.id}`] = element({rowAsset: "package", id: pkg.id});
    nodes[`repo-package-${pkg.id}`] = element({rowRepo: "package", id: pkg.id});
    nodes[`repo-package-${pkg.id}`].value = pkg.repository || "";
  });
  (fixtures.portable || []).forEach(item => {
    nodes[`repo-portable-${item.id}`] = element({rowRepo: "portable", id: item.id});
    nodes[`repo-portable-${item.id}`].value = item.repository || "";
  });
  nodes["add-package-input"] = element();
  const tabs = ["cli", "plus", "portable", "package"].map(tab => element({tab}));
  const pills = [1, 2, 4, 8, 16].map(workers => element({workers: String(workers)}));
  const documentListeners = {};
  const windowListeners = {};
  const calls = [];
  const requests = [];
  const intervals = [];
  const snapshot = {
    version: "1.5.14", proxy_enabled: false, proxy: "http://127.0.0.1:7890",
    download_directory: "/tmp/cpa-downloads", download_workers: 4, prerelease: false,
    projects: Object.fromEntries(["cli", "plus"].map(key => [key, {
      key, title: key, repo: "https://github.com/example/project", directory: "/tmp/" + key,
      installed: false, service: "未安装", endpoint: "", latest: "", asset: "", local: "",
      ...(fixtures.projects || {})[key]
    }])), portable: fixtures.portable || [], packages: fixtures.packages || [], categories: ["全部"], catalog: []
  };
  const invoke = async (command, args) => {
    calls.push({command, args});
    if (fixtures.invoke) {
      const result = await fixtures.invoke(command, args);
      if (result !== undefined) return result;
    }
    if (command === "plugin:dialog|message") return fixtures.confirm === false ? "Cancel" : "Ok";
    if (command === "get_state" || command === "save_settings") return snapshot;
    if (command === "scan_installed_apps") return {apps: fixtures.apps || []};
    if (command === "get_catalog") return fixtures.catalog ? fixtures.catalog(args) : {items: [], total: 0};
    if (command === "get_job") return fixtures.job || {done: true, progress: 100, lines: ["安装完成"]};
    if (command === "check_manager_update") return {has_update: false};
    return {ok: true, deleted: 2};
  };
  const context = vm.createContext({
    console, setTimeout: callback => { callback(); }, setInterval: callback => { intervals.push(callback); },
    alert: () => {}, confirm: () => true,
    addEventListener: (name, callback) => { windowListeners[name] = callback; },
    document: {
      body: element(),
      getElementById: identity => nodes[identity] || null,
      querySelectorAll: selector => selector === ".main-tabs button" ? tabs : selector === ".quick-pill" ? pills : [],
      addEventListener: (name, callback) => { documentListeners[name] = callback; }
    },
    fetch: async (url, options) => {
      requests.push({url, options});
      if (native) throw new Error("原生模式不应请求 HTTP 接口");
      return {ok: true, json: async () => url.startsWith("/api/job/") ? {done: true, lines: []} : snapshot};
    }
  });
  vm.runInContext("globalThis.window = globalThis", context);
  if (native) {
    context.__TAURI__ = {core: {invoke}};
    vm.runInContext("Object.defineProperty(window, 'isTauri', {value: true})", context);
  }
  vm.runInContext(fs.readFileSync(path.join(directory, "app.js"), "utf8"), context);
  documentListeners.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  return {context, calls, requests, nodes, tabs, pills, intervals, body: context.document.body, documentListeners, windowListeners};
}

for (const relative of ["tauri/src", "cpa_mac/ui/web"]) {
  const directory = path.join(__dirname, "..", relative);

  function releaseFixtures(overrides = {}) {
    return {packages: [{id: "release-test", name: "测试软件", repository: "example/test", latest: "v3"}],
      ...overrides,
      invoke: async (command, args) => {
        const result = await overrides.invoke?.(command, args);
        if (result !== undefined) return result;
        if (args?.action === "release_options") return {releases: [{tag: "v3"}, {tag: "v2"}], page: 1, has_more: true};
        if (args?.action === "release_assets") return {tag: args.body.tag || "v3", recommended: "tool-macos-arm64.dmg",
          assets: [{name: "tool-macos-arm64.dmg", size: 1048576}, {name: "tool-macos-arm64.zip"}]};
      }};
  }

  test(`${relative}: 表格列直接提供版本下拉框，切换版本即时持久化并更新附件候选`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures());
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const versionSelect = app.context.document.getElementById("pkg-version-release-test");
    const assetSelect = app.context.document.getElementById("pkg-asset-release-test");
    versionSelect.dataset = {rowVersion: "package", id: "release-test"};
    assetSelect.dataset = {rowAsset: "package", id: "release-test"};
    versionSelect.value = "v2";
    await app.body.listeners.change({target: versionSelect});
    const call = app.calls.find(call => call.args?.action === "select_release");
    assert.equal(call.args.body.tag, "v2");
    assert.equal(call.args.body.id, "release-test");
    assert.match(app.nodes.log.textContent, /已切换目标版本为 v2/);
  });

  test(`${relative}: 表格列直接提供附件下拉框，选择具体附件即时持久化`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures());
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const assetSelect = app.context.document.getElementById("pkg-asset-release-test");
    assetSelect.dataset = {rowAsset: "package", id: "release-test"};
    assetSelect.value = "tool-macos-arm64.zip";
    await app.body.listeners.change({target: assetSelect});
    const call = app.calls.find(call => call.args?.action === "select_release");
    assert.equal(call.args.body.asset, "tool-macos-arm64.zip");
    assert.equal(call.args.body.id, "release-test");
    assert.match(app.nodes.log.textContent, /已切换安装附件为 tool-macos-arm64.zip/);
  });

  test(`${relative}: 版本下拉选项包含最新版与预发布支持`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures());
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    await app.context.preloadRowOptions("release-test", "example/test", "v3");
    const versionSelect = app.context.document.getElementById("pkg-version-release-test");
    assert.match(versionSelect.innerHTML, /value="".*>v3.*value="v2"/);
    assert.doesNotMatch(versionSelect.innerHTML, /自动跟随/);
    const assetSelect = app.context.document.getElementById("pkg-asset-release-test");
    assert.doesNotMatch(assetSelect.innerHTML, /自动匹配/);
  });

  test(`${relative}: 版本下拉支持加载更多页并去重`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures({invoke: (command, args) => {
      if (args?.action === "release_options" && args.body.page === 2) return {
        releases: [{tag: "v2"}, {tag: "v1"}], page: 2, has_more: false};
    }}));
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    await app.context.preloadRowOptions("release-test", "example/test", "v3");
    const versionSelect = app.context.document.getElementById("pkg-version-release-test");
    versionSelect.dataset = {rowVersion: "package", id: "release-test"};
    versionSelect.value = "__more__";
    await app.body.listeners.change({target: versionSelect});
    assert.match(versionSelect.innerHTML, /v1/);
    assert.equal((versionSelect.innerHTML.match(/value="v2"/g) || []).length, 1);
  });

  test(`${relative}: 手动切换过版本后点击检查自动切回最新版本`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures({
      packages: [{id: "release-test", name: "测试软件", repository: "example/test", latest: "v3", selected_tag: "v2", selected_asset: "tool-v2.dmg"}]
    }));
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const button = {dataset: {id: "release-test", rowCheck: "package"}, closest: () => null};
    await app.body.listeners.click({target: {closest: () => button}});
    const state = vm.runInContext("state", app.context);
    const row = state.packages.find(p => p.id === "release-test");
    assert.equal(row.selected_tag, "");
    assert.equal(row.selected_asset, "");
  });

  test(`${relative}: 切换版本网络失败时给出明确提示`, async () => {
    const app = await loadFrontend(directory, true, releaseFixtures({invoke: (command, args) => {
      if (args?.action === "select_release") throw new Error("network-timeout");
    }}));
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const versionSelect = app.context.document.getElementById("pkg-version-release-test");
    versionSelect.dataset = {rowVersion: "package", id: "release-test"};
    versionSelect.value = "v2";
    await app.body.listeners.change({target: versionSelect});
    assert.match(app.nodes.log.textContent, /切换版本失败：network-timeout/);
  });

  test(`${relative}: 打开后台使用原生命令且不依赖 window.open`, async () => {
    const app = await loadFrontend(directory);
    await app.body.listeners.click({target: {dataset: {openPage: "plus"}, closest: () => null}});
    const call = app.calls.find(call => call.command === "open_project_page");
    assert.equal(call.args.key, "plus");
    assert.match(app.nodes.log.textContent, /已在系统默认浏览器中打开后台/);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 原生后台打开失败明确提示且不伪报成功`, async () => {
    const app = await loadFrontend(directory, true, {invoke: command => {
      if (command === "open_project_page") throw new Error("服务未运行");
    }});
    await app.body.listeners.click({target: {dataset: {openPage: "cli"}, closest: () => null}});
    assert.match(app.nodes.log.textContent, /打开后台失败：服务未运行/);
    assert.doesNotMatch(app.nodes.log.textContent, /已在系统默认浏览器中打开后台/);
  });

  test(`${relative}: 重新读取 Key 独立调用后端且无密钥时禁用复制`, async () => {
    const app = await loadFrontend(directory, true, {invoke: (command, args) => {
      if (args?.action === "read_key") return {directory: "/tmp/plus", admin_key: "", admin_key_note: "数据库不能恢复原文"};
    }});
    app.nodes["plus-admin-key"] = element();
    app.nodes["plus-copy-key"] = element();
    app.nodes["plus-key-note"] = element();
    app.calls.length = 0;
    await app.body.listeners.click({target: {dataset: {refreshKey: "plus"}, closest: () => null}});
    assert.deepEqual(app.calls.map(call => call.command), ["project_action", "plugin:dialog|message"]);
    assert.equal(app.calls[1].args.message, "数据库不能恢复原文");
    assert.equal(app.calls[0].args.action, "read_key");
    assert.equal(app.nodes["plus-copy-key"].disabled, true);
    assert.equal(app.nodes["plus-key-note"].textContent, "数据库不能恢复原文");
  });

  test(`${relative}: 录入 Key 以密码方式输入并从后端读取保存结果`, async () => {
    const app = await loadFrontend(directory, true, {invoke: (command, args) => {
      if (args?.action === "save_key") return {directory: "/tmp/plus", admin_key: "fixture-key", admin_key_note: "已保存副本"};
    }});
    app.nodes["plus-admin-key"] = element();
    app.nodes["plus-copy-key"] = element();
    app.nodes["plus-key-note"] = element();
    const pending = app.body.listeners.click({target: {dataset: {enterKey: "plus"}, closest: () => null}});
    assert.equal(app.nodes["input-modal-value"].type, "password");
    app.nodes["input-modal-value"].value = " fixture-key ";
    app.nodes["input-modal-ok"].onclick();
    await pending;
    const call = app.calls.find(call => call.args?.action === "save_key");
    assert.equal(call.args.body.key, "fixture-key");
    assert.equal(app.nodes["plus-admin-key"].value, "fixture-key");
    assert.equal(app.nodes["plus-copy-key"].disabled, false);
    assert.doesNotMatch(app.nodes.log.textContent, /fixture-key/);
  });

  test(`${relative}: 在途 Key 读取不会覆盖录入后的新 Key`, async () => {
    let finishRead;
    let finishSave;
    const app = await loadFrontend(directory, true, {invoke: (command, args) => {
      if (args?.action === "read_key") return new Promise(resolve => { finishRead = resolve; });
      if (args?.action === "save_key") return new Promise(resolve => { finishSave = resolve; });
    }});
    app.nodes["plus-admin-key"] = element();
    app.nodes["plus-copy-key"] = element();
    const reading = app.context.refreshAdminKey();
    const saving = app.body.listeners.click({target: {dataset: {enterKey: "plus"}, closest: () => null}});
    app.nodes["input-modal-value"].value = "fixture-new-key";
    app.nodes["input-modal-ok"].onclick();
    await new Promise(resolve => setImmediate(resolve));
    finishRead({directory: "/tmp/plus", admin_key: "fixture-old-key"});
    await reading;
    const readCount = app.calls.filter(call => call.args?.action === "read_key").length;
    await app.context.refreshAdminKey();
    assert.equal(app.calls.filter(call => call.args?.action === "read_key").length, readCount);
    finishSave({directory: "/tmp/plus", admin_key: "fixture-new-key"});
    await saving;
    assert.equal(app.nodes["plus-admin-key"].value, "fixture-new-key");
    assert.equal(app.nodes["plus-copy-key"].dataset.copyKey, "fixture-new-key");
    assert.doesNotMatch(app.nodes.log.textContent, /fixture-new-key|fixture-old-key/);
  });

  test(`${relative}: 原生复制 Key 不依赖 WebKit 剪贴板权限`, async () => {
    const app = await loadFrontend(directory);
    await app.body.listeners.click({target: {dataset: {copyKey: "fixture-key"}, closest: () => null}});
    const call = app.calls.find(call => call.command === "copy_text");
    assert.equal(call.args.text, "fixture-key");
    assert.match(app.nodes.log.textContent, /成功复制/);
    assert.doesNotMatch(app.nodes.log.textContent, /fixture-key/);
  });

  test(`${relative}: Plus 页自动刷新 Key，不刷新其他页或覆盖改动中的目录`, async () => {
    const app = await loadFrontend(directory, true, {invoke: (command, args) => {
      if (args?.action === "read_key") return {directory: "/tmp/plus", admin_key: "fixture-key", admin_key_note: "已捕获"};
    }});
    app.calls.length = 0;
    app.intervals[0]();
    assert.equal(app.calls.length, 0);
    app.tabs[1].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.calls[0].args.action, "read_key");
    assert.equal(app.context.applyAdminKey({directory: "/different", admin_key: "wrong-key"}), false);
    app.nodes["dir-plus"] = {...element(), value: "/unsaved"};
    assert.equal(app.context.applyAdminKey({directory: "/tmp/plus", admin_key: "wrong-key"}), false);
  });

  test(`${relative}: 所有弹窗关闭按钮及并发快捷按钮可用`, async () => {
    const app = await loadFrontend(directory);
    for (const [modal, buttons] of [
      ["settings-modal", ["settings-close-x", "settings-done-btn"]],
      ["library-modal", ["library-close-x", "library-close-btn"]],
      ["associate-modal", ["associate-close-x", "associate-cancel-btn"]],
      ["doctor-modal", ["doctor-modal-close", "doctor-done-btn"]]
    ]) {
      for (const button of buttons) {
        app.nodes[modal].classList.remove("hidden");
        await app.nodes[button].listeners.click();
        assert.equal(app.nodes[modal].classList.contains("hidden"), true);
      }
    }
    app.calls.length = 0;
    await app.pills[4].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.calls.find(call => call.command === "save_settings").args.download_workers, 16);
    app.calls.length = 0;
    await app.nodes["workers-plus"].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.calls.find(call => call.command === "save_settings").args.download_workers, 5);
    app.calls.length = 0;
    await app.nodes["workers-minus"].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.calls.find(call => call.command === "save_settings").args.download_workers, 3);
  });

  test(`${relative}: 打开目录及移除记录不再静默失败`, async () => {
    const app = await loadFrontend(directory, true, {packages: [{id: "test", name: "test", repository: "example/tool"}], invoke: async command => {
      if (command === "open_path") throw "目录无权限";
    }});
    await app.body.listeners.click({target: {dataset: {openDownloadDir: ""}, closest: () => null}});
    assert.match(app.nodes.log.textContent, /目录无权限/);
    await app.body.listeners.click({target: {dataset: {remove: "package"}, closest: () => null}});
    const call = app.calls.find(call => call.command === "software_action");
    assert.equal(call.args.action, "remove");
    assert.equal(call.args.body.id, "test");
  });

  test(`${relative}: 发布页链接指向管理器仓库而非被管理的服务`, () => {
    const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
    assert.match(html, /id="manager-releases-link" href="https:\/\/github.com\/Xunzi229\/CPA-Win-Manager\/releases"/);
  });

  test(`${relative}: 复制失败不再伪报成功`, async () => {
    const app = await loadFrontend(directory, true, {invoke: command => {
      if (command === "copy_text") throw "剪贴板不可用";
    }});
    app.nodes["plus-admin-key"] = {select: () => {}};
    app.context.document.execCommand = () => false;
    await app.body.listeners.click({target: {dataset: {copyKey: "fixture-key"}, closest: () => null}});
    assert.match(app.nodes.log.textContent, /复制 Key 失败：剪贴板不可用/);
    assert.doesNotMatch(app.nodes.log.textContent, /Key 已复制/);
  });

  for (const mode of ["portable", "package"]) {
    for (const action of ["check", "install"]) {
      test(`${relative}: ${mode} 行${action}不会路由到其他类型`, async () => {
        const app = await loadFrontend(directory);
        const button = {dataset: {id: "test-id", [action === "check" ? "rowCheck" : "rowInstall"]: mode}, closest: () => null};
        const child = {closest: selector => selector === "button, a" ? button : null};
        await app.body.listeners.click({target: child});
        const call = app.calls.find(call => call.command === "software_action");
        assert.equal(call.args.mode, mode);
        assert.equal(call.args.action, action);
        assert.equal(call.args.body.id, "test-id");
      });
    }
  }

  test(`${relative}: 顶部批量按钮及清理按钮调用真实后端`, async () => {
    const app = await loadFrontend(directory);
    for (const [dataset, action] of [
      [{checkAll: "package"}, "check"], [{checkAll: "portable"}, "check"],
      [{upgradeAll: "package"}, "upgrade_all"], [{cleanAllDownloads: ""}, "clean_downloads"]
    ]) {
      app.calls.length = 0;
      await app.body.listeners.click({target: {dataset, closest: () => null}});
      assert.equal(app.calls.find(call => call.command === "software_action").args.action, action);
    }
    assert.match(app.nodes.log.textContent, /已清理 2 个管理器下载包/);
  });

  test(`${relative}: 取消清理确认或点击禁用按钮不触发操作`, async () => {
    const app = await loadFrontend(directory, true, {confirm: false});
    app.calls.length = 0;
    await app.body.listeners.click({target: {dataset: {cleanAllDownloads: ""}, closest: () => null}});
    await app.body.listeners.click({target: {disabled: true, dataset: {rowInstall: "package"}, closest: () => null}});
    assert.equal(app.calls.filter(call => call.command === "software_action").length, 0);
  });

  test(`${relative}: 原生字符串错误可见且失败任务不会显示成功`, async () => {
    const app = await loadFrontend(directory, true, {
      job: {done: true, progress: 25, lines: ["下载失败"], error: "网络不可用"},
      invoke: async command => { if (command === "software_action") throw "仓库不存在"; }
    });
    await assert.rejects(app.context.api("/api/software/package/check", {id: "test"}), /仓库不存在/);
    await app.context.poll("failed-job");
    assert.equal(app.nodes["progress-bar"].style.width, "25%");
    assert.match(app.nodes["status-text"].textContent, /任务失败：网络不可用/);
    assert.equal(app.nodes["progress-container"].classList.contains("hidden"), false);
  });

  test(`${relative}: 获取附件及编辑免安装配置有实际处理器`, async () => {
    const app = await loadFrontend(directory, true, {portable: [{id: "tool", name: "tool", repository: "example/tool", directory: "/tmp/tool"}]});
    await app.body.listeners.click({target: {id: "btn-portable-fetch-asset", dataset: {}, closest: () => null}});
    assert.equal(app.calls.find(call => call.command === "software_action").args.body.id, "tool");
    await app.body.listeners.change({target: {id: "portable-edit-keep", value: "config;data"}});
    const call = app.calls.find(call => call.args?.action === "edit");
    assert.equal(call.args.body.preserve, "config;data");
  });

  test(`${relative}: 原生输入框不依赖 WebKit prompt`, async () => {
    const app = await loadFrontend(directory);
    const pending = app.context.promptText("输入 GitHub 地址");
    app.nodes["input-modal-value"].value = "example/tool";
    app.nodes["input-modal-ok"].onclick();
    assert.equal(await pending, "example/tool");
    assert.equal(app.nodes["input-modal"].classList.contains("hidden"), true);
  });

  for (const key of ["cli", "plus"]) {
    for (const action of ["check", "install", "start", "stop", "restart", "clear_backups"]) {
      test(`${relative}: ${key} 的 ${action} 按钮与后端绑定`, async () => {
        const app = await loadFrontend(directory);
        await app.body.listeners.click({target: {dataset: {project: key, action}, closest: () => null}});
        const call = app.calls.find(call => call.command === "project_action");
        assert.equal(call.args.key, key);
        assert.equal(call.args.action, action);
      });
    }
  }

  test(`${relative}: cli 卡片不展示解压目录选择框且不展示清空安装备份`, async () => {
    const app = await loadFrontend(directory);
    const html = app.nodes["panel-cli"].innerHTML;
    assert.doesNotMatch(html, /id="dir-cli"/);
    assert.doesNotMatch(html, /data-browse="cli"/);
    assert.doesNotMatch(html, /data-action="clear_backups"/);
  });

  test(`${relative}: cli 已安装时展示更新按钮且点击触发 update 动作`, async () => {
    const app = await loadFrontend(directory, true, {
      projects: {
        cli: { installed: true, local: "v8.0.20", latest: "v8.0.22", update: true }
      }
    });
    const html = app.nodes["panel-cli"].innerHTML;
    assert.match(html, /data-action="update"/);
    await app.body.listeners.click({target: {dataset: {project: "cli", action: "update"}, closest: () => null}});
    const call = app.calls.find(call => call.command === "project_action" && call.args.action === "update");
    assert.ok(call);
    assert.equal(call.args.key, "cli");
  });

  test(`${relative}: brew_missing 时 cli 卡片展示提示并在点击操作时弹窗`, async () => {
    const app = await loadFrontend(directory, true, {
      projects: {
        cli: { installed: false, brew_missing: true }
      }
    });
    const html = app.nodes["panel-cli"].innerHTML;
    assert.match(html, /未检测到 Homebrew/);
    app.calls.length = 0;
    await app.body.listeners.click({target: {dataset: {project: "cli", action: "install"}, closest: () => null}});
    assert.equal(app.calls.filter(call => call.command === "project_action").length, 0);
    assert.ok(app.calls.some(call => call.command === "plugin:dialog|message" && /Homebrew/.test(JSON.stringify(call.args))));
  });

  test(`${relative}: 表格操作按钮保持单行且窄窗口可横向滚动`, () => {
    const css = fs.readFileSync(path.join(directory, "style.css"), "utf8");
    const script = fs.readFileSync(path.join(directory, "app.js"), "utf8");
    assert.match(css, /\.data-table button\s*\{\s*white-space: nowrap;\s*\}/);
    assert.match(css, /\.table-container\s*\{[^}]*overflow-x: auto;/);
    assert.match(script, /data-row-uninstall="package"[^>]*>移至废纸篓<\/button>/);
    assert.match(script, /data-row-clear-backup="portable"[^>]*>清空安装备份<\/button>/);
  });

  test(`${relative}: 发布页面链接共用按钮样式并保留链接语义`, () => {
    const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
    const css = fs.readFileSync(path.join(directory, "style.css"), "utf8");
    assert.match(html, /<a id="manager-releases-link"[^>]+class="btn-default link-btn"/);
    assert.match(css, /button, input, select, \.link-btn\s*\{\s*font: inherit;/);
    assert.match(css, /button, \.link-btn\s*\{[^}]*border-radius:[^}]*padding: 5px 12px;[^}]*font-size: 13px;[^}]*color: var\(--text-primary\);/);
    assert.match(css, /button:hover:not\(:disabled\), \.link-btn:hover\s*\{/);
    assert.match(css, /\.link-btn:focus-visible\s*\{[^}]*outline: 2px solid var\(--primary\);/);
  });

  test(`${relative}: Tauri 内置变量存在时可初始化并切换页面、打开设置`, async () => {
    const app = await loadFrontend(directory);
    assert.match(app.nodes["panel-cli"].innerHTML, /data-project="cli"/);
    app.tabs[2].listeners.click();
    assert.equal(app.nodes["panel-cli"].classList.contains("hidden"), true);
    assert.equal(app.nodes["panel-portable"].classList.contains("hidden"), false);
    app.nodes["settings-toggle"].listeners.click();
    assert.equal(app.nodes["settings-modal"].classList.contains("hidden"), false);
    app.nodes["settings-done-btn"].listeners.click();
    assert.equal(app.nodes["settings-modal"].classList.contains("hidden"), true);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 保存设置通过原生接口传递字段`, async () => {
    const app = await loadFrontend(directory);
    app.nodes["download-workers"].value = "12";
    app.nodes["prerelease-enabled"].checked = true;
    await app.nodes["download-workers"].listeners.change();
    const call = app.calls.find(call => call.command === "save_settings");
    assert.equal(call.args.download_workers, 12);
    assert.equal(call.args.prerelease, true);
    assert.equal(call.args.download_directory, "/tmp/cpa-downloads");
    assert.equal(app.nodes["auto-save-indicator"].textContent, "已自动保存");
    assert.equal(app.requests.length, 0);
  });

  for (const action of ["start", "stop", "restart"]) {
    test(`${relative}: ${action} 立即显示处理中并在实际状态返回后恢复按钮`, async () => {
      let finishJob;
      const job = new Promise(resolve => { finishJob = resolve; });
      const app = await loadFrontend(directory, true, {
        projects: {cli: {installed: true, is_running: true, service: "运行中（PID 123）"}},
        invoke: async command => {
          if (command === "project_action") return {job_id: "service-job"};
          if (command === "get_job") return job;
        }
      });
      const click = {target: {dataset: {project: "cli", action}, closest: () => null}};
      const pending = app.context.handleClick(click);
      const actionText = {start: "启动", stop: "停止", restart: "重启"}[action];
      assert.match(app.context.projectCard("cli"), new RegExp(`正在${actionText}…`));
      assert.doesNotMatch(app.context.projectCard("cli"), /运行中（PID 123）/);
      await app.context.handleClick(click);
      assert.equal(app.calls.filter(call => call.command === "project_action").length, 1);
      vm.runInContext('state.projects.cli.is_running = false; state.projects.cli.service = "未运行";', app.context);
      finishJob({done: true, progress: 100, lines: []});
      await pending;
      assert.match(app.context.projectCard("cli"), /未运行/);
      assert.doesNotMatch(app.context.projectCard("cli"), /正在.*…/);
    });
  }

  test(`${relative}: 旧的状态请求不能覆盖启停后刷新结果`, async () => {
    const app = await loadFrontend(directory);
    let finishOld;
    let requestCount = 0;
    const snapshot = vm.runInContext('JSON.parse(JSON.stringify(state))', app.context);
    app.context.__TAURI__.core.invoke = async command => {
      assert.equal(command, "get_state");
      requestCount++;
      if (requestCount === 1) return new Promise(resolve => { finishOld = resolve; });
      return {...snapshot, projects: {...snapshot.projects, cli: {...snapshot.projects.cli, service: "未运行", is_running: false}}};
    };
    const oldRequest = app.context.refresh();
    await app.context.refresh();
    finishOld({...snapshot, projects: {...snapshot.projects, cli: {...snapshot.projects.cli, service: "运行中（PID 123）", is_running: true}}});
    await oldRequest;
    assert.equal(vm.runInContext('state.projects.cli.service', app.context), "未运行");
  });

  for (const dataset of [
    {checkAll: "package"}, {checkAll: "portable"}, {upgradeAll: "package"},
    {rowCheck: "package", id: "software"}, {rowInstall: "portable", id: "software"},
    {project: "cli", action: "check"}
  ]) {
    test(`${relative}: ${JSON.stringify(dataset)} 在请求、任务执行及重绘期间持续置灰`, async () => {
      let createJob;
      let completeJob;
      const creation = new Promise(resolve => { createJob = resolve; });
      const completion = new Promise(resolve => { completeJob = resolve; });
      const app = await loadFrontend(directory, true, {invoke: command => {
        if (command === "software_action" || command === "project_action") return creation;
        if (command === "get_job") return completion;
      }});
      const button = {...element(dataset), closest: () => null};
      const pending = app.context.handleClick({target: button});
      assert.equal(button.disabled, true);
      await app.context.handleClick({target: button});
      const replacement = {...element({...dataset}), closest: () => null};
      const originalQuery = app.context.document.querySelectorAll;
      app.context.document.querySelectorAll = selector => selector === "button" ? [replacement] : originalQuery(selector);
      app.context.render();
      assert.equal(replacement.disabled, true);
      await app.context.handleClick({target: replacement});
      assert.equal(app.calls.filter(call => ["software_action", "project_action"].includes(call.command)).length, 1);
      createJob({job_id: "busy-job"});
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(replacement.disabled, true);
      completeJob({done: true, progress: 100, lines: []});
      await pending;
      assert.equal(button.disabled, false);
      assert.equal(replacement.disabled, false);
    });
  }

  for (const failure of ["提交失败", "任务失败", "任务已取消", "查询任务失败"]) {
    test(`${relative}: ${failure} 后恢复操作按钮`, async () => {
      const app = await loadFrontend(directory, true, {invoke: command => {
        if (command === "software_action") {
          if (failure === "提交失败") throw new Error(failure);
          return {job_id: "failed-job"};
        }
        if (command === "get_job") {
          if (failure === "查询任务失败") throw new Error(failure);
          return {done: true, error: failure, lines: []};
        }
      }});
      const button = {...element({checkAll: "package"}), closest: () => null};
      await app.context.handleClick({target: button});
      assert.equal(button.disabled, false);
      assert.match(app.nodes.log.textContent, new RegExp(failure));
    });
  }

  test(`${relative}: 独立检查更新按钮等待完成且不重复请求`, async () => {
    let complete;
    let waiting = false;
    const result = new Promise(resolve => { complete = resolve; });
    const app = await loadFrontend(directory, true, {invoke: command => {
      if (waiting && command === "check_manager_update") return result;
    }});
    waiting = true;
    const button = app.nodes["check-manager-update"];
    const before = app.calls.length;
    const pending = button.listeners.click({target: button});
    assert.equal(button.disabled, true);
    await button.listeners.click({target: button});
    assert.equal(app.calls.slice(before).filter(call => call.command === "check_manager_update").length, 1);
    complete({has_update: false, current: "1.5.14"});
    await pending;
    assert.equal(button.disabled, false);
  });

  test(`${relative}: 软件库添加成功后仍保持已添加按钮禁用`, async () => {
    const app = await loadFrontend(directory);
    const button = {...element({libAddRepo: "example/tool", libAddName: "Tool"}), closest: () => null};
    await app.context.handleClick({target: button});
    assert.equal(button.textContent, "已添加");
    assert.equal(button.disabled, true);
  });

  test(`${relative}: 任务轮询使用 IPC 且更新进度`, async () => {
    const app = await loadFrontend(directory);
    await app.context.poll("test-job");
    const call = app.calls.find(call => call.command === "get_job");
    assert.equal(call.args.jobId, "test-job");
    assert.equal(app.nodes["progress-bar"].style.width, "100%");
    assert.match(app.nodes["log"].textContent, /管理页面就绪/);
    assert.match(app.nodes["log"].textContent, /安装完成/);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 修改操作返回完整状态且动作名称匹配原生命令`, async () => {
    const app = await loadFrontend(directory);
    const result = await app.context.api("/api/project/cli/clear-backups");
    assert.ok(result.projects.cli);
    assert.equal(result.deleted, 2);
    assert.equal(app.calls.find(call => call.command === "project_action").args.action, "clear_backups");
    const software = await app.context.api("/api/software/portable/add", {name: "test-tool"});
    assert.ok(Array.isArray(software.portable));
    await assert.rejects(app.context.api("/api/unknown"), /不支持此接口/);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: Web 模式保持 GET 状态与任务查询、POST 设置`, async () => {
    const app = await loadFrontend(directory, false);
    await app.context.api("/api/job/web-job");
    await app.context.api("/api/settings", {download_workers: 8});
    assert.equal(app.requests.find(request => request.url === "/api/state").options.method, "GET");
    assert.equal(app.requests.find(request => request.url === "/api/job/web-job").options.method, "GET");
    const saved = app.requests.find(request => request.url === "/api/settings");
    assert.equal(saved.options.method, "POST");
    assert.equal(JSON.parse(saved.options.body).download_workers, 8);
  });

  test(`${relative}: 关联列表显示真实版本，缺失版本显示未知`, async () => {
    const fixtures = {
      packages: [{id: "v2rayn", name: "v2rayn", repository: "2dust/v2rayn", installed_version: "7.24.9"}],
      apps: [
        {id: "/Applications/v2rayN.app", name: "v2rayN", version: "7.24.9", directory: "/Applications/v2rayN.app"},
        {id: "/Applications/Unknown.app", name: "Unknown", version: "", directory: "/Applications/Unknown.app"}
      ]
    };
    const app = await loadFrontend(directory, true, fixtures);
    assert.match(app.nodes["panel-package"].innerHTML, />7\.24\.9<\/a>/);
    await app.context.openAssociateModal("v2rayn");
    assert.match(app.nodes["associate-table-body"].innerHTML, /<td>7\.24\.9<\/td>/);
    assert.match(app.nodes["associate-table-body"].innerHTML, /<td>未知<\/td>/);
    assert.ok(app.calls.some(call => call.command === "scan_installed_apps"));
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 刷新本地版本重新读取原生状态`, async () => {
    const app = await loadFrontend(directory);
    app.calls.length = 0;
    await app.body.listeners.click({target: {
      id: "refresh-installed-btn", dataset: {}, closest: () => null
    }});
    assert.deepEqual(app.calls.map(call => call.command), ["scan_installed_apps", "get_state"]);
    assert.match(app.nodes.log.textContent, /已加载 0 条记录/);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 远程软件搜索通过 IPC 传递关键词并显示真实结果`, async () => {
    const app = await loadFrontend(directory, true, {
      packages: [{id: "v2rayn", name: "v2rayn", repository: "https://github.com/2dust/v2rayn"}],
      catalog: () => ({items: [{name: "v2rayN", repository: "https://github.com/2dust/v2rayN", description: "<em>macOS</em>"}], total: 1360})
    });
    app.nodes["library-category-select"].value = "终端工具";
    app.nodes["library-search-input"].value = " v2rayN ";
    app.nodes["library-search-github"].checked = true;
    await app.context.queryLibrary();
    const call = app.calls.find(call => call.command === "get_catalog");
    assert.equal(call.args.query, "v2rayN");
    assert.equal(call.args.search_github, true);
    assert.equal(call.args.category, "终端工具");
    assert.match(app.nodes["library-count-text"].textContent, /显示 1 \/ 1360/);
    assert.match(app.nodes["library-table-body"].innerHTML, /https:\/\/github.com\/2dust\/v2rayN/);
    assert.match(app.nodes["library-table-body"].innerHTML, /已添加/);
    assert.match(app.nodes["library-table-body"].innerHTML, /&lt;em&gt;macOS&lt;\/em&gt;/);
    assert.equal(app.requests.length, 0);
  });

  test(`${relative}: 原生搜索失败显示错误而不是空结果`, async () => {
    const app = await loadFrontend(directory, true, {
      catalog: async () => { throw "GitHub 搜索请求受限，请稍后重试。"; }
    });
    app.nodes["library-add-selected"].disabled = false;
    await app.context.queryLibrary();
    assert.match(app.nodes["library-table-body"].innerHTML, /GitHub 搜索请求受限/);
    assert.doesNotMatch(app.nodes["library-table-body"].innerHTML, /未找到匹配的软件|undefined/);
    assert.match(app.nodes["library-count-text"].textContent, /查询失败/);
    assert.equal(app.nodes["library-add-selected"].disabled, true);
  });

  test(`${relative}: 旧搜索结果不会覆盖新搜索且本地搜索不启用 GitHub`, async () => {
    let completeFirst;
    const app = await loadFrontend(directory, true, {
      catalog: args => args.query === "old" ? new Promise(resolve => { completeFirst = resolve; })
        : {items: [{name: "NewTool", repository: "https://github.com/example/new", description: "最新结果"}], total: 1}
    });
    app.nodes["library-search-input"].value = "old";
    app.nodes["library-search-github"].checked = true;
    const first = app.context.queryLibrary();
    app.nodes["library-search-input"].value = "new";
    app.nodes["library-search-github"].checked = false;
    await app.context.queryLibrary();
    completeFirst({items: [{name: "OldTool", repository: "https://github.com/example/old"}], total: 99});
    await first;
    assert.match(app.nodes["library-table-body"].innerHTML, /NewTool/);
    assert.doesNotMatch(app.nodes["library-table-body"].innerHTML, /OldTool/);
    assert.match(app.nodes["library-count-text"].textContent, /显示 1 \/ 1/);
    assert.equal(app.calls.filter(call => call.command === "get_catalog").at(-1).args.search_github, false);
  });

  test(`${relative}: 切换到 package 标签页自动触发状态刷新`, async () => {
    const app = await loadFrontend(directory, true);
    app.calls.length = 0;
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(app.calls.some(call => call.command === "get_state"), "应调用 get_state 刷新状态");
  });

  test(`${relative}: triggerAutoRefresh 在 package 标签页主动触发状态刷新`, async () => {
    const app = await loadFrontend(directory, true);
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    app.calls.length = 0;
    await app.context.triggerAutoRefresh(true);
    assert.ok(app.calls.some(call => call.command === "get_state"), "triggerAutoRefresh 应调用 get_state 刷新状态");
  });

  test(`${relative}: 表格软件列优先读取本地关联的软件名，本地未安装时读取远程项目的项目名称`, async () => {
    const app = await loadFrontend(directory, true, {
      packages: [
        {
          id: "auth-installed",
          name: "releases",
          repository: "https://github.com/Xunzi229/open-authenticator",
          installed_name: "Authenticator",
          installed_dir: "/Applications/Authenticator.app",
          installed_version: "1.0.8"
        },
        {
          id: "auth-uninstalled",
          name: "releases",
          repository: "https://github.com/Xunzi229/open-authenticator",
          installed_name: "",
          installed_dir: "",
          installed_version: ""
        },
        {
          id: "v2rayn-row",
          name: "v2rayn",
          repository: "https://github.com/2dust/v2rayn",
          installed_name: "v2rayN",
          installed_dir: "/Applications/v2rayN.app",
          installed_version: "7.25.4"
        }
      ]
    });
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const html = app.nodes["panel-package"].innerHTML;
    assert.match(html, /<strong>Authenticator<\/strong>/, "已关联本地软件应显示 Authenticator");
    assert.match(html, /<strong>open-authenticator<\/strong>/, "未安装本地软件应显示远程项目名称 open-authenticator");
    assert.match(html, /<strong>v2rayN<\/strong>/, "已关联本地软件应显示本地名称 v2rayN");
    assert.doesNotMatch(html, /<strong>releases<\/strong>/, "软件名绝不应显示为 releases");
  });

  test(`${relative}: 添加软件时从 GitHub 地址正确解析项目名而非 releases`, async () => {
    let addedArgs = null;
    const app = await loadFrontend(directory, true, {
      invoke: async (command, args) => {
        if (command === "software_action" && args.action === "add") {
          addedArgs = args.body;
          return {ok: true};
        }
      }
    });
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    app.nodes["add-package-input"].value = "https://github.com/Xunzi229/open-authenticator/releases";
    await app.body.listeners.click({target: {id: "btn-add-package", dataset: {}, closest: () => null}});
    assert.ok(addedArgs, "应调用添加软件接口");
    assert.equal(addedArgs.name, "open-authenticator", "添加的软件名应为项目名称 open-authenticator 而非 releases");
  });

  test(`${relative}: 安装包表格行内直接提供 GitHub 地址输入框，修改后即时保存并更新`, async () => {
    const app = await loadFrontend(directory, true, {
      packages: [{id: "pkg-1", name: "v2rayn", repository: "https://github.com/2dust/v2rayn"}]
    });
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const html = app.nodes["panel-package"].innerHTML;
    assert.match(html, /data-row-repo="package"/, "行内应包含 GitHub 地址输入框");
    assert.match(html, /id="repo-package-pkg-1"/, "输入框应包含唯一 id");

    const repoInput = app.context.document.getElementById("repo-package-pkg-1");
    repoInput.dataset = {rowRepo: "package", id: "pkg-1"};
    repoInput.value = "https://github.com/clash-verge-rev/clash-verge-rev";
    await app.body.listeners.change({target: repoInput});

    const editCall = app.calls.find(call => call.args?.action === "edit" && call.args?.mode === "package");
    assert.ok(editCall, "应调用 package edit 接口");
    assert.equal(editCall.args.body.id, "pkg-1");
    assert.equal(editCall.args.body.repository, "https://github.com/clash-verge-rev/clash-verge-rev");
  });

  test(`${relative}: 免安装表格行内直接提供 GitHub 地址输入框，修改后即时保存`, async () => {
    const app = await loadFrontend(directory, true, {
      portable: [{id: "port-1", name: "rg", repository: "https://github.com/BurntSushi/ripgrep", directory: "/tmp/rg"}]
    });
    app.tabs[2].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const html = app.nodes["panel-portable"].innerHTML;
    assert.match(html, /data-row-repo="portable"/, "免安装表格行内应包含 GitHub 地址输入框");
    assert.match(html, /id="repo-portable-port-1"/, "输入框应包含唯一 id");

    const repoInput = app.context.document.getElementById("repo-portable-port-1");
    repoInput.dataset = {rowRepo: "portable", id: "port-1"};
    repoInput.value = "https://github.com/sharkdp/fd";
    await app.body.listeners.change({target: repoInput});

    const editCall = app.calls.find(call => call.args?.action === "edit" && call.args?.mode === "portable");
    assert.ok(editCall, "应调用 portable edit 接口");
    assert.equal(editCall.args.body.id, "port-1");
    assert.equal(editCall.args.body.repository, "https://github.com/sharkdp/fd");
  });

  test(`${relative}: 行内 GitHub 地址为空时报错并还原原值`, async () => {
    const app = await loadFrontend(directory, true, {
      packages: [{id: "pkg-1", name: "v2rayn", repository: "https://github.com/2dust/v2rayn"}]
    });
    app.tabs[3].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    const repoInput = app.context.document.getElementById("repo-package-pkg-1");
    repoInput.dataset = {rowRepo: "package", id: "pkg-1"};
    repoInput.value = "   ";
    await app.body.listeners.change({target: repoInput});

    assert.equal(repoInput.value, "https://github.com/2dust/v2rayn", "输入空地址后应还原为原地址");
    const editCall = app.calls.find(call => call.args?.action === "edit" && call.args?.mode === "package");
    assert.equal(editCall, undefined, "空地址不应发起保存请求");
  });

  test(`${relative}: cli 在端口被其他进程占用时展示占用提示且点击启动阻止并提示`, async () => {
    const app = await loadFrontend(directory, true, {
      projects: {
        cli: {
          installed: true,
          is_running: false,
          service: "未运行（端口 8317 被其他程序占用：PID 58043，/path/to/cli-proxy-api）",
          port_info: {
            port: 8317,
            pid: 58043,
            command: "cli-proxy",
            path: "/path/to/cli-proxy-api"
          }
        }
      }
    });
    const html = app.nodes["panel-cli"].innerHTML;
    assert.match(html, /service-status-alert/, "端口被占用时应展示 alert 样式");
    assert.match(html, /58043/, "应包含占用进程 PID");
    assert.match(html, /service-status-detail/, "长路径应放入 detail 次级说明");
    assert.match(html, /\/path\/to\/cli-proxy-api/, "应包含占用程序路径");

    await app.body.listeners.click({target: {dataset: {project: "cli", action: "start"}, closest: () => null}});
    const startCall = app.calls.find(call => call.command === "project_action" && call.args?.action === "start");
    assert.equal(startCall, undefined, "端口被占用时不应触发后端启动命令");
    assert.ok(app.calls.some(call => call.command === "plugin:dialog|message" && /端口 8317 已被其他程序占用/.test(JSON.stringify(call.args))), "应弹出端口占用提示");
  });


  test(`${relative}: cli 端口被占用时定时器与聚焦事件触发自动刷新`, async () => {
    const app = await loadFrontend(directory, true, {
      projects: {
        cli: {
          installed: true,
          is_running: false,
          service: "未运行（端口 8317 被其他程序占用：PID 58043）",
          port_info: {
            port: 8317,
            pid: 58043,
            command: "cli-proxy",
            path: "/path/to/cli-proxy-api"
          }
        }
      }
    });
    app.calls.length = 0;
    app.intervals[0]();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(app.calls.some(call => call.command === "get_state"), "端口占用时定时器应触发状态刷新");

    app.calls.length = 0;
    app.windowListeners.focus();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(app.calls.some(call => call.command === "get_state"), "窗口聚焦时应触发状态刷新");
  });

  test(`${relative}: cli 运行中状态不重复显示 运行中 (运行中 ...)`, async () => {
    const app = await loadFrontend(directory, true, {
      projects: {
        cli: {
          installed: true,
          is_running: true,
          service: "运行中（PID 12345）"
        }
      }
    });
    const html = app.nodes["panel-cli"].innerHTML;
    assert.doesNotMatch(html, /运行中\s*\(运行中/, "不应出现重复嵌套的运行中字样");
    assert.match(html, /运行中（PID 12345）/);
  });
}
