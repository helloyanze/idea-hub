/* =================== Idea Hub 前端（设计稿 v1.0 还原 + 真实 API） =================== */
const $ = s => document.querySelector(s);
const api = {
  async req(method, url, body) {
    const opt = {method, headers: {'Content-Type': 'application/json'}};
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(url, opt);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  get: (p) => api.req('GET', p),
  post: (p, b) => api.req('POST', p, b ?? {}),
  patch: (p, b) => api.req('PATCH', p, b),
  put: (p, b) => api.req('PUT', p, b),
  delete: (p) => api.req('DELETE', p),
};

let TASKS = [];
let SOURCES = [];
let TAGS = [];
let TARGETS = [];
let currentTarget = null;
let currentTag = '';
const selected = new Set();
let curId = null;

const COL_NAMES = {todo: '待办', waiting: '等待', in_progress: '进行中', done: '已完成'};
const COLS = [
  {key: "todo", name: "待办", hint: "精选区 · 配额 10 · 高分优先 · 可拖拽 / 点「确认」入等待", accent: true},
  {key: "waiting", name: "等待", hint: "已确认 · 可拖入进行中"},
  {key: "in_progress", name: "进行中", hint: "执行中 · 可拖入完成区"},
  {key: "done", name: "已完成", hint: "有产出物 · 点「确认」归档 / 可回看"},
];

/* =================== 工具 =================== */
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
const scoreClass = s => s == null ? "none" : s >= 8 ? "green" : s >= 6 ? "yellow" : "red";
const scoreText = s => s == null ? "--" : s;

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  const icon = kind === "ok" ? '<svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>'
    : kind === "err" ? '<svg viewBox="0 0 24 24"><path d="M12 8v5m0 3h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>' : '';
  t.innerHTML = icon + msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.opacity = 0; t.style.transform = "translateY(8px)"; setTimeout(() => t.remove(), 200); }, 2600);
}

/* =================== 数据加载 =================== */
async function loadAll() {
  const [tasks, stats, tags, targets, sources, settings] = await Promise.all([
    api.get('/api/tasks'), api.get('/api/stats'), api.get('/api/tags'),
    api.get('/api/targets'), api.get('/api/sources'), api.get('/api/settings'),
  ]);
  TASKS = tasks.items;
  TAGS = tags.items;
  TARGETS = targets.items;
  SOURCES = sources.items;
  renderTargets();
  renderTagFilter();
  const autoRun = (settings.items || []).find(x => x.key === 'auto_run');
  $("#autoSwitch").classList.toggle('on', autoRun ? autoRun.value !== '0' : true);
  renderBoard(stats);
}

function renderTargets() {
  const sel = $("#goalSelect");
  sel.innerHTML = TARGETS.map(t => `<option value="${t.id}" ${t.is_active ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
  currentTarget = TARGETS.find(t => t.is_active)?.id ?? null;
  sel.onchange = async () => {
    await api.post(`/api/targets/${sel.value}/activate`);
    currentTarget = Number(sel.value);
    await loadAll();
  };
}

function renderTagFilter() {
  const sel = $("#tagFilter");
  sel.innerHTML = '<option value="">全部标签</option>' +
    TAGS.map(t => `<option value="${t.id}">${esc(t.name)}${t.is_active ? '' : ' (停用)'}</option>`).join('');
  sel.value = currentTag;
  sel.onchange = () => { currentTag = sel.value; renderBoard(); };
}

/* =================== 看板渲染 =================== */
function renderBoard(stats) {
  const board = $("#board");
  board.innerHTML = "";
  const q = $("#searchInput").value.trim().toLowerCase();
  const tf = currentTag;

  COLS.forEach(col => {
    const data = TASKS
      .filter(t => t.status === col.key)
      .filter(t => !tf || (t.tags || []).some(x => String(x.id) === tf))
      .filter(t => !q || `${t.title} ${t.idea_summary || ''} ${(t.tags || []).map(x => x.name).join(' ')}`.toLowerCase().includes(q));

    const el = document.createElement("section");
    el.className = `col ${col.key}${col.accent ? " todo-accent" : ""}`;
    el.dataset.col = col.key;
    el.innerHTML = `
      <div class="col-head" data-col="${col.key}">
        <div class="col-title"><span class="bar"></span>${col.name}</div>
        <span class="col-count">${data.length}</span>
      </div>
      <div class="col-hint">${col.hint}</div>
      <div class="cards"></div>`;
    const cards = el.querySelector(".cards");

    cards.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; el.classList.add("drop-target"); });
    cards.addEventListener("dragleave", e => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop-target"); });
    cards.addEventListener("drop", async e => {
      e.preventDefault(); el.classList.remove("drop-target");
      const id = +e.dataTransfer.getData("text/plain");
      const t = TASKS.find(x => x.id === id);
      if (!t || t.status === col.key) return;
      await moveTask(id, col.key);
      toast(`已移动到${col.name}`, "ok");
    });

    if (data.length === 0) {
      const e = document.createElement("div");
      e.className = "empty";
      e.innerHTML = col.key === "todo" && !q && !tf
        ? "<b>配额已满 · 自动留档</b>低分 idea 已转入留档列"
        : "<b>暂无任务</b>" + (q ? "无匹配结果" : "拖动卡片到此列");
      cards.appendChild(e);
    } else {
      data.forEach(t => cards.appendChild(cardEl(t)));
    }
    board.appendChild(el);
  });
  renderStats(stats);
  updateBatchBar();
}

async function moveTask(id, toStatus) {
  await api.post(`/api/tasks/${id}/move`, {to_status: toStatus});
  const t = TASKS.find(x => x.id === id);
  if (t) t.status = toStatus;
  if (curId === id) { const cur = TASKS.find(x => x.id === id); if (cur) openDrawer(id); }
  renderBoard();
}

function cardEl(t) {
  const c = document.createElement("article");
  c.className = "card" + (t.status === "done" ? " done" : "") + (selected.has(t.id) ? " sel" : "");
  c.dataset.id = t.id;
  c.draggable = true;
  const tags = (t.tags || []).map(x => `<span class="tag" data-tag="${x.id}">${esc(x.name)}</span>`).join("");
  const check = '<svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>';
  const back = '<svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>';
  const archive = '<svg viewBox="0 0 24 24"><path d="M3 8h18v12H3z"/><path d="M3 8l2-4h14l2 4"/><path d="M10 12h4"/></svg>';
  const del = '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>';
  let q = "";
  if (t.status === "todo") q += `<button class="qbtn confirm" title="确认并加入等待">${check}</button>`;
  if (t.status === "waiting") q += `<button class="qbtn back" title="撤回至待办">${back}</button>`;
  if (t.status === "in_progress") q += `<button class="qbtn confirm" title="标记完成">${check}</button>`;
  if (t.status === "done") {
    q += `<button class="qbtn confirm" title="确认归档">${archive}</button>`;
    q += `<button class="qbtn back" title="退回待办重做">${back}</button>`;
  }
  q += `<button class="qbtn del" title="删除">${del}</button>`;
  c.innerHTML = `
    <label class="pick"><input type="checkbox" data-pick="${t.id}" ${selected.has(t.id) ? "checked" : ""}></label>
    <div class="quick">${q}</div>
    <div class="title">${esc(t.title)}</div>
    <div class="meta">
      <span class="score ${scoreClass(t.feasibility_score)}">${scoreText(t.feasibility_score)}分</span>
      ${tags}
    </div>
    <div class="summary">${esc(t.idea_summary || "")}</div>`;

  c.addEventListener("click", e => {
    if (e.target.closest(".quick") || e.target.closest(".pick")) return;
    if (e.target.closest(".tag")) { currentTag = e.target.dataset.tag; renderBoard(); renderTagFilter(); return; }
    openDrawer(t.id);
  });
  const bind = (sel, fn) => { const b = c.querySelector(sel); if (b) b.addEventListener("click", e => { e.stopPropagation(); fn(); }); };
  bind(".confirm", () => t.status === "todo" ? confirmTask(t.id) : t.status === "in_progress" ? completeTask(t.id) : archiveDoneTask(t.id));
  bind(".back", () => t.status === "waiting" ? backTask(t.id) : redoTask(t.id));
  bind(".del", () => delTask(t.id));
  const pick = c.querySelector("[data-pick]");
  pick.addEventListener("change", e => {
    e.stopPropagation();
    if (e.target.checked) selected.add(t.id); else selected.delete(t.id);
    c.classList.toggle("sel", e.target.checked);
    updateBatchBar();
  });
  c.addEventListener("dragstart", e => {
    if (e.target.closest(".pick")) { e.preventDefault(); return; }
    e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; c.classList.add("dragging");
  });
  c.addEventListener("dragend", () => {
    c.classList.remove("dragging");
    document.querySelectorAll(".col.drop-target").forEach(x => x.classList.remove("drop-target"));
  });
  return c;
}

function renderStats(stats) {
  const s = stats || {todo: 0, archived: 0, waiting: 0, in_progress: 0, done: 0};
  document.querySelectorAll("#stats .stat").forEach(el => {
    el.querySelector("b").textContent = s[el.dataset.col] ?? 0;
  });
}

function updateBatchBar() {
  const n = [...selected].filter(id => TASKS.some(t => t.id === id)).length;
  $("#batchCount").textContent = `已选 ${n} 项`;
  $("#batchBar").classList.toggle("show", n > 0);
}

/* =================== 操作 =================== */
async function confirmTask(id) {  // todo → waiting
  await moveTask(id, "waiting");
  toast(`已确认，加入等待区`, "ok");
  if (curId === id) closeDrawer();
}
async function backTask(id) {  // waiting → todo
  await moveTask(id, "todo");
  toast("已撤回至待办");
  if (curId === id) closeDrawer();
}
async function completeTask(id) {  // in_progress → done
  await moveTask(id, "done");
  toast("已标记完成", "ok");
  if (curId === id) closeDrawer();
}
async function archiveDoneTask(id) {  // done → archived（完成确认归档）
  await moveTask(id, "archived");
  toast("已归档（完成存档）", "ok");
  if (curId === id) closeDrawer();
}
async function redoTask(id) {  // done → todo
  await moveTask(id, "todo");
  toast("已退回待办重做");
  if (curId === id) closeDrawer();
}
async function delTask(id) {
  const t = TASKS.find(x => x.id === id);
  if (!t) return;
  if (!confirm(`确认删除「${t.title.slice(0, 16)}…」？此操作不可恢复（连同构思与产出）`)) return;
  await api.delete(`/api/tasks/${id}`);
  TASKS = TASKS.filter(x => x.id !== id);
  selected.delete(id);
  renderBoard();
  toast("已删除", "err");
  if (curId === id) closeDrawer();
}

/* =================== 抽屉 =================== */
async function openDrawer(id) {
  const t = await api.get(`/api/tasks/${id}`);
  if (!t) return;
  curId = id;
  $("#drTitle").value = t.title || "";
  const bd = Object.entries(JSON.parse(t.score_breakdown || "{}") || {}).map(([k, v]) => `
    <div class="bd-row"><span>${esc(k)}</span><span class="bd-bar"><i style="width:${Math.min(100, v * 10)}%"></i></span><span class="bd-val">${v}</span></div>`).join("");
  const tags = (t.tags || []).map(x => `<span class="tag">${esc(x.name)}</span>`).join("") || '<span class="tag gray">无</span>';
  const relHtml = (t.hot_item_id ? `<a href="#"><svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>关联热点 #${t.hot_item_id}</a>` : "")
    + (t.output_path ? `<a href="/outputs/${esc(t.output_path.replace(/^outputs\//, ''))}" target="_blank"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>产出文件</a>` : "");

  $("#drBody").innerHTML = `
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6z"/></svg>可行性评分</h4>
      <div class="score-row">
        <input type="range" id="drScore" min="1" max="10" value="${t.feasibility_score ?? 5}">
        <span class="score-big ${scoreClass(t.feasibility_score)}" id="drScoreBig">${scoreText(t.feasibility_score)}</span>
      </div>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M20.6 13.4 12 22l-9-9V3h10l7.6 7.4a2 2 0 0 1 0 2.8z"/></svg>标签</h4>
      <div class="tags-row">${tags}<span class="add" id="drAddTag">+ 添加标签</span></div>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg>摘要</h4>
      <textarea class="field" id="drSummary">${esc(t.idea_summary || "")}</textarea>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>构思全文</h4>
      <details open><summary>展开 / 收起</summary>
        <div class="md">${md(t.idea_full)}</div>
      </details>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 14l3-4 3 3 4-6"/></svg>评分明细</h4>
      <div class="breakdown">${bd || '<span style="color:var(--text-3);font-size:12px">无评分明细</span>'}</div>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>关联与产出</h4>
      <div class="rel">${relHtml || '<span style="color:var(--text-3);font-size:12px">暂无关联</span>'}</div>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h6"/></svg>备注</h4>
      <textarea class="field" id="drNotes" placeholder="写点想法…">${esc(t.notes || "")}</textarea>
    </div>`;

  const sc = $("#drScore"), big = $("#drScoreBig");
  sc.addEventListener("input", () => { big.textContent = sc.value; big.className = "score-big " + scoreClass(+sc.value); });

  $("#drAddTag").addEventListener("click", async () => {
    const name = prompt("输入标签名（不存在将自动创建）：");
    if (!name) return;
    await api.post(`/api/tasks/${id}/tags`, {name});
    await loadAll();
    openDrawer(id);
  });

  // 底部操作按钮随状态变化
  const runBtn = $("#drRun");
  const backIcon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>';
  const checkIcon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 13l4 4L19 7"/></svg>';
  const archiveIcon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8h18v12H3z"/><path d="M3 8l2-4h14l2 4"/><path d="M10 12h4"/></svg>';
  const playIcon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 3l14 9-14 9V3z"/></svg>';
  runBtn.style.display = "";
  if (t.status === "todo") { runBtn.innerHTML = checkIcon + '确认并加入等待'; runBtn.onclick = () => confirmTask(id); }
  else if (t.status === "waiting") { runBtn.innerHTML = backIcon + '撤回至待办'; runBtn.onclick = () => backTask(id); }
  else if (t.status === "in_progress") { runBtn.innerHTML = checkIcon + '标记完成'; runBtn.onclick = () => completeTask(id); }
  else if (t.status === "done") { runBtn.innerHTML = archiveIcon + '确认归档'; runBtn.onclick = () => archiveDoneTask(id); }
  else if (t.status === "archived") { runBtn.innerHTML = backIcon + '恢复至待办'; runBtn.onclick = () => { moveTask(id, "todo"); toast("已恢复到待办", "ok"); closeDrawer(); }; }
  else runBtn.style.display = "none";

  $("#drawer").classList.add("show");
  $("#drawerScrim").classList.add("show");
  $("#drawer").setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  $("#drawer").classList.remove("show");
  $("#drawerScrim").classList.remove("show");
  $("#drawer").setAttribute("aria-hidden", "true");
  curId = null;
}

function md(s) {
  return (s || "*暂无构思文件*")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^# (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n\n/g, "<br>");
}

/* =================== 弹窗 =================== */
function openModal(sel) { $(sel).classList.add("show"); }
function closeModal(sel) { $(sel).classList.remove("show"); }

function renderSources() {
  const list = $("#sourceList");
  list.innerHTML = "";
  const typeMap = {hotlist: ["type-hotlist", "热榜"], rss: ["type-rss", "RSS"], "github-trending": ["type-github", "GitHub"], hackernews: ["type-hn", "HN"]};
  SOURCES.forEach(s => {
    const [cls, label] = typeMap[s.type] || ["type-rss", s.type];
    const row = document.createElement("div");
    row.className = "src-row";
    row.innerHTML = `
      <span class="type-badge ${cls}">${label}</span>
      <div class="grow"><div class="name">${esc(s.name)}</div>
        <div class="kw">${s.keywords ? "关键词：" + esc(s.keywords) : "无关键词过滤"}</div></div>
      <div class="switch ${s.enabled ? "on" : ""}" data-toggle><span class="track"></span></div>
      <button class="mini danger" data-del>删除</button>`;
    row.querySelector("[data-toggle]").addEventListener("click", async e => {
      await api.post(`/api/sources/${s.id}/toggle`);
      s.enabled = !s.enabled;
      e.currentTarget.classList.toggle("on", s.enabled);
      toast(`${s.name} 已${s.enabled ? "启用" : "停用"}`);
    });
    row.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm(`删除来源「${s.name}」？其历史热点将一并删除`)) return;
      await api.delete(`/api/sources/${s.id}`);
      SOURCES = SOURCES.filter(x => x.id !== s.id);
      renderSources();
      toast("已删除来源", "err");
    });
    list.appendChild(row);
  });
  const add = document.createElement("div");
  add.className = "add-card";
  add.innerHTML = `
    <select id="src-type"><option value="hotlist">hotlist 热榜</option><option value="rss">rss</option><option value="github-trending">github-trending</option><option value="hackernews">hackernews</option></select>
    <input id="src-name" placeholder="名称">
    <input id="src-url" class="full" placeholder="URL（热榜 API 或 RSS 地址）">
    <input id="src-items-path" placeholder="条目路径（默认 data）">
    <input id="src-title-field" placeholder="标题字段（默认 title）">
    <input id="src-keywords" class="full" placeholder="关键词白名单（逗号分隔，可留空）">
    <button class="btn primary" id="src-add-btn" style="grid-column:1/-1;justify-content:center">添加</button>`;
  add.querySelector("#src-add-btn").addEventListener("click", async () => {
    const name = add.querySelector("#src-name").value.trim();
    const url = add.querySelector("#src-url").value.trim();
    if (!name || !url) { toast("名称和 URL 必填", "err"); return; }
    await api.post("/api/sources", {
      type: add.querySelector("#src-type").value, name, url,
      items_path: add.querySelector("#src-items-path").value || "data",
      title_field: add.querySelector("#src-title-field").value || "title",
      keywords: add.querySelector("#src-keywords").value || "",
    });
    toast("已添加来源", "ok");
    SOURCES = (await api.get('/api/sources')).items;
    renderSources();
  });
  list.appendChild(add);
}

function renderTags() {
  const list = $("#tagList");
  list.innerHTML = "";
  TAGS.forEach(t => {
    const row = document.createElement("div");
    row.className = "tag-row";
    const n = TASKS.filter(x => (x.tags || []).some(tg => tg.id === t.id)).length;
    row.innerHTML = `
      <div class="grow"><div class="name">${esc(t.name)} <span style="color:var(--text-3);font-size:11px">· ${n} 个任务</span></div>
        <div style="font-size:11px;color:var(--text-3)">${esc(t.description || "")}</div></div>
      <div class="switch ${t.is_active ? "on" : ""}" data-toggle><span class="track"></span></div>
      <button class="mini danger" data-del>删除</button>`;
    row.querySelector("[data-toggle]").addEventListener("click", async e => {
      await api.post(`/api/tags/${t.id}/toggle`);
      t.is_active = !t.is_active;
      e.currentTarget.classList.toggle("on", t.is_active);
    });
    row.querySelector("[data-del]").addEventListener("click", async () => {
      await api.delete(`/api/tags/${t.id}`);
      TAGS = TAGS.filter(x => x.id !== t.id);
      renderTags();
    });
    list.appendChild(row);
  });
  const add = document.createElement("div");
  add.className = "add-card";
  add.innerHTML = `<input id="tag-name" placeholder="标签名"><input id="tag-desc" placeholder="描述（可选）">
    <button class="btn primary" id="tag-add-btn" style="grid-column:1/-1;justify-content:center">添加</button>`;
  add.querySelector("#tag-add-btn").addEventListener("click", async () => {
    const name = add.querySelector("#tag-name").value.trim();
    if (!name) { toast("标签名必填", "err"); return; }
    await api.post("/api/tags", {name, description: add.querySelector("#tag-desc").value || ""});
    TAGS = (await api.get('/api/tags')).items;
    renderTagFilter();
    renderTags();
  });
  list.appendChild(add);
}

function renderArchived() {
  const list = $("#archivedList");
  list.innerHTML = "";
  const data = TASKS.filter(t => t.status === "archived");
  $("#archivedHint").textContent = data.length ? `共 ${data.length} 条 · 悬停可恢复至待办` : "";
  if (data.length === 0) {
    list.innerHTML = '<div class="empty"><b>留档为空</b>低分 idea（6-7 分）与完成归档的任务会出现在这里</div>';
    return;
  }
  data.forEach(t => {
    const c = document.createElement("article");
    c.className = "card";
    const tags = (t.tags || []).map(x => `<span class="tag">${esc(x.name)}</span>`).join("");
    c.innerHTML = `
      <div class="quick">
        <button class="qbtn restore" title="恢复至待办"><svg viewBox="0 0 24 24"><path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 3-7.7L3 8"/></svg></button>
        <button class="qbtn del" title="删除"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg></button>
      </div>
      <div class="title">${esc(t.title)}</div>
      <div class="meta"><span class="score ${scoreClass(t.feasibility_score)}">${scoreText(t.feasibility_score)}分</span>${tags}</div>
      <div class="summary">${esc(t.idea_summary || "")}</div>`;
    c.addEventListener("click", e => {
      if (e.target.closest(".quick")) return;
      openDrawer(t.id);
    });
    c.querySelector(".restore").addEventListener("click", async e => {
      e.stopPropagation();
      await moveTask(t.id, "todo");
      renderArchived();
      toast("已恢复到待办", "ok");
    });
    c.querySelector(".del").addEventListener("click", e => {
      e.stopPropagation();
      delTask(t.id);
      renderArchived();
    });
    list.appendChild(c);
  });
}

/* =================== 事件绑定 =================== */
$("#tagFilter").addEventListener("change", renderBoard);
$("#searchInput").addEventListener("input", renderBoard);
$("#drClose").addEventListener("click", closeDrawer);
$("#drawerScrim").addEventListener("click", closeDrawer);
$("#drSave").addEventListener("click", async () => {
  const t = TASKS.find(x => x.id === curId);
  if (!t) return;
  await api.patch(`/api/tasks/${curId}`, {
    title: $("#drTitle").value,
    idea_summary: $("#drSummary").value,
    feasibility_score: Number($("#drScore").value),
    notes: $("#drNotes").value,
  });
  t.title = $("#drTitle").value;
  t.idea_summary = $("#drSummary").value;
  t.feasibility_score = Number($("#drScore").value);
  t.notes = $("#drNotes").value;
  renderBoard();
  toast("修改已保存", "ok");
  closeDrawer();
});
$("#drDel").addEventListener("click", () => curId && delTask(curId));
$("#openSource").addEventListener("click", () => { renderSources(); openModal("#sourceModal"); });
$("#openTag").addEventListener("click", () => { renderTags(); openModal("#tagModal"); });
document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", e => {
  e.target.closest(".modal").classList.remove("show");
}));

$("#autoSwitch").addEventListener("click", async e => {
  const on = e.currentTarget.classList.toggle("on");
  await api.put("/api/settings", {key: "auto_run", value: on ? "1" : "0"});
  toast(`每晚自动运行已${on ? "开启" : "关闭"}`);
});

$("#collectBtn").addEventListener("click", async () => {
  const b = $("#collectBtn");
  b.disabled = true;
  b.innerHTML = '收集中…';
  try {
    const r = await api.post("/api/collect");
    toast(`收集完成：新增 ${r.collected}，丢弃 ${r.discarded}，待复核 ${r.review}`, "ok");
    if (r.errors && r.errors.length) toast(`部分来源失败：${r.errors[0]}`, "err");
  } catch (err) {
    toast("收集失败：" + err.message, "err");
  } finally {
    b.disabled = false;
    b.innerHTML = '<svg viewBox="0 0 24 24" style="width:15px;height:15px;stroke:#fff;stroke-width:1.8;fill:none"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"/></svg>立即收集';
    await loadAll();
  }
});

$("#themeBtn").addEventListener("click", () => {
  const r = document.documentElement;
  const dark = r.dataset.theme === "dark";
  r.dataset.theme = dark ? "light" : "dark";
  $("#themeIcon").innerHTML = dark
    ? '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>'
    : '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
});

document.querySelectorAll("#stats .stat").forEach(el => el.addEventListener("click", () => {
  if (el.dataset.col === "archived") { renderArchived(); openModal("#archivedModal"); return; }
  const col = document.querySelector(`.col[data-col="${el.dataset.col}"]`);
  if (col) col.scrollIntoView({behavior: "smooth", inline: "center", block: "nearest"});
}));

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeDrawer();
    document.querySelectorAll(".modal.show").forEach(m => m.classList.remove("show"));
  }
  if (e.key === "/" && document.activeElement !== $("#searchInput")) {
    e.preventDefault();
    $("#searchInput").focus();
  }
});

/* 批量操作 */
$("#batchDel").addEventListener("click", async () => {
  if (!selected.size) return;
  if (!confirm(`确认删除选中的 ${selected.size} 项？`)) return;
  for (const id of [...selected]) {
    try { await api.delete(`/api/tasks/${id}`); } catch (e) { /* 单个失败继续 */ }
  }
  selected.clear();
  await loadAll();
  toast("已批量删除", "err");
});
$("#batchArchive").addEventListener("click", async () => {
  if (!selected.size) return;
  for (const id of [...selected]) { try { await moveTask(id, "archived"); } catch (e) {} }
  selected.clear();
  toast("已批量归档到留档", "ok");
});
$("#batchTodo").addEventListener("click", async () => {
  if (!selected.size) return;
  for (const id of [...selected]) { try { await moveTask(id, "todo"); } catch (e) {} }
  selected.clear();
  toast("已批量退回待办");
});
$("#batchClear").addEventListener("click", () => { selected.clear(); updateBatchBar(); renderBoard(); });

/* =================== 启动：先骨架屏，再加载 =================== */
function showSkeleton() {
  const board = $("#board");
  board.innerHTML = "";
  COLS.forEach(col => {
    const el = document.createElement("section");
    el.className = "col " + col.key;
    el.innerHTML = `<div class="col-head"><div class="col-title"><span class="bar"></span>${col.name}</div><span class="col-count">–</span></div><div class="cards">${'<div class="skel"><div class="l" style="width:90%"></div><div class="l" style="width:60%"></div><div class="l" style="width:75%"></div></div>'.repeat(3)}</div>`;
    board.appendChild(el);
  });
}
showSkeleton();
loadAll().catch(err => {
  document.querySelectorAll(".skel").forEach(s => s.remove());
  document.querySelectorAll(".col .cards").forEach(c => {
    c.innerHTML = '<div class="empty"><b>加载失败</b>请确认 SSH 隧道已建立，然后刷新页面</div>';
  });
  console.error(err);
});
