/* DS Mac Cleaner — local, dependency-free UI */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const TOKEN = $('meta[name="dsmc-token"]').content;
  const VERSION = $('meta[name="dsmc-version"]').content;

  const CATEGORY_ORDER = ["containers", "appsupport", "caches", "dev", "media", "system", "user", "other"];
  const CATEGORY_LABELS = {
    containers: "应用容器",
    appsupport: "应用数据",
    caches: "缓存",
    dev: "开发工具",
    media: "聊天与媒体",
    system: "系统与虚拟内存",
    user: "个人文件",
    other: "其他",
  };

  const state = {
    scan: null,
    scanLoadedAt: 0,
    selected: new Set(),
    expanded: new Set(),
    filter: "safe",
    scanning: false,
    cleaning: false,
    db: null,
    history: null,
  };

  const el = {};

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    cacheElements();
    bindEvents();
    try {
      const health = await api("/api/health");
      updateDbPill(health.db);
      if (!health.db?.connected) scheduleDbProbe(0);
    } catch (err) {
      toast("后端连接失败", err.message || String(err), "error");
    }
    try {
      const latest = await api("/api/latest");
      if (latest.scan) {
        applyScan(latest.scan);
      }
    } catch (err) {
      toast("读取报告失败", err.message || String(err), "error");
    }
    // This product scans when the user opens the app, not on a background timer.
    startScan(true);
    loadHistory();
    setInterval(refreshRelativeTimes, 30000);
  }

  function cacheElements() {
    el.scanOverlay = $("#scan-overlay");
    el.scanStage = $("#scan-stage");
    el.scanPercent = $("#scan-percent");
    el.scanRing = $("#scan-ring-progress");
    el.scanMeta = $("#scan-meta");
    el.scanBtn = $("#scan-btn");
    el.rescanTop = $("#rescan-top");
    el.dbPill = $("#db-pill");
    el.dbDot = $("#db-dot");
    el.dbLabel = $("#db-label");
    el.insightGrid = $("#insight-grid");
    el.topList = $("#top-list");
    el.winList = $("#win-list");
    el.winsTotal = $("#wins-total");
    el.stackedBar = $("#stacked-bar");
    el.mapLegend = $("#map-legend");
    el.mapNote = $("#map-note");
    el.cleanGroups = $("#clean-groups");
    el.selectionBar = $("#selection-bar");
    el.selectionSize = $("#selection-size");
    el.selectionCount = $("#selection-count");
    el.cleanSelected = $("#clean-selected");
    el.riskFilters = $("#risk-filters");
    el.historyList = $("#history-list");
    el.historySource = $("#history-source");
    el.dbCallout = $("#db-callout");
    el.toast = $("#toast");
    el.refreshToast = $("#refresh-toast");
    el.refreshText = $("#refresh-text");
    el.toastTitle = $("#toast-title");
    el.toastBody = $("#toast-body");
    el.dialog = $("#confirm-dialog");
    el.confirmForm = $("#confirm-form");
    el.confirmWord = $("#confirm-word");
    el.dialogConfirm = $("#dialog-confirm");
    el.dialogError = $("#dialog-error");
    el.dialogSize = $("#dialog-size");
    el.dialogCount = $("#dialog-count");
    el.dialogList = $("#dialog-list");
  }

  function bindEvents() {
    $("#scan-btn").addEventListener("click", () => startScan(false));
    $("#rescan-top").addEventListener("click", () => startScan(false));
    $("#scan-empty")?.addEventListener("click", () => startScan(false));
    $("#jump-clean").addEventListener("click", () => $("#clean").scrollIntoView({ behavior: "smooth" }));
    $("#storage-settings").addEventListener("click", openStorageSettings);
    $("#db-pill").addEventListener("click", () => (state.db?.connected ? showDbInfo() : startDb()));
    $("#start-db").addEventListener("click", startDb);
    $("#refresh-history").addEventListener("click", loadHistory);
    $("#select-all").addEventListener("click", selectAllSafe);
    $("#clear-all").addEventListener("click", clearSelection);
    $("#clean-selected").addEventListener("click", openConfirm);

    el.riskFilters.addEventListener("click", (event) => {
      const chip = event.target.closest(".chip[data-filter]");
      if (!chip) return;
      state.filter = chip.dataset.filter;
      $$(".chip", el.riskFilters).forEach((node) => node.classList.toggle("active", node === chip));
      renderCleanGroups();
    });

    el.cleanGroups.addEventListener("change", (event) => {
      const input = event.target.closest(".check[data-id]");
      if (!input) return;
      if (input.checked) state.selected.add(input.dataset.id);
      else state.selected.delete(input.dataset.id);
      updateSelection();
    });

    el.cleanGroups.addEventListener("click", (event) => {
      const reveal = event.target.closest("[data-reveal]");
      if (reveal) {
        revealPath(reveal.dataset.reveal);
        return;
      }
      const more = event.target.closest("[data-expand]");
      if (more) {
        state.expanded.add(more.dataset.expand);
        renderCleanGroups();
      }
    });

    $("#dialog-close").addEventListener("click", closeDialog);
    $("#dialog-cancel").addEventListener("click", closeDialog);
    el.dialog.addEventListener("cancel", (event) => {
      if (state.cleaning) event.preventDefault();
    });
    el.confirmWord.addEventListener("input", () => {
      el.dialogConfirm.disabled = el.confirmWord.value !== "DELETE";
      el.dialogError.hidden = true;
    });
    el.confirmForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (el.confirmWord.value !== "DELETE") return;
      startCleanup();
    });
    $("#toast-close").addEventListener("click", hideToast);

}

  // ------------------------------------------------------------------ API ---
  async function api(path, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && opts.method !== "GET") opts.headers["X-DSMC-Token"] = TOKEN;
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error */ }
    if (!res.ok) {
      const message = (data && data.error) || `HTTP ${res.status}`;
      throw new Error(message);
    }
    return data;
  }

  // --------------------------------------------------------------- scanning --
  async function startScan(auto, silent = Boolean(state.scan)) {
    if (state.scanning || state.cleaning) return;
    state.scanning = true;
    el.scanBtn.disabled = true;
    el.rescanTop.disabled = true;
    if (silent) {
      el.refreshToast.hidden = false;
      el.refreshText.textContent = "正在准备只读扫描…";
    } else {
      el.scanOverlay.hidden = false;
      document.body.classList.add("locked");
      setScanProgress(1, "准备只读扫描…");
    }
    const showProgress = (task) => {
      const pct = task.progress || 0;
      const stage = task.stage || "扫描中…";
      if (silent) {
        el.refreshText.textContent = `${stage} · ${pct}%`;
      } else {
        setScanProgress(pct, stage);
      }
    };
    try {
      const res = await api("/api/scans", {
        method: "POST",
        body: { trigger: auto ? "launch" : "manual" },
      });
      if (!res.task) throw new Error("服务器没有返回扫描任务");
      await pollTask(res.task.id, showProgress, (task) => {
        if (task.status === "error") throw new Error(task.error || "扫描失败");
        applyScan(task.result);
        toast("扫描完成", `已分析 ${task.result.summary.item_count} 项，只读扫描没有修改任何文件。`);
        loadHistory();
      });
    } catch (err) {
      toast("扫描失败", err.message || String(err), "error");
    } finally {
      state.scanning = false;
      el.scanBtn.disabled = false;
      el.rescanTop.disabled = false;
      await sleep(260);
      el.scanOverlay.hidden = true;
      el.refreshToast.hidden = true;
      document.body.classList.remove("locked");
    }
  }

  async function pollTask(taskId, onUpdate, onDone) {
    let failures = 0;
    while (true) {
      try {
        const res = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
        failures = 0;
        const task = res.task;
        onUpdate(task);
        if (task.status === "done") {
          onDone(task);
          return task;
        }
        if (task.status === "error") {
          onDone(task);
          return task;
        }
      } catch (err) {
        failures += 1;
        if (failures >= 4) throw err;
      }
      await sleep(700);
    }
  }

  function setScanProgress(percent, stage) {
    const pct = Math.max(0, Math.min(100, Number(percent) || 0));
    el.scanPercent.innerHTML = `${pct}<span>%</span>`;
    el.scanStage.textContent = stage;
    const circumference = 2 * Math.PI * 52;
    el.scanRing.style.strokeDashoffset = String(circumference * (1 - pct / 100));
  }

  // ------------------------------------------------------------- render scan --
  function applyScan(scan) {
    if (!scan) return;
    state.scan = scan;
    state.scanLoadedAt = Date.now();
    state.expanded = new Set();
    state.selected = new Set(scan.items.filter((i) => i.cleanable).map((i) => i.id));
    renderHeader(scan);
    renderStats(scan);
    renderInsights(scan);
    renderMap(scan);
    renderTop(scan);
    renderWins(scan);
    renderCleanGroups();
    updateSelection();
    refreshRelativeTimes();
  }

  function renderHeader(scan) {
    const disk = scan.disk || {};
    const pct = Number(disk.percent) || 0;
    $("#disk-percent").textContent = `${Math.round(pct)}%`;
    $("#disk-caption").textContent = `${fmt(disk.used)} / ${fmt(disk.total)}`;
    $("#disk-ring").style.setProperty("--used", String(Math.max(0, Math.min(100, pct))));
    $("#disk-total").textContent = fmt(disk.total);
    $("#disk-free").textContent = fmt(disk.free);
    $("#disk-system").textContent = fmt(scan.summary?.system_data_estimate);
    $("#host-chip").textContent = scan.host?.hostname || "Macintosh HD";
    el.scanBtn.textContent = "重新扫描";
    el.scanMeta.innerHTML = `<span class="pulse-dot"></span>扫描于 ${formatDate(scan.created_at)} · 用时 ${(scan.duration_ms / 1000).toFixed(1)} 秒 · ${scan.summary.item_count} 项 · 全程只读`;
    $("#stat-time").textContent = timeAgo(scan.created_at);
    $("#stat-duration").textContent = `${scan.host?.macos || "macOS"} · ${(scan.duration_ms / 1000).toFixed(1)}s`;
    $("#stat-hidden").textContent = fmt(scan.summary.system_data_estimate);
    $("#stat-hidden-note").textContent = `其中 ~/Library ${fmt(scan.summary.library_bytes)}`;
    $("#stat-cleanable").textContent = fmt(scan.summary.cleanable_bytes);
    const safeCount = scan.items.filter((i) => i.cleanable).length;
    $("#stat-cleanable-note").textContent = safeCount
      ? `${safeCount} 个白名单缓存`
      : "缓存已经清理干净";
    $("#stat-review").textContent = fmt(scan.summary.review_bytes);
    $("#stat-review-note").textContent = "聊天记录 / 个人文件 / VM";
    $("#map-note").textContent = `共 ${fmt(scan.categories.reduce((sum, c) => sum + c.bytes, 0))}`;
  }

  function renderStats() { /* handled in renderHeader */ }

  function renderInsights(scan) {
    const items = (scan.insights || []).slice(0, 6);
    if (!items.length) return;
    el.insightGrid.innerHTML = items.map((item, idx) => `
      <article class="insight" data-severity="${esc(item.severity || "low")}">
        <span class="insight-index">${String(idx + 1).padStart(2, "0")} / ${esc((item.severity || "").toUpperCase())}</span>
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.body)}</p>
      </article>
    `).join("");
  }

  function renderMap(scan) {
    const cats = (scan.categories || []).filter((c) => c.key !== "user");
    const total = cats.reduce((sum, c) => sum + Number(c.bytes || 0), 0) || 1;
    el.stackedBar.innerHTML = cats.map((c) => {
      const pct = (c.bytes / total) * 100;
      return `<span style="width:${pct.toFixed(3)}%;background:${esc(c.color)}" title="${esc(c.label)} ${fmt(c.bytes)}"></span>`;
    }).join("") || '<div class="stack-empty"></div>';

    let cursor = 0;
    const segments = cats.map((c) => {
      const start = cursor;
      cursor += (c.bytes / total) * 100;
      return `${esc(c.color)} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
    });
    const donut = $("#category-donut");
    donut.style.background = segments.length
      ? `conic-gradient(from -90deg, ${segments.join(", ")})`
      : "conic-gradient(from -90deg, #e7ece5 0 100%)";
    $("#donut-total").textContent = fmt(total);

    el.mapLegend.innerHTML = cats.map((c) => `
      <span class="legend-item">
        <i class="legend-dot" style="background:${esc(c.color)}"></i>
        <span class="legend-label">${esc(c.label)}</span>
        <strong>${fmt(c.bytes)}</strong>
        <em>${c.percent ?? ((c.bytes / total) * 100).toFixed(1)}%</em>
      </span>
    `).join("");
    $("#map-note").textContent = `共 ${fmt(total)} · 不含个人文件`;
    const largest = cats.slice().sort((a, b) => b.bytes - a.bytes)[0];
    $("#map-footnote").textContent = largest
      ? `最大来源：${largest.label} ${fmt(largest.bytes)}，占 ${largest.percent ?? ((largest.bytes / total) * 100).toFixed(1)}%`
      : "按占用大小排序 · 每次扫描重新计算";
  }

  function renderTop(scan) {
    const top = scan.top_consumers || [];
    if (!top.length) {
      el.topList.innerHTML = '<p class="empty-copy">没有可展示的项目。</p>';
      return;
    }
    const max = Math.max(...top.map((i) => i.bytes), 1);
    el.topList.innerHTML = top.map((item, idx) => `
      <div class="top-row">
        <span class="top-rank">${String(idx + 1).padStart(2, "0")}</span>
        <div class="top-info">
          <div class="top-line">
            <span class="top-name">${esc(item.label)}</span>
            <span class="top-size">${fmt(item.bytes)}</span>
          </div>
          <div class="top-meta">
            <span class="top-category">${esc(item.category_label)}</span>
            <span class="risk-tag ${item.risk === "safe" ? "safe" : ""}">${item.risk === "safe" ? "SAFE" : "REVIEW"}</span>
          </div>
          <div class="top-track"><span style="width:${Math.max(2, (item.bytes / max) * 100).toFixed(2)}%;background:${esc(item.color)}"></span></div>
        </div>
      </div>
    `).join("");
  }

  function renderWins(scan) {
    const safe = scan.items.filter((i) => i.cleanable).sort((a, b) => b.bytes - a.bytes);
    el.winsTotal.textContent = fmt(scan.summary.cleanable_bytes);
    if (!safe.length) {
      el.winList.innerHTML = '<p class="empty-copy">这次没有发现可安全清理的缓存。</p>';
      return;
    }
    el.winList.innerHTML = safe.slice(0, 4).map((item) => `
      <div class="win-row">
        <div>
          <span class="win-name">${esc(item.label)}</span>
          <span class="win-sub">${esc(item.category_label)} · 可重建</span>
        </div>
        <span class="win-size">${fmt(item.bytes)}</span>
      </div>
    `).join("");
  }

  // --------------------------------------------------------------- cleaning --
  function renderCleanGroups() {
    const scan = state.scan;
    if (!scan) return;
    const all = scan.items;
    const safeCount = all.filter((i) => i.cleanable).length;
    $("#count-safe").textContent = String(safeCount);
    $("#count-review").textContent = String(all.length - safeCount);
    $("#count-all").textContent = String(all.length);

    let items = all;
    if (state.filter === "safe") items = all.filter((i) => i.cleanable);
    if (state.filter === "review") items = all.filter((i) => !i.cleanable);
    if (!items.length) {
      const noSafe = state.filter === "safe" && safeCount === 0;
      el.cleanGroups.innerHTML = `<div class="empty-state card">
        <div class="empty-orbit"></div>
        <h3>${noSafe ? "当前没有可安全清理的缓存" : "这个筛选下没有项目"}</h3>
        <p>${noSafe ? "可重建缓存已经清空。你仍可以查看「需人工确认」中的大文件，或稍后重新扫描。" : "切换到其他分类看看。"}</p>
      </div>`;
      return;
    }

    const grouped = new Map();
    for (const item of items) {
      if (!grouped.has(item.category)) grouped.set(item.category, []);
      grouped.get(item.category).push(item);
    }
    const keys = [...grouped.keys()].sort((a, b) => {
      const ia = CATEGORY_ORDER.indexOf(a);
      const ib = CATEGORY_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    el.cleanGroups.innerHTML = keys.map((key) => {
      const group = grouped.get(key).sort((a, b) => b.bytes - a.bytes);
      const total = group.reduce((sum, i) => sum + Number(i.bytes || 0), 0);
      const color = (scan.categories.find((c) => c.key === key) || {}).color || "#64748b";
      const expanded = state.expanded.has(key);
      const visible = expanded ? group : group.slice(0, 8);
      const hiddenCount = group.length - visible.length;
      return `
        <section class="clean-group">
          <div class="group-head">
            <span class="group-dot" style="background:${esc(color)}"></span>
            <span class="group-title">${esc(CATEGORY_LABELS[key] || group[0].category_label || key)}</span>
            <span class="group-count">${group.length} 项</span>
            <span class="group-total">${fmt(total)}</span>
          </div>
          ${visible.map(renderCleanRow).join("")}
          ${hiddenCount > 0 ? `<button class="group-more" type="button" data-expand="${esc(key)}">显示其余 ${hiddenCount} 项 <span>↓</span></button>` : ""}
        </section>
      `;
    }).join("");
  }

  function renderCleanRow(item) {
    const checked = state.selected.has(item.id) && item.cleanable;
    const riskClass = item.risk === "safe" ? "safe-label" : "review-label";
    const riskText = item.risk === "safe" ? "安全" : "只读";
    return `
      <div class="clean-row ${item.cleanable ? "" : "locked"}" data-item="${esc(item.id)}">
        <div class="check-wrap">
          <input class="check" type="checkbox" data-id="${esc(item.id)}" ${checked ? "checked" : ""}
            ${item.cleanable ? "" : "disabled"} aria-label="选择 ${esc(item.label)}">
        </div>
        <div class="item-icon">${iconFor(item.kind)}</div>
        <div class="item-main">
          <div class="item-title-line">
            <span class="item-title">${esc(item.label)}</span>
            <span class="risk-tag ${riskClass}">${riskText}</span>
          </div>
          <p class="item-desc">${esc(item.description)}</p>
          <div class="item-path" title="${esc(item.path)}">${esc(item.display_path)}</div>
        </div>
        <div class="item-side">
          <span class="item-size">${fmt(item.bytes)}</span>
          <button class="item-action" type="button" data-reveal="${esc(item.path)}">在 Finder 中显示</button>
        </div>
      </div>
    `;
  }

  function updateSelection() {
    if (!state.scan) return;
    const selectedItems = state.scan.items.filter((i) => state.selected.has(i.id) && i.cleanable);
    const bytes = selectedItems.reduce((sum, i) => sum + Number(i.bytes || 0), 0);
    el.selectionSize.textContent = fmt(bytes);
    el.selectionCount.textContent = `${selectedItems.length} 项`;
    el.cleanSelected.disabled = selectedItems.length === 0 || state.cleaning;
    el.cleanSelected.textContent = state.cleaning ? "清理中…" : "清理选中项";
    el.selectionBar.classList.toggle("visible", selectedItems.length > 0);
  }

  function selectAllSafe() {
    if (!state.scan) return;
    state.selected = new Set(state.scan.items.filter((i) => i.cleanable).map((i) => i.id));
    syncCheckboxes();
    updateSelection();
  }

  function clearSelection() {
    state.selected.clear();
    syncCheckboxes();
    updateSelection();
  }

  function syncCheckboxes() {
    $$(".check[data-id]", el.cleanGroups).forEach((input) => {
      input.checked = state.selected.has(input.dataset.id);
    });
  }

  function openConfirm() {
    if (!state.scan || state.selected.size === 0) return;
    const selected = state.scan.items.filter((i) => state.selected.has(i.id) && i.cleanable);
    const bytes = selected.reduce((sum, i) => sum + Number(i.bytes || 0), 0);
    el.dialogSize.textContent = fmt(bytes);
    el.dialogCount.textContent = String(selected.length);
    const preview = selected.slice(0, 5);
    el.dialogList.innerHTML = preview.map((item) => `
      <div class="dialog-item"><span>${esc(item.label)}</span><strong>${fmt(item.bytes)}</strong></div>
    `).join("") + (selected.length > preview.length
      ? `<div class="dialog-item dialog-more"><span>还有 ${selected.length - preview.length} 个项目…</span><strong>${fmt(bytes)}</strong></div>`
      : "");
    el.confirmWord.value = "";
    el.dialogError.hidden = true;
    el.dialogConfirm.disabled = true;
    el.dialogConfirm.textContent = "永久清理";
    if (typeof el.dialog.showModal === "function") el.dialog.showModal();
    else el.dialog.setAttribute("open", "");
    setTimeout(() => el.confirmWord.focus(), 40);
  }

  function closeDialog() {
    if (state.cleaning) return;
    if (typeof el.dialog.close === "function") el.dialog.close();
    else el.dialog.removeAttribute("open");
  }

  async function startCleanup() {
    if (state.cleaning) return;
    const ids = [...state.selected];
    if (!ids.length) return;
    state.cleaning = true;
    el.dialogConfirm.disabled = true;
    el.dialogConfirm.textContent = "清理中…";
    el.dialogError.hidden = true;
    updateSelection();
    try {
      const res = await api("/api/cleanups", {
        method: "POST",
        body: { scan_id: state.scan.id, item_ids: ids, confirm: "DELETE" },
      });
      const task = await pollTask(res.task.id, (t) => {
        el.dialogConfirm.textContent = t.stage || "清理中…";
      }, (t) => {
        if (t.status === "error") throw new Error(t.error || "清理失败");
      });
      const result = task.result || {};
      const freed = result.freed_bytes || 0;
      const errors = result.errors || [];
      state.cleaning = false;
      if (typeof el.dialog.close === "function") el.dialog.close();
      else el.dialog.removeAttribute("open");
      if (freed > 0) {
        toast("清理完成", `已释放 ${fmt(freed)}${errors.length ? `，${errors.length} 项未能删除` : "。"} 即将重新扫描。`);
      } else {
        toast("没有释放空间", errors[0]?.error || "所选项目未能删除，请检查权限。", "error");
      }
      loadHistory();
      state.selected.clear();
      updateSelection();
      await sleep(900);
      startScan(false);
    } catch (err) {
      state.cleaning = false;
      el.dialogError.textContent = err.message || String(err);
      el.dialogError.hidden = false;
      el.dialogConfirm.textContent = "永久清理";
      el.dialogConfirm.disabled = el.confirmWord.value !== "DELETE";
      updateSelection();
    }
  }

  // ---------------------------------------------------------------- history --
  async function loadHistory() {
    try {
      const res = await api("/api/history?limit=24");
      state.history = res;
      el.historySource.textContent = res.db_connected ? "POSTGRESQL" : "LOCAL JSONL";
      el.dbCallout.hidden = Boolean(res.db_connected);
      renderHistory(res.events || []);
    } catch (err) {
      el.historyList.innerHTML = `<div class="history-empty">历史读取失败：${esc(err.message)}</div>`;
    }
  }

  function renderHistory(events) {
    if (!events.length) {
      el.historyList.innerHTML = `<div class="empty-state card compact"><h3>暂无历史</h3><p>扫描和清理完成后，会在这里留下时间线。</p></div>`;
      return;
    }
    el.historyList.innerHTML = events.slice(0, 20).map((event) => {
      const isClean = event.type === "cleanup";
      const isLaunch = event.trigger === "launch";
      const isAuto = event.trigger === "auto";
      const date = formatDate(event.created_at);
      if (isClean) {
        const count = (event.items || []).length;
        const errCount = (event.errors || []).length;
        const failed = !event.freed_bytes && errCount > 0;
        return `
          <div class="history-row">
            <div class="history-icon clean ${failed ? "failed" : ""}">${iconFor("clean")}</div>
            <div class="history-main">
              <div class="history-title">清理了 ${count} 项缓存${errCount ? ` · ${errCount} 项失败` : ""}</div>
              <div class="history-sub">${date} · ${event.scan_id ? "关联扫描 " + esc(String(event.scan_id).slice(-12)) : "手动清理"}</div>
            </div>
            <div class="history-value clean ${failed ? "failed" : ""}"><strong>${failed ? "0 B" : "+" + fmt(event.freed_bytes)}</strong><span>${failed ? "未释放" : "已释放"}</span></div>
          </div>
        `;
      }
      const disk = event.disk_free ? `可用 ${fmt(event.disk_free)}` : "";
      return `
        <div class="history-row">
          <div class="history-icon">${iconFor("scan")}</div>
          <div class="history-main">
            <div class="history-title">${isAuto ? "自动扫描" : (isLaunch ? "启动扫描" : "手动扫描")} · 隐藏数据 ${fmt(event.system_data_bytes)}${isLaunch ? '<span class="history-tag launch">启动</span>' : ""}</div>
            <div class="history-sub">${date} · ${esc(event.macos || "macOS")} · ${disk}</div>
          </div>
          <div class="history-value"><strong>${fmt(event.cleanable_bytes)}</strong><span>可清理</span></div>
        </div>
      `;
    }).join("");
  }

  async function startDb() {
    const button = $("#start-db");
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "正在启动…";
    try {
      const res = await api("/api/db/start", { method: "POST", body: {} });
      if (!res.ok) throw new Error(res.error || "数据库启动失败");
      updateDbPill(res.status || { connected: true });
      toast("历史数据库已启动", "后续扫描和清理会写入 PostgreSQL。");
      loadHistory();
    } catch (err) {
      toast("数据库启动失败", err.message || String(err), "error");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  function scheduleDbProbe(attempt) {
    if (attempt >= 6) return;
    setTimeout(async () => {
      try {
        const health = await api("/api/health");
        updateDbPill(health.db);
        if (health.db?.connected) {
          loadHistory();
          return;
        }
      } catch (_) { /* keep the local fallback UI */ }
      scheduleDbProbe(attempt + 1);
    }, attempt === 0 ? 3500 : 5000);
  }

  function updateDbPill(db) {
    state.db = db || {};
    if (state.db.connected) {
      el.dbDot.className = "status-dot online";
      el.dbLabel.textContent = "历史库在线";
      el.dbPill.title = `PostgreSQL ${state.db.version || ""} · ${state.db.host || ""}`;
    } else {
      el.dbDot.className = "status-dot warn";
      el.dbLabel.textContent = "历史库离线";
      el.dbPill.title = state.db.error || "本地记录模式";
    }
  }

  function showDbInfo() {
    toast("PostgreSQL 已连接", `${state.db.version || "PostgreSQL"} · ${state.db.host} / ${state.db.database}`);
  }

  // ---------------------------------------------------------------- actions --
  async function revealPath(path) {
    try {
      await api("/api/reveal", { method: "POST", body: { path } });
    } catch (err) {
      toast("无法打开 Finder", err.message || String(err), "error");
    }
  }

  async function openStorageSettings() {
    try {
      await api("/api/open-storage-settings", { method: "POST", body: {} });
    } catch (err) {
      toast("无法打开系统设置", err.message || String(err), "error");
    }
  }

  // ------------------------------------------------------------------ utils --
  function fmt(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${Math.round(n)} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = n;
    let idx = -1;
    do { value /= 1024; idx += 1; } while (value >= 1024 && idx < units.length - 1);
    return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[idx]}`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  function timeAgo(value) {
    if (!value) return "—";
    const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
  }

  function refreshRelativeTimes() {
    if (state.scan) {
      $("#stat-time").textContent = timeAgo(state.scan.created_at);
    }
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  let toastTimer = null;
  function toast(title, body, type = "ok") {
    el.toastTitle.textContent = title;
    el.toastBody.textContent = body || "";
    el.toast.classList.toggle("error", type === "error");
    el.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 5200);
  }
  function hideToast() {
    el.toast.classList.remove("show");
    clearTimeout(toastTimer);
  }

  function iconFor(kind) {
    const icons = {
      chat: '<svg viewBox="0 0 20 20" fill="none"><path d="M4 5.5h12v7H9l-4 3v-3H4v-7Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M7 8.5h6M7 10.6h3.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
      app: '<svg viewBox="0 0 20 20" fill="none"><rect x="3.5" y="3.5" width="13" height="13" rx="3" stroke="currentColor" stroke-width="1.3"/><path d="M7 7h6M7 10h6M7 13h3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
      cache: '<svg viewBox="0 0 20 20" fill="none"><ellipse cx="10" cy="5.5" rx="6" ry="2.5" stroke="currentColor" stroke-width="1.2"/><path d="M4 5.5v4c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5v-4M4 9.5v4c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5v-4" stroke="currentColor" stroke-width="1.2"/></svg>',
      code: '<svg viewBox="0 0 20 20" fill="none"><path d="m7 6-4 4 4 4M13 6l4 4-4 4M11.2 4.8 8.8 15.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      docker: '<svg viewBox="0 0 20 20" fill="none"><path d="M3 9.5h14c-.4 4-3 6-7.2 6-3.8 0-6.1-2-6.8-6Z" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 9.5V7h2.4v2.5M8.8 9.5V7h2.4v2.5M12.1 9.5V7h2.4v2.5M8.8 6.3V3.8h2.4v2.5" stroke="currentColor" stroke-width="1.1"/></svg>',
      backup: '<svg viewBox="0 0 20 20" fill="none"><rect x="6" y="2.8" width="8" height="14.4" rx="2" stroke="currentColor" stroke-width="1.2"/><path d="M8.5 5.5h3M8.8 14.6h2.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
      media: '<svg viewBox="0 0 20 20" fill="none"><rect x="3" y="4.5" width="14" height="11" rx="2.5" stroke="currentColor" stroke-width="1.2"/><path d="m8.2 8 4 2-4 2V8Z" fill="currentColor"/></svg>',
      memory: '<svg viewBox="0 0 20 20" fill="none"><rect x="3.5" y="5" width="13" height="10" rx="2" stroke="currentColor" stroke-width="1.2"/><path d="M6.5 8v4M9.8 8v4M13.1 8v4M5 5V3M9 5V3M13 5V3M5 17v-2M9 17v-2M13 17v-2" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>',
      logs: '<svg viewBox="0 0 20 20" fill="none"><path d="M5 3h7l3 3v11H5V3Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M12 3v3h3M7.5 9h5M7.5 11.5h5M7.5 14h3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>',
      system: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="3" stroke="currentColor" stroke-width="1.2"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
      download: '<svg viewBox="0 0 20 20" fill="none"><path d="M10 3v9M6.5 9.5 10 13l3.5-3.5M4 16h12" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      trash: '<svg viewBox="0 0 20 20" fill="none"><path d="M4.5 6h11M8 3.5h4M6 6l.8 10h6.4L14 6M8.5 9v4.5M11.5 9v4.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      scan: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="6.5" stroke="currentColor" stroke-width="1.2"/><path d="M10 6.5V10l2.5 1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M2.8 10a7.2 7.2 0 0 1 .4-2.4M17.2 10c0 .8-.1 1.6-.4 2.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
      clean: '<svg viewBox="0 0 20 20" fill="none"><path d="m4 13 6-8 6 8-6 3-6-3Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="m7.5 8.5 2.5 2 2.5-2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
    };
    return icons[kind] || icons.app;
  }
})();
