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

/* 内容类型与复杂标记徽标 */
const ctMap = {short: "短文", long: "长文", video_script: "视频"};
function badgeHtml(t) {
  let h = `<span class="ct-badge ct-${t.content_type || 'long'}">${ctMap[t.content_type] || '长文'}</span>`;
  if (t.is_complex) h += `<span class="ct-badge complex">复杂</span>`;
  return h;
}

/* 设置项缓存：key -> value（loadAll 填充，弹窗 / 顶部开关共用） */
let SETTINGS = {};

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
  SETTINGS = {};
  (settings.items || []).forEach(x => { SETTINGS[x.key] = x.value; });
  renderTargets();
  renderTagFilter();
  const autoRun = (settings.items || []).find(x => x.key === 'auto_run');
  $("#autoSwitch").classList.toggle('on', autoRun ? autoRun.value !== '0' : true);
  renderBoard(stats);
  refreshHealth();
  refreshNotifications();
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
  const play = '<svg viewBox="0 0 24 24"><path d="M5 3l14 9-14 9V3z"/></svg>';
  let q = "";
  if (t.status === "todo") q += `<button class="qbtn confirm" title="确认并加入等待">${check}</button>`;
  if (t.status === "waiting") q += `<button class="qbtn run" title="立即执行">${play}</button>`;
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
      ${badgeHtml(t)}
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
  bind(".run", () => runNow(t.id));
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
async function runNow(id) {  // waiting → 插队执行（execute_requests 入队，下一轮调度优先处理）
  await api.post(`/api/tasks/${id}/execute`);
  toast("已加入执行队列，下一轮调度优先处理", "ok");
  await loadAll();
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
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/><circle cx="12" cy="12" r="3"/></svg>执行设置</h4>
      <div class="set-grid">
        <label class="set-field">内容类型
          <select id="drContentType">
            <option value="short">短文</option>
            <option value="long">长文</option>
            <option value="video_script">视频脚本</option>
          </select>
        </label>
        <label class="check-row">
          <input type="checkbox" id="drComplex" ${t.is_complex ? "checked" : ""}>
          <span>复杂任务<span class="hint">判定标准：需联网调研 / 多源交叉验证 / 深度长文 2000 字以上</span></span>
        </label>
        <label class="set-field">打回意见（redo_note）
          <textarea class="field" id="drRedoNote" placeholder="记录打回 / 重做原因…">${esc(t.redo_note || "")}</textarea>
        </label>
      </div>
    </div>
    <div class="sec">
      <h4><svg class="ic" viewBox="0 0 24 24"><path d="M12 8v5m0 3h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>执行情况</h4>
      <div class="fail-box">
        <div class="fail-count">失败次数：<b id="drFailCount">${t.fail_count ?? 0}</b></div>
        <div class="fail-reason">最近失败原因：${esc(t.last_fail_reason || "无")}</div>
        <button class="btn mini danger" id="drResetFail">重置失败计数</button>
      </div>
      <div class="rel" id="drVersions" style="margin-top:9px"></div>
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

  $("#drContentType").value = t.content_type || "long";
  $("#drResetFail").addEventListener("click", async () => {
    try {
      await api.post(`/api/tasks/${id}/reset-failures`);
      toast("失败计数已重置", "ok");
      openDrawer(id);
    } catch (err) {
      toast("重置失败：" + err.message, "err");
    }
  });
  renderVersions(t);

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

/* 探测产出目录中的历史版本文件（output_vN.md），存在则给出下载链接 */
async function renderVersions(t) {
  const box = $("#drVersions");
  if (!box) return;
  box.innerHTML = "";
  if (!t.output_path) return;
  const dir = t.output_path.replace(/^outputs\//, "").replace(/\/[^/]+$/, "");
  const links = [];
  for (let n = 2; n <= 10; n++) {
    const u = `/outputs/${dir}/output_v${n}.md`;
    try {
      const r = await fetch(u, {method: "HEAD"});
      if (r.ok) links.push(`<a href="${u}" target="_blank">历史版本 v${n}</a>`);
      else if (r.status === 404) break;
    } catch (e) { break; }
  }
  if (links.length) {
    box.innerHTML = '<span style="color:var(--text-3);font-size:12px;padding:2px 4px">版本下载：</span>' + links.join("");
  }
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

let editingSourceId = null;

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
        <div class="kw">${s.keywords ? "关键词：" + esc(s.keywords) : "无关键词过滤"} · 时效 ${s.ttl_hours || 24}h</div></div>
      <button class="mini" data-edit title="编辑来源">编辑</button>
      <div class="switch ${s.enabled ? "on" : ""}" data-toggle><span class="track"></span></div>
      <button class="mini danger" data-del>删除</button>`;
    row.querySelector("[data-edit]").addEventListener("click", () => {
      editingSourceId = s.id;
      const f = list.querySelector(".add-card");
      f.querySelector("#src-type").value = s.type;
      f.querySelector("#src-name").value = s.name;
      f.querySelector("#src-url").value = s.url;
      f.querySelector("#src-items-path").value = s.items_path || "data";
      f.querySelector("#src-title-field").value = s.title_field || "title";
      f.querySelector("#src-keywords").value = s.keywords || "";
      f.querySelector("#src-ttl").value = s.ttl_hours || 24;
      const btn = f.querySelector("#src-add-btn");
      btn.textContent = "保存修改";
      btn.scrollIntoView({block: "nearest"});
      toast(`正在编辑：${s.name}`, "ok");
    });
    row.querySelector("[data-toggle]").addEventListener("click", async e => {
      const el = e.currentTarget;
      await api.post(`/api/sources/${s.id}/toggle`);
      s.enabled = !s.enabled;
      el.classList.toggle("on", s.enabled);
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
    <input id="src-ttl" type="number" min="1" placeholder="时效小时（默认 24）">
    <button class="btn primary" id="src-add-btn" style="grid-column:1/-1;justify-content:center">添加</button>`;
  add.querySelector("#src-add-btn").addEventListener("click", async () => {
    const name = add.querySelector("#src-name").value.trim();
    const url = add.querySelector("#src-url").value.trim();
    if (!name || !url) { toast("名称和 URL 必填", "err"); return; }
    const body = {
      type: add.querySelector("#src-type").value, name, url,
      items_path: add.querySelector("#src-items-path").value || "data",
      title_field: add.querySelector("#src-title-field").value || "title",
      keywords: add.querySelector("#src-keywords").value || "",
      ttl_hours: parseInt(add.querySelector("#src-ttl").value, 10) || 24,
    };
    if (editingSourceId !== null) {
      await api.patch(`/api/sources/${editingSourceId}`, body);
      toast("已保存修改", "ok");
      editingSourceId = null;
    } else {
      await api.post("/api/sources", body);
      toast("已添加来源", "ok");
    }
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
      const el = e.currentTarget;
      await api.post(`/api/tags/${t.id}/toggle`);
      t.is_active = !t.is_active;
      el.classList.toggle("on", t.is_active);
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
      <div class="meta"><span class="score ${scoreClass(t.feasibility_score)}">${scoreText(t.feasibility_score)}分</span>${badgeHtml(t)}${tags}</div>
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

/* =================== 顶部状态栏：调度健康 / 今日 token / 未读角标 =================== */
async function refreshHealth() {
  try {
    const h = await api.get("/api/health");
    const el = $("#schedStatus");
    if (h.minutes_ago === null) {
      el.textContent = "调度: 未知";
      el.className = "sched-warn";
    } else if (h.minutes_ago > 15) {
      el.textContent = `调度: ${h.minutes_ago} 分钟前`;
      el.className = "sched-bad";
    } else {
      el.textContent = `调度: ${h.minutes_ago} 分钟前`;
      el.className = "sched-ok";
    }
    $("#tokenToday").textContent = `今日: ${h.today_tokens ?? 0}`;
  } catch (e) { /* 忽略，下次刷新重试 */ }
}

async function refreshNotifications() {
  try {
    const n = await api.get("/api/notifications?unread_only=1");
    const badge = $("#notifBadge");
    badge.textContent = n.unread;
    badge.classList.toggle("hidden", n.unread === 0);
  } catch (e) { /* 忽略 */ }
}

/* =================== 通知中心抽屉 =================== */
const notifTypeMap = {task_done: "完成", task_fail: "失败", exec_start: "执行", info: "信息"};

async function renderNotifications() {
  const n = await api.get("/api/notifications");
  const list = $("#notifList");
  list.innerHTML = n.items.length
    ? n.items.map(it => `
      <div class="notif-item ${it.is_read ? "" : "unread"}" data-id="${it.id}">
        <div class="n-head">
          <span class="notif-type">${notifTypeMap[it.type] || esc(it.type)}</span>
          <span class="n-time">${esc((it.created_at || "").replace("T", " ").slice(0, 16))}</span>
        </div>
        <div class="n-title">${esc(it.title)}</div>
        ${it.body ? `<div class="n-body">${esc(it.body)}</div>` : ""}
      </div>`).join("")
    : '<div class="empty"><b>暂无通知</b>执行完成 / 失败等事件会显示在这里</div>';
  list.querySelectorAll(".notif-item.unread").forEach(el => el.addEventListener("click", async () => {
    try {
      await api.post(`/api/notifications/${el.dataset.id}/read`);
      el.classList.remove("unread");
      refreshNotifications();
    } catch (e) { /* 忽略 */ }
  }));
}

function openNotif() {
  $("#notifDrawer").classList.add("show");
  $("#notifScrim").classList.add("show");
  $("#notifDrawer").setAttribute("aria-hidden", "false");
  renderNotifications().catch(e => toast("通知加载失败", "err"));
}

function closeNotif() {
  $("#notifDrawer").classList.remove("show");
  $("#notifScrim").classList.remove("show");
  $("#notifDrawer").setAttribute("aria-hidden", "true");
}

/* =================== 设置弹窗 =================== */
const SETTING_FIELDS = [
  {key: "auto_run", label: "每晚自动运行", type: "switch", hint: "夜间定时自动收集与调度"},
  {key: "auto_execute", label: "自动执行任务", type: "switch", hint: "调度器自动领取等待区任务（插队不受此开关影响）"},
  {key: "max_concurrent", label: "最大并发数", type: "number", hint: "同时执行的 LLM 任务数"},
  {key: "max_fail_count", label: "最大失败次数", type: "number", hint: "连续失败超过该次数将停止重试"},
  {key: "stale_simple_min", label: "简单任务卡死判定（分钟）", type: "number"},
  {key: "stale_complex_min", label: "复杂任务卡死判定（分钟）", type: "number"},
  {key: "max_daily_tokens", label: "每日 token 上限", type: "number", hint: "达到上限后自动领取暂停"},
  {key: "qq_target", label: "QQ 推送目标", type: "text", hint: "如 qq:123456，留空不推送", placeholder: "qq:123456"},
];

function renderSettings() {
  const body = $("#settingsBody");
  body.innerHTML = SETTING_FIELDS.map(f => {
    const v = SETTINGS[f.key] ?? "";
    if (f.type === "switch") {
      return `<div class="set-row"><label>${f.label}${f.hint ? `<small>${f.hint}</small>` : ""}</label>
        <div class="switch ${v === "1" ? "on" : ""}" data-set-switch="${f.key}"><span class="track"></span></div></div>`;
    }
    return `<div class="set-row"><label>${f.label}${f.hint ? `<small>${f.hint}</small>` : ""}</label>
      <input type="${f.type}" data-set-input="${f.key}" value="${esc(v)}" ${f.placeholder ? `placeholder="${f.placeholder}"` : ""}></div>`;
  }).join("");
  body.querySelectorAll("[data-set-switch]").forEach(el => el.addEventListener("click", () => {
    el.classList.toggle("on");
  }));
}

function openSettings() {
  renderSettings();
  openModal("#settingsModal");
}

async function saveSettings() {
  const updates = [];
  SETTING_FIELDS.forEach(f => {
    if (f.type === "switch") {
      updates.push({key: f.key, value: $(`[data-set-switch="${f.key}"]`).classList.contains("on") ? "1" : "0"});
    } else {
      const el = $(`[data-set-input="${f.key}"]`);
      updates.push({key: f.key, value: el.value.trim()});
    }
  });
  for (const u of updates) {
    await api.put("/api/settings", u);
    SETTINGS[u.key] = u.value;
  }
  $("#autoSwitch").classList.toggle("on", SETTINGS.auto_run === "1");
  closeModal("#settingsModal");
  toast("设置已保存", "ok");
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
    content_type: $("#drContentType").value,
    is_complex: $("#drComplex").checked ? 1 : 0,
    redo_note: $("#drRedoNote").value,
  });
  t.title = $("#drTitle").value;
  t.idea_summary = $("#drSummary").value;
  t.feasibility_score = Number($("#drScore").value);
  t.notes = $("#drNotes").value;
  t.content_type = $("#drContentType").value;
  t.is_complex = $("#drComplex").checked ? 1 : 0;
  t.redo_note = $("#drRedoNote").value;
  renderBoard();
  toast("修改已保存", "ok");
  closeDrawer();
});
$("#drDel").addEventListener("click", () => curId && delTask(curId));
$("#openSource").addEventListener("click", () => { renderSources(); openModal("#sourceModal"); });
$("#openTag").addEventListener("click", () => { renderTags(); openModal("#tagModal"); });

/* 通知中心 */
$("#notifBtn").addEventListener("click", openNotif);
$("#notifClose").addEventListener("click", closeNotif);
$("#notifScrim").addEventListener("click", closeNotif);
$("#notifReadAll").addEventListener("click", async () => {
  try {
    await api.post("/api/notifications/read-all");
    toast("已全部标记为已读", "ok");
    await renderNotifications();
    refreshNotifications();
  } catch (err) {
    toast("操作失败：" + err.message, "err");
  }
});

/* 设置弹窗 */
$("#openSettings").addEventListener("click", openSettings);
$("#more-settings").addEventListener("click", () => { moreMenu.hidden = true; openSettings(); });
$("#settingsSave").addEventListener("click", async () => {
  try {
    await saveSettings();
  } catch (err) {
    toast("保存失败：" + err.message, "err");
  }
});

/* 窄屏更多菜单 */
const moreMenu = $("#moreMenu");
$("#btn-more").addEventListener("click", e => {
  e.stopPropagation();
  moreMenu.hidden = !moreMenu.hidden;
});
$("#more-source").addEventListener("click", () => { moreMenu.hidden = true; renderSources(); openModal("#sourceModal"); });
$("#more-tag").addEventListener("click", () => { moreMenu.hidden = true; renderTags(); openModal("#tagModal"); });
$("#more-collect").addEventListener("click", () => { moreMenu.hidden = true; $("#collectBtn").click(); });

/* 立即生成（提示候选与生成流程；真实生成由 Hermes agent 流程执行） */
$("#generateBtn").addEventListener("click", async () => {
  const b = $("#generateBtn");
  b.disabled = true;
  try {
    const r = await api.post("/api/generate");
    if (r && r.candidate_count !== undefined) {
      toast(`待生成候选 ${r.candidate_count} 条。idea 生成由 AI 流程执行（每晚自动或云端手动触发）`, "ok");
    } else {
      toast("已触发，结果请查看候选列表", "ok");
    }
  } catch (err) {
    toast("生成检查失败：" + err.message, "err");
  } finally {
    b.disabled = false;
  }
});

/* 新建目标模式 */
$("#addTargetBtn").addEventListener("click", () => {
  $("#tg-name").value = ""; $("#tg-desc").value = ""; $("#tg-dims").value = "";
  openModal("#targetModal");
});
$("#tg-save").addEventListener("click", async () => {
  const name = $("#tg-name").value.trim();
  if (!name) { toast("目标名称必填", "err"); return; }
  let dims = $("#tg-dims").value.trim();
  if (dims) {
    try { JSON.parse(dims); } catch (e) { toast("评分维度不是合法 JSON", "err"); return; }
  } else {
    dims = "{}";
  }
  try {
    const r = await api.post("/api/targets", {
      name, description: $("#tg-desc").value.trim(), score_dimensions: dims,
    });
    closeModal("#targetModal");
    await loadAll();
    if (r && r.id) {
      await api.post(`/api/targets/${r.id}/activate`);
      await loadAll();
      toast(`目标「${name}」已创建并激活`, "ok");
    } else {
      toast("目标已创建", "ok");
    }
  } catch (err) {
    toast("创建失败：" + err.message, "err");
  }
});
document.addEventListener("click", e => {
  if (!moreMenu.hidden && !e.target.closest("#btn-more") && !e.target.closest("#moreMenu")) moreMenu.hidden = true;
});
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
    closeNotif();
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

/* 每 60 秒刷新调度健康与未读角标 */
setInterval(() => { refreshHealth(); refreshNotifications(); }, 60000);
