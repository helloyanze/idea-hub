const $ = (s) => document.querySelector(s);
const api = {
  get: (p) => fetch(p).then(r => r.json()),
  post: (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b||{})}).then(r=>r.json()),
  patch: (p, b) => fetch(p, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r=>r.json()),
  delete: (p) => fetch(p, {method:'DELETE'}).then(r=>r.json()),
};
let currentTarget = null;
let currentTag = '';
let tags = [];  // [{id, name, description, is_active}]

const COLUMN_NAMES = {archived:'留档', todo:'待办', waiting:'等待', in_progress:'进行中', done:'已完成'};

// 转义外部数据后再插入 innerHTML，防止 XSS（& < > " '）
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function badge(score) {
  const cls = score < 6 ? 'red' : score <= 7 ? 'yellow' : 'green';
  return `<span class="badge ${cls}">${score}</span>`;
}

function tagBadges(tagsArr) {
  return (tagsArr || []).map(t => `<span class="type-badge">${escapeHtml(t.name)}</span>`).join(' ');
}

function cardHTML(t) {
  return `<div class="card" data-id="${t.id}">
    <div class="card-title">${escapeHtml(t.title)}</div>
    <div class="card-meta">${badge(t.feasibility_score)} ${tagBadges(t.tags)}</div>
    <div class="card-summary">${escapeHtml((t.idea_summary||'').slice(0,60))}</div>
  </div>`;
}

async function loadTags() {
  const d = await api.get('/api/tags');
  tags = d.items;
  const sel = $('#type-filter');
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部标签</option>' +
    tags.map(t => `<option value="${t.id}">${escapeHtml(t.name)}${t.is_active ? '' : ' (停用)'}</option>`).join('');
  sel.value = cur;
  sel.onchange = () => { currentTag = sel.value; loadBoard(); };
}

async function loadBoard() {
  const q = currentTarget ? `?target_id=${currentTarget}` : '';
  const data = await api.get(`/api/tasks${q}`);
  const st = await api.get(`/api/stats${q}`);
  for (const [status, col] of Object.entries(COLUMN_NAMES)) {
    const box = document.querySelector(`.col[data-status="${status}"] .cards`);
    box.innerHTML = data.items
      .filter(t => t.status === status)
      .filter(t => !currentTag || (t.tags || []).some(x => String(x.id) === currentTag))
      .map(cardHTML).join('');
  }
  $('#stats').textContent = Object.entries(st).map(([k,v]) => `${COLUMN_NAMES[k]||k}:${v}`).join(' | ');
}

function initSortable() {
  document.querySelectorAll('.col .cards').forEach(box => {
    new Sortable(box, {
      group: 'board',
      onEnd: async (evt) => {
        const taskId = Number(evt.item.dataset.id);
        const toStatus = evt.to.closest('.col').dataset.status;
        await api.post(`/api/tasks/${taskId}/move`, {to_status: toStatus});
        loadBoard();
      }
    });
    // 点击卡片打开详情抽屉（事件委托，loadBoard 重绘后依然有效）
    box.addEventListener('click', (e) => {
      const card = e.target.closest('.card');
      if (card) openDrawer(Number(card.dataset.id));
    });
  });
}

function prettyBreakdown(raw) {
  try { return JSON.stringify(JSON.parse(raw || '{}'), null, 2); }
  catch (e) { return raw || '{}'; }
}

async function openDrawer(id) {
  const t = await api.get(`/api/tasks/${id}`);
  const hotItem = t.hot_item_id ? `<div>关联热点 #${t.hot_item_id}</div>` : '';
  $('#drawer').innerHTML = `
    <button onclick="closeDrawer()">关闭</button>
    <label>标题 <input type="text" id="f-title"></label>
    <label>构思摘要 <textarea id="f-summary"></textarea></label>
    <label>分数 <input type="range" min="1" max="10" id="f-score" value="${t.feasibility_score}"></label>
    <h3>构思全文</h3><div id="idea-full"></div>
    <h3>评分明细</h3><pre>${escapeHtml(prettyBreakdown(t.score_breakdown))}</pre>
    <h3>产出</h3><div>${t.output_path ? `<a href="/outputs/${escapeHtml(t.output_path.replace(/^outputs\//,''))}">打开产出</a>` : '无'}</div>
    ${hotItem}
    <label>备注 <textarea id="f-notes"></textarea></label>
    <div class="actions">
      <button onclick="saveTask(${t.id})">保存修改</button>
      <button onclick="runTask(${t.id})">执行</button>
    </div>`;
  $('#idea-full').textContent = t.idea_full || '(无构思文件)';
  // value 赋值写入外部数据（不解析 HTML，与 idea_full 的 textContent 同级别安全）
  $('#f-title').value = t.title || '';
  $('#f-summary').value = t.idea_summary || '';
  $('#f-notes').value = t.notes || '';
  $('#drawer').hidden = false;
}
window.closeDrawer = () => $('#drawer').hidden = true;

async function saveTask(id) {
  await api.patch(`/api/tasks/${id}`, {
    title: $('#f-title').value,
    idea_summary: $('#f-summary').value,
    feasibility_score: Number($('#f-score').value),
    notes: $('#f-notes').value
  });
  closeDrawer(); loadBoard();
}

async function runTask(id) {
  await api.post(`/api/tasks/${id}/execute`);
  alert('已加入执行队列');
}

async function loadTargets() {
  const data = await api.get('/api/targets');
  const sel = $('#target-switch');
  sel.innerHTML = data.items.map(t => `<option value="${t.id}" ${t.is_active?'selected':''}>${escapeHtml(t.name)}</option>`).join('');
  currentTarget = data.items.find(t => t.is_active)?.id ?? null;
  sel.onchange = async () => {
    await api.post(`/api/targets/${sel.value}/activate`);
    currentTarget = Number(sel.value);
    loadBoard();
  };
}

// sources modal: list/add/toggle/delete per Task 6 routes
async function loadSources() {
  const d = await api.get('/api/sources');
  $('#source-modal').innerHTML = `<div class="modal-box"><h3>来源管理</h3>
    <div class="src-add">
      <input id="src-type" value="hotlist" placeholder="类型(hotlist/rss)">
      <input id="src-name" placeholder="名称">
      <input id="src-url" placeholder="URL">
      <input id="src-items-path" placeholder="条目路径(默认 data)">
      <input id="src-title-field" placeholder="标题字段(默认 title)">
      <input id="src-keywords" placeholder="关键词白名单(逗号分隔，空=不过滤)">
      <button onclick="addSource()">添加</button>
    </div>` +
    d.items.map(s => `<div class="src-row">${escapeHtml(s.name)} (${escapeHtml(s.type)}) ${s.enabled?'启用':'停用'}\n      <span><button onclick="toggleSource(${s.id})">切换</button>\n      <button onclick="delSource(${s.id})">删除</button></span></div>`).join('') +
    `<button onclick="closeSources()">关闭</button></div>`;
  $('#source-modal').hidden = false;
}

// tags modal: 主题标签管理（可自定义）
async function loadTagsModal() {
  const d = await api.get('/api/tags');
  $('#type-modal').innerHTML = `<div class="modal-box"><h3>标签管理</h3>
    <div class="src-add">
      <input id="ct-name" placeholder="标签名(如 langchain/agent/skills)">
      <input id="ct-desc" placeholder="描述(可选)">
      <button onclick="addTag()">添加</button>
    </div>` +
    d.items.map(t => `<div class="src-row">${escapeHtml(t.name)} ${t.is_active?'启用':'停用'} — ${escapeHtml(t.description)}
      <span><button onclick="toggleTag(${t.id})">切换</button>
      <button onclick="delTag(${t.id})">删除</button></span></div>`).join('') +
    `<button onclick="closeTypes()">关闭</button></div>`;
  $('#type-modal').hidden = false;
}
window.addTag = async () => {
  await api.post('/api/tags', {name: $('#ct-name').value, description: $('#ct-desc').value || ''});
  loadTagsModal(); loadTags();
};
window.toggleTag = async (id) => { await api.post(`/api/tags/${id}/toggle`); loadTagsModal(); loadTags(); };
window.delTag = async (id) => { await api.delete(`/api/tags/${id}`); loadTagsModal(); loadTags(); };
window.closeTypes = () => $('#type-modal').hidden = true;
window.addSource = async () => {
  await api.post('/api/sources', {
    type: $('#src-type').value || 'hotlist',
    name: $('#src-name').value,
    url: $('#src-url').value,
    items_path: $('#src-items-path').value || 'data',
    title_field: $('#src-title-field').value || 'title',
    keywords: $('#src-keywords').value || ''
  });
  loadSources();
};
window.toggleSource = async (id) => { await api.post(`/api/sources/${id}/toggle`); loadSources(); };
window.delSource = async (id) => { await api.delete(`/api/sources/${id}`); loadSources(); };
window.closeSources = () => $('#source-modal').hidden = true;

async function init() {
  $('#btn-sources').onclick = loadSources;
  $('#btn-types').onclick = loadTagsModal;
  $('#btn-collect').onclick = () => alert('收集由每日定时任务执行；如需立即收集请运行：uv run python -m idea_hub.cli collect');
  initSortable();
  await loadTargets();
  await loadTags();
  await loadBoard();
}
init();
