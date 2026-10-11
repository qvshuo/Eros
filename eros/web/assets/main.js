'use strict';
const $ = id => document.getElementById(id);
const PAGE_SIZE = 100,
  TASK_PAGE_SIZE = 100;
const sourceNames = {
  dmm: 'DMM',
  javdb: 'JavDB',
  javbus: 'JavBus',
  fc2: 'FC2 官方',
  avsox: 'AVSOX'
};
const sourceLabel = source => sourceNames[source] || (source === 'gfriends' ? 'Gfriends' : source);
const categoryNames = {
  censored: '有码',
  uncensored: '无码',
  fc2: 'FC2',
  unknown: '未分类'
};
const statusNames = {
  pending: '未获取',
  running: '进行中',
  scraped: '已完成',
  translation_partial: '翻译未完成',
  failed: '获取失败',
  success: '已完成',
  finished: '已结束',
  invalid_key: '名称有误',
  conflict: '编号重复',
  cancelled: '已取消',
  skipped: '已跳过'
};
const taskNames = {
  fetch_all: '获取全部',
  fill_missing: '补齐信息',
  fetch_selected: '获取信息',
  fetch_source: '选择网站',
  refresh: '重新获取',
  fetch_portraits: '演员头像'
};
const statusOrder = {
  invalid_key: 0,
  conflict: 1,
  failed: 2,
  translation_partial: 3,
  running: 4,
  pending: 5,
  scraped: 6
};
const statusGroups = {
  todo: ['pending', 'failed', 'translation_partial'],
  running: ['running'],
  scraped: ['scraped'],
  invalid: ['invalid_key', 'conflict']
};
const selectedPaths = new Set();
let taskId = null,
  resultPage = 1,
  taskRequestId = 0,
  resultTimer = null;
let works = [],
  tasks = [],
  preferences = null,
  actors = [],
  pageNumber = 1,
  taskPage = 1,
  actorPage = 1,
  savedSettingsSnapshot = null,
  savingSettings = false;
let noticeTimer = null;
let siteDrafts = {},
  scanTimer = null,
  reloading = false,
  startingTask = false,
  activeTab = 'library';

let workDirectory = null,
  workDetails = null,
  workSnapshot = null,
  workRequestId = 0,
  workReturn = '#library',
  savingWork = false,
  previewingCrop = false,
  cropPreview = null,
  cropTimer = null,
  cropRequest = 0;

function text(tag, value, className = '') {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

function notice(value, error = false) {
  $('notice').textContent = value;
  $('notice').className = 'notice' + (error ? ' error' : '');
  $('notice').hidden = !value;
  $('notice').setAttribute('role', error ? 'alert' : 'status');
  if (noticeTimer) clearTimeout(noticeTimer);
  if (value && !error) noticeTimer = setTimeout(() => notice(''), 5000);
}

function active(task) {
  return ['running', 'pending'].includes(task.status);
}

function busy() {
  return tasks.some(active) || works.some(work => work.status === 'running');
}

function valid(work) {
  return !['invalid_key', 'conflict'].includes(work.status);
}

function badge(status) {
  const tone = ['failed', 'invalid_key', 'conflict'].includes(status) ? 'error' : status ===
    'translation_partial' ? 'warn' : ['success', 'scraped'].includes(status) ? 'success' : active({
      status
    }) ? 'active' : '';
  return text('span', statusNames[status] || status, 'badge ' + tone);
}

function readableError(detail) {
  if (Array.isArray(detail)) {
    const labels = {
      media_directories: '作品文件夹',
      metadata_directory: '信息文件夹',
      routes: '网站顺序',
      sites: '网站设置',
      translation: '翻译设置',
      timeout_seconds: '等待秒数',
      retry_delay_seconds: '重试秒数',
      retries: '再试次数',
      watch_interval_seconds: '检查间隔',
      cookie: '登录信息',
      model: '模型',
      endpoint: '接口地址',
      api_key: '接口密钥'
    };
    return detail.map(item => `${(item.loc || []).map(part=>labels[part]).find(Boolean) || '设置'}填写有误。`).join(
      ' ');
  }
  const errors = {
    'cannot change configuration while tasks are active': '任务结束后可保存设置。',
    'cannot scan while a task is active': '任务结束后可查找作品。',
    'task overlaps an active write task': '所选作品已有任务，请稍后再试。',
    'no eligible works or requested directory is invalid/conflicting': '请先选择名称正确的作品。',
    'explicit work ID or URL requires one key and a source': '请只选择一部作品和一个网站。',
    'cannot retry active task': '任务尚未结束，不能再试。',
    'task contains no failed items': '该任务没有可再试的失败项。',
    'directory is outside the available mounts': '请选择可访问的文件夹。',
    'configure media and metadata directories first': '请先设置作品与信息文件夹。',
    'configuration change in progress': '正在更新设置，请稍后。',
    'filesystem access failed': '无法访问信息文件夹。',
    'directory does not exist': '文件夹不存在。'
  };
  return errors[detail] || (/[\u4e00-\u9fff]/.test(String(detail)) ? String(detail) : '操作未完成，请稍后再试。');
}
async function api(path, method = 'GET', data) {
  const response = await fetch('/api/' + path, {
    method,
    headers: {
      'Content-Type': 'application/json'
    },
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw Error(`服务暂不可用（${response.status}）`);
  }
  if (!response.ok) throw Error(readableError(value.detail));
  return value;
}
async function action(fn) {
  try {
    await fn();
  } catch (error) {
    notice(error.message, true);
  }
}

function switchTab(tab, focus = false) {
  const path = tab.split('/');
  const nextWork = path[0] === 'library' && path[1] === 'work' ? decodeURIComponent(path[2] || '') : null;
  if (savingWork && nextWork !== workDirectory) {
    history.replaceState(null, '', '#library/work/' + encodeURIComponent(workDirectory));
    notice('正在保存…');
    return;
  }
  if (workUnsaved() && nextWork !== workDirectory) {
    if ($('confirm-dialog').open) return;
    void action(async () => {
      if (await confirmAction('离开页面', '未保存的修改会丢失。')) {
        workSnapshot = workFingerprint();
        switchTab(tab, focus);
      } else history.replaceState(null, '', '#library/work/' + encodeURIComponent(workDirectory));
    });
    return;
  }
  const changedWork = nextWork !== workDirectory;
  workDirectory = nextWork;
  $('work-overview').hidden = Boolean(workDirectory);
  $('work-page').hidden = !workDirectory;
  tab = path[0];
  if (tab === 'tasks') {
    const id = path[1] || null;
    if (id !== taskId) {
      resultPage = 1;
      $('result-search').value = '';
      $('result-filter').value = 'all';
      $('task-results').replaceChildren();
      $('task-detail-title').textContent = '正在加载…';
    }
    taskId = id;
    $('task-overview').hidden = Boolean(taskId);
    $('task-detail').hidden = !taskId;
  }
  if (!['library', 'tasks', 'actors', 'settings'].includes(tab)) tab = 'library';
  if (activeTab !== tab && !$('notice').classList.contains('error')) notice('');
  activeTab = tab;
  for (const button of document.querySelectorAll('[data-tab]')) {
    const on = button.dataset.tab === tab;
    button.setAttribute('aria-selected', String(on));
    button.tabIndex = on ? 0 : -1;
    $('panel-' + button.dataset.tab).hidden = !on;
  }
  const hash = '#' + tab + (tab === 'tasks' && taskId ? '/' + taskId : workDirectory ? '/work/' +
    encodeURIComponent(workDirectory) : '');
  if (location.hash !== hash) history.replaceState(null, '', hash);
  if (focus) $('tab-' + tab).focus();
  if (tab === 'tasks' && taskId) void action(loadTaskDetail);
  if (tab === 'actors') void action(reload);
  if (workDirectory && changedWork) void action(loadWorkDetail);
}
for (const button of document.querySelectorAll('[data-tab]')) {
  button.onclick = () => switchTab(button.dataset.tab);
  button.onkeydown = event => {
    const tabs = ['library', 'tasks', 'actors', 'settings'],
      index = tabs.indexOf(activeTab);
    let next;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % 4];
    if (event.key === 'ArrowLeft') next = tabs[(index + 3) % 4];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[3];
    if (next) {
      event.preventDefault();
      switchTab(next, true);
    }
  };
}
window.addEventListener('hashchange', () => switchTab(location.hash.slice(1)));
switchTab(location.hash.slice(1));
const colorPreference = matchMedia('(prefers-color-scheme: dark)');

function applyTheme() {
  document.documentElement.dataset.theme = colorPreference.matches ? 'dark' : 'light';
}
colorPreference.addEventListener('change', applyTheme);
applyTheme();
initDropdowns();

function filteredWorks() {
  const query = $('search').value.trim().toLowerCase(),
    status = $('status-filter').value,
    category = $('category-filter').value;
  return works.filter(work => (!query || work.code.toLowerCase().includes(query) || work.path.toLowerCase()
    .includes(query)) && (status === 'all' || statusGroups[status]?.includes(work.status)) && (
    category === 'all' || work.category === category)).sort((a, b) => (statusOrder[a.status] ?? 7) - (
    statusOrder[b.status] ?? 7) || a.code.localeCompare(b.code, 'en', {
    numeric: true
  }) || a.path.localeCompare(b.path));
}

function selectedWorks() {
  return works.filter(work => selectedPaths.has(work.path));
}

function targetKeys() {
  return [...new Set(selectedWorks().filter(valid).map(work => work.code))];
}

function linkSource(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    const matches = Object.entries(preferences?.source_origins || {}).filter(([, origins]) => origins.some(
      origin => new URL(origin).host === url.host));
    return matches.length === 1 ? matches[0][0] : null;
  } catch {
    return null;
  }
}

function updateActions() {
  const chosen = selectedWorks(),
    codes = targetKeys(),
    isSource = $('operation').value === 'fetch_source',
    single = chosen.length === 1 && codes.length === 1;
  for (const id of ['work-id', 'work-url']) {
    $(id).disabled = !single;
    if (!single) $(id).value = '';
  }
  const url = $('work-url').value.trim();
  const overlap = tasks.some(task => active(task) && (task.params?.codes || []).some(key => codes.includes(
    key))) || works.some(work => work.status === 'running' && codes.includes(work.code));
  const badUrl = isSource && url && linkSource(url) !== $('source').value;
  $('source-field').hidden = !isSource;
  $('precise').hidden = !isSource;
  $('selection-summary').textContent = `已选 ${chosen.length} 部作品`;
  $('clear-selection').disabled = !chosen.length;
  $('invert-selection').disabled = !chosen.length;
  $('execute').disabled = startingTask || !codes.length || overlap || Boolean(badUrl);
  const hint = badUrl ? '链接不属于所选网站。' : '';
  $('action-help').textContent = hint;
  $('action-help').hidden = !hint;
  const eligible = filteredWorks().filter(valid);
  $('select-filtered').disabled = !eligible.length || eligible.every(work => selectedPaths.has(work.path));
  $('invert-selection').disabled = !chosen.length || !eligible.length;
  refreshDropdowns();
}

function renderWorks() {
  const rows = filteredWorks(),
    pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  pageNumber = Math.min(pageNumber, pages);
  const visible = rows.slice((pageNumber - 1) * PAGE_SIZE, pageNumber * PAGE_SIZE),
    eligible = visible.filter(valid),
    scroll = $('works').closest('.table-scroll'),
    top = scroll.scrollTop;
  $('works').replaceChildren();
  for (const work of visible) {
    const row = document.createElement('tr');
    row.dataset.status = work.status;
    row.dataset.code = work.code;
    row.dataset.path = work.path;
    row.className = selectedPaths.has(work.path) ? 'selected' : '';
    const cell = document.createElement('td');
    cell.className = 'check-cell';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.disabled = !valid(work);
    check.checked = selectedPaths.has(work.path);
    check.setAttribute('aria-label', `选择 ${work.code}`);
    check.onchange = () => {
      check.checked ? selectedPaths.add(work.path) : selectedPaths.delete(work.path);
      row.classList.toggle('selected', check.checked);
      const checks = [...$('works').querySelectorAll('input:not(:disabled)')],
        count = checks.filter(box => box.checked).length;
      $('select-page').checked = count === checks.length;
      $('select-page').indeterminate = count > 0 && count < checks.length;
      updateActions();
    };
    const hit = document.createElement('label');
    hit.className = 'checkbox-hit';
    hit.append(check);
    cell.append(hit);
    const key = document.createElement('td');
    key.className = 'key-cell';
    const link = text('button', work.code, 'key-link');
    link.type = 'button';
    link.onclick = () => action(() => showWorkDetail(work));
    key.append(link);
    const status = document.createElement('td');
    status.append(badge(work.status));
    row.onclick = event => {
      if (!event.target.closest('button,a,input,label') && !window.getSelection()?.toString())
        showWorkDetail(work);
    };
    row.append(cell, key, text('td', categoryNames[work.category] || '未分类', 'category-cell'), status);
    $('works').append(row);
  }
  scroll.scrollTop = top;
  $('work-empty').hidden = rows.length !== 0;
  $('select-page').disabled = !eligible.length;
  $('select-page').checked = eligible.length > 0 && eligible.every(work => selectedPaths.has(work.path));
  $('select-page').indeterminate = eligible.some(work => selectedPaths.has(work.path)) && !$('select-page')
    .checked;
  $('page-summary').textContent = rows.length ?
    `第 ${(pageNumber-1)*PAGE_SIZE+1}–${Math.min(pageNumber*PAGE_SIZE,rows.length)} 条 / 共 ${rows.length} 条` :
    '0 条';
  $('page-number').replaceChildren();
  for (let n = 1; n <= pages; n++) {
    const option = text('option', `第 ${n} / ${pages} 页`);
    option.value = n;
    $('page-number').append(option);
  }
  $('page-number').value = String(pageNumber);
  $('page-prev').disabled = pageNumber === 1;
  $('page-next').disabled = pageNumber === pages;

  updateActions();
}

function pageChange(value) {
  pageNumber = value;
  $('works').closest('.table-scroll').scrollTop = 0;
  renderWorks();
}
$('page-prev').onclick = () => pageChange(pageNumber - 1);
$('page-next').onclick = () => pageChange(pageNumber + 1);
$('page-number').onchange = () => pageChange(Number($('page-number').value));
for (const id of ['search', 'status-filter', 'category-filter']) $(id).addEventListener(id === 'search' ?
  'input' : 'change', () => pageChange(1));
$('select-page').onchange = () => {
  for (const work of filteredWorks().slice((pageNumber - 1) * PAGE_SIZE, pageNumber * PAGE_SIZE).filter(
      valid)) $(
    'select-page').checked ? selectedPaths.add(work.path) : selectedPaths.delete(work.path);
  renderWorks();
};
$('invert-selection').onclick = () => {
  for (const work of filteredWorks().filter(valid)) selectedPaths.has(work.path) ? selectedPaths.delete(work
    .path) : selectedPaths.add(work.path);
  renderWorks();
};
$('clear-selection').onclick = () => {
  selectedPaths.clear();
  renderWorks();
};
$('select-filtered').onclick = () => {
  for (const work of filteredWorks().filter(valid)) selectedPaths.add(work.path);
  renderWorks();
};
$('operation').onchange = updateActions;
$('source').onchange = () => {
  $('work-id').value = '';
  if ($('work-url').value && linkSource($('work-url').value) !== $('source').value) $('work-url').value =
    '';
  updateActions();
};
$('work-id').oninput = updateActions;
$('work-url').oninput = () => {
  const source = linkSource($('work-url').value);
  if (source && source !== $('source').value) {
    $('source').value = source;
    $('work-id').value = '';
  }
  updateActions();
};
async function confirmAction(title, description) {
  const dialog = $('confirm-dialog');
  $('confirm-title').textContent = title;
  $('confirm-description').textContent = description;
  dialog.querySelector('button[value="confirm"]').textContent = title === '修改预览' ? '保存' : '确认';
  dialog.returnValue = 'cancel';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {
      once: true
    });
    dialog.showModal();
  });
}
$('execute').onclick = () => action(async () => {
  const codes = targetKeys(),
    type = $('operation').value;
  if (!codes.length) return;
  if (type === 'refresh' || codes.length > 100)
    if (!await confirmAction(taskNames[type],
        `处理所选 ${codes.length} 部作品。${type==='refresh'?'\n将重新获取并更新已有信息。':''}`)) return;
  startingTask = true;
  updateActions();
  try {
    const body = {
      type,
      codes: codes
    };
    if (type === 'fetch_source') {
      body.source_id = $('source').value;
      if ($('work-id').value.trim()) body.external_id = $('work-id').value.trim();
      if ($('work-url').value.trim()) body.source_url = $('work-url').value.trim();
    }
    await api('tasks', 'POST', body);
    taskPage = 1;
    $('task-filter').value = 'all';
    switchTab('tasks');
    notice('任务已提交。');
    await reload();
  } finally {
    startingTask = false;
    updateActions();
  }
});
$('scan').onclick = async () => {
  clearTimeout(scanTimer);
  $('scan').disabled = true;
  const feedback = $('scan-feedback');
  feedback.classList.remove('error');
  feedback.textContent = '正在查找…';
  feedback.title = '';
  try {
    await api('scan', 'POST');
    await reload();
    feedback.textContent = '查找完成';
    scanTimer = setTimeout(() => {
      feedback.textContent = '';
    }, 5000);
  } catch (error) {
    feedback.textContent = error.message;
    feedback.title = error.message;
    feedback.classList.add('error');
  } finally {
    $('scan').disabled = busy();
  }
};

function chooseCandidate(item, source, candidate) {
  if (!works.some(work => work.code === item.code && valid(work))) {
    notice('作品已移除或编号重复。', true);
    return;
  }
  selectedPaths.clear();
  selectedPaths.add(works.find(work => work.code === item.code && valid(work)).path);
  $('operation').value = 'fetch_source';
  $('source').value = source.source;
  $('work-id').value = candidate.external_id || '';
  $('work-url').value = candidate.url || '';
  pageNumber = 1;
  renderWorks();
  switchTab('library');
  notice('已选择作品');
}

const stageNames = {
  source: '网站',
  challenge: '访问助手',
  translation: '翻译',
  metadata: '保存信息',
  images: '下载图片',
  portraits: '演员头像',
  crop: '裁剪封面',
  retry: '再试',
  finished: '完成',
  wait: '访问间隔',
  request: '访问',
  response: '响应',
  metadata_found: '解析信息'
};
const sourceStatusNames = {
  found: '已获取',
  not_found: '未找到',
  ambiguous: '待选择',
  login_required: '需要登录',
  region_blocked: '地区限制',
  ip_blocked: '访问受限',
  blocked: '访问受限',
  network_error: '网络错误',
  parse_error: '解析失败',
  identity_mismatch: '编号不符',
  unsupported: '不支持',
  success: '已完成',
  failed: '失败'
};

function processList(events, running = false) {
  const list = document.createElement('ol');
  list.className = 'process-list';
  for (const event of events || []) {
    const row = document.createElement('li');
    const date = new Date(event.time);
    const time =
      `${date.getFullYear()}/${String(date.getMonth()+1).padStart(2,'0')}/${String(date.getDate()).padStart(2,'0')} ${date.toLocaleTimeString('zh-CN',{hour12:false})}`;
    const name = event.source ?
      `${sourceLabel(event.source)} · ${stageNames[event.stage] || event.stage}${event.message ? ' · '+event.message : ''}` :
      stageNames[
        event.stage] || event.stage;
    const description = event.source ? name : `${name}${event.message ? ' · ' + event.message : ''}`;
    row.append(text('time', time), text('span', description), text('span', event.status === 'running' ?
      running &&
      event === events.at(-1) ?
      `进行中 · ${Math.max(0,Math.floor((Date.now()-new Date(event.time))/1000))} 秒` : '已完成' :
      sourceStatusNames[event.status] || event.status, 'muted'));
    list.append(row);
  }
  return list;
}

function renderWorkProcess(history) {
  $('work-process').replaceChildren();
  const current = history?.find(item => ['pending', 'running'].includes(item.status)) || history?.find(item =>
    workReturn === '#tasks/' + item.task_id) || history?.[0];
  $('work-process').hidden = !current;
  if (!current) return;
  const heading = document.createElement('div');
  heading.className = 'section-heading';
  const link = text('h3', '获取进度');
  const stage = current.stage;
  heading.append(link, text('span', current.status === 'running' ? (stage?.source ? sourceLabel(stage
      .source) : stageNames[stage?.stage]) || '进行中' : current.status === 'pending' ? '等待中' :
    sourceStatusNames[current.status] || '已结束', 'muted'));
  $('work-process').append(heading, processList(current.events, current.status === 'running'));
}

function renderResults(container, record, showHeading = true) {
  container.replaceChildren();
  if (record.error) container.append(text('p', record.error, 'error'));
  const items = [...(record.items || [])].sort((a, b) => (a.status === 'failed' ? 0 : 1) - (b.status ===
    'failed' ? 0 : 1));
  for (const item of items) {
    const box = document.createElement('div');
    box.className = 'result-row' + (item.status === 'failed' ? ' error' : '');
    const heading = document.createElement('div');
    heading.className = 'result-heading';
    const workLink = text('button', item.code, 'key-link');
    workLink.onclick = () => openWorkKey(item.code);
    heading.append(workLink, badge(item.status));
    box.onclick = event => {
      if (!event.target.closest('button,a,input') && !window.getSelection()?.toString()) openWorkKey(item
        .code);
    };
    if (showHeading) box.append(heading);
    if (showHeading) {
      container.append(box);
      continue;
    }
    if (item.stage && item.status === 'running') box.append(text('p', item.stage.source ? sourceLabel(item
      .stage.source) : stageNames[item.stage.stage], 'muted'));
    if (item.events?.length) box.append(processList(item.events, item.status === 'running'));
    const result = item.result || {},
      notices = [result.error, ...(result.warnings || []), ...(result.translation_errors || []).map(e =>
          '翻译：' + e.error), ...(result.actor_issues || []).filter(e => e.candidates?.length).map(e =>
          '同名演员：' + e.name), ...(result.images || []).filter(e => e.status === 'error').map(e => '图片：' + (e
          .message || e.error)),
        ...(result.portraits || []).filter(e => e.status === 'error').map(e => '演员头像：' + e.name + ' · ' + e
          .error)
      ].filter(Boolean);
    for (const line of notices) box.append(text('p', line));
    for (const source of result.sources || []) {
      if (source.status !== 'found') box.append(text('p',
        `${sourceNames[source.source]||source.source}：${source.message||source.status}`));
      for (const candidate of source.candidates || []) {
        const choice = document.createElement('div');
        choice.className = 'candidate';
        choice.append(text('div', `${candidate.code} · ${candidate.title||''}`), text('p',
          `站内编号 ${candidate.external_id||'—'} · ${candidate.url||''}`));
        const button = text('button', '使用这部', 'button');
        button.onclick = () => chooseCandidate(item, source, candidate);
        choice.append(button);
        box.append(choice);
      }
    }
    if (box.childElementCount) container.append(box);
  }
}

function formatDate(value, time = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const day = `${date.getFullYear()}年${date.getMonth()+1}月${date.getDate()}日`;
  return day + (time ? ' ' + date.toLocaleTimeString('zh-CN', {
    hour12: false
  }) : '');
}

function taskTitle(task) {
  return `${formatDate(task.created_at,true)} · ${taskNames[task.type] || task.type}`;
}

function taskBadge(task) {
  return text('span', task.status === 'cancelled' ? '已取消' : task.status === 'pending' ? '等待中' : task
    .status === 'running' ? '进行中' : '已结束', 'badge ' + (active(task) ? 'active' : ''));
}

function taskWhen(task) {
  return formatDate(task.created_at, true);
}

function filteredTasks() {
  const query = $('task-search').value.trim().toLowerCase();
  return tasks.filter(task => ($('task-filter').value === 'all' || task.status === $('task-filter').value) &&
    (!query || [taskNames[task.type], taskWhen(task)].filter(Boolean).join(' ').toLowerCase().includes(
      query)));
}

function taskButtons(task) {
  const controls = document.createElement('div');
  controls.className = 'task-actions';
  for (const [name, disabled, suffix, method, description] of [
      ['再试', active(task) || !(task.failed || task.skipped), 'retry', 'POST', null],
      ['取消', !active(task), 'cancel', 'POST', '停止未完成的任务，保留已有信息。'],
      ['删除', active(task), '', 'DELETE', '只删除记录，保留已有信息。']
    ]) {
    const button = text('button', name, 'button quiet');
    button.disabled = disabled;
    button.onclick = event => {
      event.stopPropagation();
      void action(async () => {
        if (description && !await confirmAction(name + '任务', description)) return;
        button.disabled = true;
        await api('tasks/' + task.id + (suffix ? '/' + suffix : ''), method);
        if (!suffix && taskId === task.id) switchTab('tasks');
        await reload();
      });
    };
    controls.append(button);
  }
  return controls;
}

function renderTasks() {
  const rows = filteredTasks(),
    pages = Math.max(1, Math.ceil(rows.length / TASK_PAGE_SIZE));
  taskPage = Math.min(taskPage, pages);
  $('tasks').replaceChildren();
  for (const task of rows.slice((taskPage - 1) * TASK_PAGE_SIZE, taskPage * TASK_PAGE_SIZE)) {
    const row = document.createElement('tr');
    row.className = 'task-row';
    const createdCell = document.createElement('td'),
      createdTime = text('span', taskWhen(task));
    createdCell.append(createdTime);

    const process = document.createElement('td');
    process.className = 'task-process';
    process.append(text('span', `成功：${task.success} · 失败：${task.failed}` + (task.skipped ?
      ` · 跳过：${task.skipped}` : '')));
    const status = document.createElement('td');
    status.append(taskBadge(task));
    const controls = document.createElement('td');
    controls.append(taskButtons(task));
    row.append(text('td', '#' + task.sequence, 'task-number'), createdCell, text('td', (taskNames[task
          .type] || task
        .type) + (task.params?.source_id ? ' · ' +
        sourceLabel(task.params.source_id) : ''), 'task-type'), text('td', task.total),
      process, status,
      controls);
    row.onclick = event => {
      if (event.target.closest('button,a') || window.getSelection()?.toString()) return;
      location.hash = 'tasks/' + task.id;
    };
    $('tasks').append(row);
  }
  $('tasks-summary').textContent = `${tasks.length} 个任务`;
  $('task-empty').hidden = rows.length !== 0;
  $('task-pagination').hidden = rows.length <= TASK_PAGE_SIZE;
  $('task-page-summary').textContent = `第 ${taskPage} / ${pages} 页 · ${rows.length} 个任务`;
  $('task-prev').disabled = taskPage === 1;
  $('task-next').disabled = taskPage === pages;
}
async function loadTaskDetail() {
  if (!taskId || activeTab !== 'tasks') return;
  const id = taskId,
    request = ++taskRequestId;
  try {
    const record = await api('tasks/' + id + '?page=' + resultPage + '&status=' + $('result-filter').value +
      '&query=' + encodeURIComponent($('result-search').value.trim()));
    if (request !== taskRequestId || taskId !== id || activeTab !== 'tasks') return;
    resultPage = record.page;
    $('task-detail-title').textContent = taskTitle(record);
    $('task-detail-status').replaceChildren(taskBadge(record));
    $('task-detail-summary').textContent =
      `成功：${record.success} · 失败：${record.failed}` + (record.skipped ? ` · 跳过：${record.skipped}` : '');
    renderResults($('task-results'), record);
    $('result-empty').hidden = Boolean(record.items.length);
    $('result-summary').textContent = `${record.matched} 部作品`;
    $('result-page').replaceChildren();
    for (let n = 1; n <= record.pages; n++) {
      const option = text('option', `第 ${n} / ${record.pages} 页`);
      option.value = n;
      $('result-page').append(option);
    }
    $('result-page').value = String(resultPage);
    $('result-prev').disabled = resultPage <= 1;
    $('result-next').disabled = resultPage >= record.pages;
    refreshDropdowns();
  } catch (error) {
    if (request !== taskRequestId || id !== taskId) return;
    if (error.message === 'not found') {
      switchTab('tasks');
      await reload();
    } else throw error;
  }
}
$('task-back').onclick = () => {
  location.hash = 'tasks';
};

function changeResultPage(pageNumber) {
  resultPage = pageNumber;
  void action(loadTaskDetail);
}
$('result-prev').onclick = () => changeResultPage(resultPage - 1);
$('result-next').onclick = () => changeResultPage(resultPage + 1);
$('result-page').onchange = () => changeResultPage(Number($('result-page').value));
$('result-filter').onchange = () => changeResultPage(1);
$('result-search').oninput = () => {
  clearTimeout(resultTimer);
  resultTimer = setTimeout(() => changeResultPage(1), 250);
};
$('task-filter').onchange = () => {
  taskPage = 1;
  renderTasks();
};
$('task-prev').onclick = () => {
  taskPage--;
  renderTasks();
};
$('task-next').onclick = () => {
  taskPage++;
  renderTasks();
};

function renderActors() {
  const portraitFilter = $('actor-portrait-filter').value;
  const rows = actors.filter(issue => (portraitFilter ===
    'all' || issue.portrait_status === portraitFilter));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  actorPage = Math.min(actorPage, pages);
  $('actors').replaceChildren();
  $('actors-summary').textContent = `${actors.length} 位演员`;
  $('actor-fill').disabled = busy() || !actors.some(actor => actor.codes?.length);
  for (const issue of rows.slice((actorPage - 1) * PAGE_SIZE, actorPage * PAGE_SIZE)) {
    const row = document.createElement('tr'),
      name = text('td', issue.name, 'actor-name');
    const avatar = document.createElement('td');
    if (issue.portrait) {
      const image = document.createElement('img');
      image.src = '/api/actors/' + encodeURIComponent(issue.portrait_name || issue.name) + '/portrait';
      image.alt = issue.name;
      image.loading = 'lazy';
      image.className = 'actor-portrait';
      avatar.append(image);
    } else avatar.append(text('span', '缺少头像', 'muted'));
    const operation = document.createElement('td'),
      button = text('button', issue.portrait ? '更新头像' : '获取头像', 'button quiet');
    button.disabled = busy() || !issue.codes?.length || issue.status === 'ambiguous';
    button.onclick = () => action(async () => {
      await api('tasks', 'POST', {
        type: 'fetch_portraits',
        codes: issue.codes,
        actors: issue.actors || [issue.name],
        replace_portraits: issue.portrait
      });
      await reload();
    });
    operation.append(button);
    row.append(avatar, name, text('td', issue.count ?? 0), operation);
    $('actors').append(row);
  }
  $('actor-empty').hidden = rows.length !== 0;
  $('actor-page-summary').textContent = `第 ${actorPage} / ${pages} 页 · ${rows.length} 个名称`;
  $('actor-prev').disabled = actorPage === 1;
  $('actor-next').disabled = actorPage === pages;
}
let actorSearchTimer = null,
  actorSearchRequest = 0;
async function searchActors() {
  const query = $('actor-search').value.trim(),
    ticket = ++actorSearchRequest;
  const result = await api('actors/library?query=' + encodeURIComponent(query));
  if (ticket !== actorSearchRequest || query !== $('actor-search').value.trim()) return;
  actors = result;
  actorPage = 1;
  renderActors();
}
$('actor-fill').onclick = () => action(async () => {
  const codes = [...new Set(actors.filter(actor => actor.status !== 'ambiguous').flatMap(actor => actor
    .codes || []))];
  await api('tasks', 'POST', {
    type: 'fetch_portraits',
    codes: codes
  });
  await reload();
});
for (const id of ['actor-search', 'actor-portrait-filter']) $(id).addEventListener(id ===
  'actor-search' ? 'input' :
  'change', () => {
    actorPage = 1;
    if (id === 'actor-search') {
      clearTimeout(actorSearchTimer);
      actorSearchTimer = setTimeout(() => void action(searchActors), 200);
    } else renderActors();
  });
$('actor-prev').onclick = () => {
  actorPage--;
  renderActors();
};
$('actor-next').onclick = () => {
  actorPage++;
  renderActors();
};
$('task-search').oninput = () => {
  taskPage = 1;
  renderTasks();
};

// Compare displayed values without retaining unmasked saved secrets.
function settingsFormSnapshot() {
  return JSON.stringify([...$('settings').querySelectorAll('input,select,textarea')].map(field => [field.id ||
    field.getAttribute('aria-label') || field.className, field.type === 'checkbox' ? field.checked :
    field.value
  ]));
}

function settingsChanged() {
  return Boolean(preferences && savedSettingsSnapshot !== null && settingsFormSnapshot() !==
    savedSettingsSnapshot);
}

function updateSettingsState() {
  const writing = busy();
  $('save-settings').disabled = savingSettings || writing || !settingsChanged();
  $('save-settings').title = writing ? '任务处理结束后可保存' : '';
  $('settings-help').hidden = !settingsChanged();
  $('settings-help').textContent = writing ? '设置未保存，任务结束后可保存。' : '设置未保存。';
}
async function reload() {
  if (reloading) return;
  reloading = true;
  try {
    const actorQuery = $('actor-search').value.trim();
    const [nextWorks, nextTasks, nextActors] = await Promise.all([api('works'), api('tasks'),
      activeTab === 'actors' ? api('actors/library?query=' + encodeURIComponent($('actor-search').value
        .trim())) : Promise.resolve(actors)
    ]);
    const changedWorks = JSON.stringify(works) !== JSON.stringify(nextWorks),
      changedTasks = JSON.stringify(tasks) !== JSON.stringify(nextTasks),
      changedActors = JSON.stringify(actors) !== JSON.stringify(nextActors);
    works = nextWorks;
    tasks = nextTasks;
    if (actorQuery === $('actor-search').value.trim()) actors = nextActors;
    const paths = new Set(works.filter(valid).map(work => work.path));
    for (const path of selectedPaths)
      if (!paths.has(path)) selectedPaths.delete(path);
    const runningCount = tasks.filter(active).length;
    $('task-count').textContent = runningCount;
    $('task-count').hidden = !runningCount;
    $('scan-summary').textContent = `${works.length} 部作品`;
    if (changedWorks || !$('works').children.length) renderWorks();
    if (changedTasks) {
      renderTasks();
      if (taskId) void action(loadTaskDetail);
    }
    if (changedActors || changedTasks) renderActors();
    updateActions();
    updateSettingsState();
    $('scan').disabled = busy();
    updateWorkState();
    if (workDirectory && workDetails && (busy() || workDetails.history?.some(item => ['pending', 'running']
        .includes(item.status)))) {
      const path = workDirectory;
      const detail = await api('works/detail?path=' + encodeURIComponent(path));
      if (path === workDirectory) {
        if (!workUnsaved() && !savingWork && !previewingCrop && JSON.stringify([detail.metadata, detail
            .preview
          ]) !== JSON.stringify([workDetails.metadata, workDetails.preview])) {
          renderWorkDetail(detail);
          return;
        }
        workDetails.history = detail.history;
        renderWorkProcess(detail.history);
        $('work-detail-status').replaceChildren(badge(detail.status));
      }
    }
  } finally {
    reloading = false;
  }

}
const settingFields = {
  interval: 'watch_interval_seconds',
  timeout: 'timeout_seconds',
  retries: 'retries',
  'item-retries': 'item_retries',
  'retry-delay': 'retry_delay_seconds'
};
const translationFields = {
  'translation-timeout': 'timeout_seconds',
  'translation-interval': 'interval_seconds'
};
const SECRET_MASK = '●'.repeat(16);

function showSecret(id, value) {
  $(id).value = value ? SECRET_MASK : '';
  $(id).type = value ? 'password' : 'text';
}
for (const id of ['api-key']) {
  $(id).onfocus = () => {
    if ($(id).value === SECRET_MASK) $(id).select();
  };
  $(id).addEventListener('input', () => {
    $(id).type = 'text';
  });
}

function storeSiteDraft() {
  siteDrafts = {};
  for (const row of $('site-rows').children) {
    const site = row.dataset.source,
      stored = preferences.sites[site] || {},
      defaultURL = preferences.source_defaults[site],
      entered = row.querySelector('.site-url').value.trim();
    const effective = stored.base_url || defaultURL,
      base = entered === effective ? (stored.base_url || null) : entered === defaultURL ? null : (entered ||
        null);
    const draft = {
      base_url: base,
      interval_seconds: Number(row.querySelector('.site-interval').value)
    };
    const cookie = row.querySelector('.site-cookie');
    if (cookie && cookie.value !== SECRET_MASK) draft.cookie = cookie.value;
    if (draft.base_url !== (stored.base_url || null) || draft.interval_seconds !== (stored.interval_seconds ??
        preferences.source_intervals[site]) || ('cookie' in draft && draft.cookie !== (stored.cookie || '')))
      siteDrafts[site] = draft;
  }
}

function renderSites() {
  $('site-rows').replaceChildren();
  for (const [site, name] of Object.entries(sourceNames)) {
    const stored = preferences.sites[site] || {},
      row = document.createElement('tr');
    row.dataset.source = site;
    row.append(text('td', name));
    const address = document.createElement('input');
    address.type = 'url';
    address.required = true;
    address.className = 'site-url';
    address.value = stored.base_url || preferences.source_defaults[site];
    address.setAttribute('aria-label', name + ' 网址');
    const interval = document.createElement('input');
    interval.type = 'number';
    interval.min = 0;
    interval.required = true;
    interval.className = 'site-interval';
    interval.value = stored.interval_seconds ?? preferences.source_intervals[site];
    interval.setAttribute('aria-label', name + ' 访问间隔');
    const addressCell = document.createElement('td'),
      intervalCell = document.createElement('td'),
      cookieCell = document.createElement('td');
    addressCell.append(address);
    intervalCell.append(interval);
    if (site === 'javdb') {
      const cookie = document.createElement('input');
      cookie.id = 'cookie-javdb';
      cookie.className = 'site-cookie';
      cookie.autocomplete = 'off';
      cookie.placeholder = 'name=value; name2=value2';
      cookie.setAttribute('aria-label', 'JavDB 登录信息');
      cookieCell.append(cookie);
      row.append(addressCell, intervalCell, cookieCell);
      $('site-rows').append(row);
      showSecret(cookie.id, stored.cookie);
      cookie.onfocus = () => {
        if (cookie.value === SECRET_MASK) cookie.select();
      };
      cookie.oninput = () => {
        cookie.type = 'text';
      };
    } else {
      cookieCell.textContent = '—';
      row.append(addressCell, intervalCell, cookieCell);
      $('site-rows').append(row);
    }
  }
}

function showSettings(value) {
  preferences = value;
  siteDrafts = {};
  $('translation').checked = value.translation.enabled;
  $('actor-photos').checked = value.download_portraits;
  $('endpoint').value = value.translation.endpoint;
  $('model').value = value.translation.model;
  showSecret('api-key', value.translation.api_key);
  $('solver-url').value = value.flaresolverr_url || '';
  for (const [id, field] of Object.entries(settingFields)) $(id).value = value[field];
  for (const [id, field] of Object.entries(translationFields)) $(id).value = value.translation[field];
  for (const category of Object.keys(categoryNames)) $('route-' + category).value = value.routes[category]
    .join(', ');
  renderMediaRoots(value.media_directories);
  $('metadata-root').value = value.metadata_directory || '';
  renderSites();
  savedSettingsSnapshot = settingsFormSnapshot();
  updateSettingsState();
  refreshDropdowns();
}
$('settings').addEventListener('input', updateSettingsState);
$('settings').addEventListener('change', updateSettingsState);

function validateSeconds(value, name, zero = false) {
  if (!Number.isFinite(value) || (value === 0 ? !zero : value < 0 || (value !== 1 && value % 5 !== 0)))
    throw Error(`${name}应为 1 秒或 5 的倍数${zero?'，也可设为 0':''}。`);
}

function collectSettings() {
  storeSiteDraft();
  const value = {
    media_directories: [...document.querySelectorAll('.media-path')].map(input => input.value.trim())
      .filter(
        Boolean),
    metadata_directory: $('metadata-root').value.trim() || null,
    download_portraits: $('actor-photos').checked,
    flaresolverr_url: $('solver-url').value.trim(),
    sites: siteDrafts,
    routes: {},
    translation: {
      enabled: $('translation').checked,
      endpoint: $('endpoint').value.trim(),
      model: $('model').value.trim()
    }
  };
  for (const [id, field] of Object.entries(settingFields)) value[field] = Number($(id).value);
  for (const [id, field] of Object.entries(translationFields)) value.translation[field] = Number($(id).value);
  for (const category of Object.keys(categoryNames)) value.routes[category] = $('route-' + category).value
    .split(/[,，]/).map(s => s.trim()).filter(Boolean);
  if ($('api-key').value !== SECRET_MASK) value.translation.api_key = $('api-key').value;
  return value;
}
$('settings').onsubmit = event => {
  event.preventDefault();
  void action(async () => {
    if (savingSettings || busy() || !settingsChanged()) return;
    const value = collectSettings();
    for (const id of ['interval', 'timeout', 'retry-delay', 'translation-timeout']) validateSeconds(
      Number($(id).value), $(id).closest('label').textContent.trim().replace(/\s+/g, ' '));
    validateSeconds(value.translation.interval_seconds, '翻译访问间隔', true);
    for (const draft of Object.values(siteDrafts)) validateSeconds(draft.interval_seconds, '网站访问间隔',
      true);
    if (value.translation.enabled && (!value.translation.model || !value.translation.endpoint || !(
        value.translation.api_key ?? preferences.translation.api_key))) throw Error(
      '自动翻译前，请填写 接口地址、模型和 接口密钥。');
    if (!value.media_directories.length || !value.metadata_directory) throw Error('请选择作品和信息文件夹。');
    if (preferences.metadata_directory && preferences.metadata_directory !== value
      .metadata_directory && !await confirmAction(
        '切换信息文件夹', '现有资料保留在原文件夹。')) return;
    savingSettings = true;
    updateSettingsState();
    try {
      const saved = await api('settings', 'PUT', value);
      showSettings(saved);
      notice('已保存。');
      await reload();
    } finally {
      savingSettings = false;
      updateSettingsState();
    }
  });
};

function addMediaRow(value = '') {
  const row = document.createElement('div');
  row.className = 'path-row';
  const input = document.createElement('input');
  input.className = 'media-path';
  input.value = value;
  input.placeholder = '视频与 STRM 文件夹';
  input.setAttribute('aria-label', '作品文件夹');
  const browse = text('button', '选择', 'button');
  browse.type = 'button';
  browse.onclick = () => action(() => openDirectory('media', input.value, path => {
    input.value = path;
    updateSettingsState();
  }));
  const remove = text('button', '×', 'button quiet');
  remove.type = 'button';
  remove.setAttribute('aria-label', '移除此文件夹');
  remove.onclick = () => {
    row.remove();
    updateSettingsState();
  };
  row.append(input, browse, remove);
  $('media-roots').append(row);
}

function renderMediaRoots(roots) {
  $('media-roots').replaceChildren();
  for (const root of roots.length ? roots : ['']) addMediaRow(root);
}
$('add-media-root').onclick = () => action(() => openDirectory('media', '', path => {
  const empty = [...document.querySelectorAll('.media-path')].find(input => !input.value);
  if (empty) empty.value = path;
  else addMediaRow(path);
  updateSettingsState();
}));
$('browse-metadata').onclick = () => action(() => openDirectory('metadata', $('metadata-root').value,
  path => {
    $('metadata-root').value = path;
    updateSettingsState();
  }));
let directoryKind = 'media',
  directoryView = null,
  directoryCallback = null,
  directorySelection = null;
const directoryExpanded = new Map();
let directoryRequest = 0;
async function loadDirectory(path) {
  const ticket = ++directoryRequest;
  $('directory-error').hidden = true;
  try {
    const view = await api('directories?kind=' + directoryKind + (path ? '&path=' + encodeURIComponent(
      path) : ''));
    if (ticket !== directoryRequest || !$('directory-dialog').open) return;
    directoryView = view;
    directorySelection = view.path;
    $('directory-selected').textContent = '已选文件夹：' + (view.path || '无');
    directoryExpanded.clear();
    $('directory-path').value = view.path || '';
    $('directory-search').value = '';
    renderDirectoryEntries();
    $('directory-up').disabled = !view.path;
    $('directory-choose').disabled = !view.path;
    $('directory-create').hidden = false;
    $('create-folder').disabled = directoryKind !== 'metadata' || !view.path;
    $('new-folder-name').disabled = directoryKind !== 'metadata' || !view.path;
  } catch (error) {
    if (ticket !== directoryRequest || !$('directory-dialog').open) return;
    $('directory-error').textContent = error.message;
    $('directory-error').hidden = false;
    $('directory-choose').disabled = true;
  }
}

function renderDirectoryEntries() {
  $('directory-list').replaceChildren();
  const query = $('directory-search').value.trim().toLowerCase();

  function entries(nodes, parent, depth = 0) {
    for (const entry of nodes) {
      const children = directoryExpanded.get(entry.path);
      if (query && !entry.name.toLowerCase().includes(query) && !children) continue;
      const row = document.createElement('div');
      row.className = 'directory-tree-row';
      row.style.paddingLeft = (depth * 18) + 'px';
      const toggle = text('button', children ? '⌄' : '›', 'button quiet directory-expand');
      toggle.type = 'button';
      toggle.setAttribute('aria-label', (children ? '收起 ' : '展开 ') + entry.name);
      toggle.setAttribute('aria-expanded', String(Boolean(children)));
      toggle.onclick = () => void action(async () => {
        const ticket = directoryRequest;
        toggle.disabled = true;
        try {
          if (children) directoryExpanded.delete(entry.path);
          else {
            const view = await api('directories?kind=' + directoryKind + '&path=' + encodeURIComponent(
              entry.path));
            if (ticket !== directoryRequest || !$('directory-dialog').open) return;
            directoryExpanded.set(entry.path, view.directories);
          }
          renderDirectoryEntries();
        } finally {
          toggle.disabled = false;
        }
      });
      const name = text('button', entry.name, 'directory-entry');
      name.type = 'button';
      name.setAttribute('aria-pressed', String(directorySelection === entry.path));
      name.onclick = () => {
        directorySelection = entry.path;
        $('directory-path').value = entry.path;
        $('directory-selected').textContent = '已选文件夹：' + entry.path;
        $('directory-choose').disabled = false;
        renderDirectoryEntries();
      };
      row.append(toggle, name);
      parent.append(row);
      if (children) entries(children, parent, depth + 1);
    }
  }
  entries(directoryView?.directories || [], $('directory-list'));
  if (!$('directory-list').children.length) $('directory-list').append(text('p', '暂无', 'muted'));
}
$('directory-search').oninput = renderDirectoryEntries;
$('directory-go').onclick = () => void loadDirectory($('directory-path').value.trim() || null);
$('directory-path').onkeydown = event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    $('directory-go').click();
  }
};
async function openDirectory(kind, path, callback) {
  directoryKind = kind;
  directoryCallback = callback;
  directoryView = null;
  $('directory-up').disabled = true;
  $('directory-title').textContent = '选择文件夹';
  $('directory-dialog').showModal();
  await loadDirectory(path || null);
  if (!directoryView) await loadDirectory(null);
}
$('directory-close').onclick = () => $('directory-dialog').close();
$('directory-cancel').onclick = () => $('directory-dialog').close();
$('directory-up').onclick = () => void loadDirectory(directoryView.parent);
$('directory-choose').onclick = () => {
  directoryCallback(directorySelection);
  $('directory-dialog').close();
};
$('create-folder').onclick = async () => {
  const name = $('new-folder-name').value.trim();
  if (!name) return;
  try {
    const created = await api('directories', 'POST', {
      parent: directorySelection,
      name
    });
    $('new-folder-name').value = '';
    await loadDirectory(created.path);
  } catch (error) {
    $('directory-error').textContent = error.message;
    $('directory-error').hidden = false;
  }
};

function showWorkDetail(work) {
  if (work.path === workDirectory) return;
  workReturn = location.hash || '#library';
  location.hash = 'library/work/' + encodeURIComponent(work.path);
}

function openWorkKey(key) {
  const work = works.find(item => item.code === key);
  if (work) showWorkDetail(work);
  else notice('作品已不在作品库中。', true);
}

function workFingerprint() {
  return JSON.stringify([...$('work-fields').querySelectorAll('input,textarea')].map(input => input.value));
}

function workUnsaved() {
  return Boolean(workDirectory && workSnapshot !== null && workFingerprint() !== workSnapshot);
}

function updateWorkState() {
  $('work-back').disabled = savingWork;
  $('work-save').disabled = !workDetails?.editable || busy() || savingWork || previewingCrop || !
    workUnsaved();
  $('crop-open').disabled = !workDetails?.editable || !workDetails?.artwork.length || busy() || savingWork ||
    previewingCrop;
  $('crop-save').disabled = !cropPreview || previewingCrop || savingWork || busy();
  for (const input of $('work-fields').querySelectorAll('input,textarea'))
    input.disabled = !workDetails?.editable || savingWork || busy();
  for (const id of ['crop-source', 'crop-detect', 'crop-center', 'crop-x', 'crop-y'])
    $(id).disabled = previewingCrop || savingWork || busy();
  $('crop-close').disabled = savingWork;
  $('crop-open').title = !workDetails?.editable ? '名称有误或编号重复' : !workDetails?.artwork.length ? '尚无图片' :
    busy() ?
    '请先完成或取消任务' : savingWork || previewingCrop ? '正在保存或生成预览' : '裁剪封面';
  for (const button of $('work-gallery').querySelectorAll('button')) button.disabled = !workDetails
    ?.editable || busy() || savingWork || previewingCrop;
}
const workFields = [
  ['title', '标题', 'textarea'],
  ['original_title', '原标题', 'textarea'],
  ['plot', '介绍', 'textarea'],
  ['release_date', '发行日期', 'text'],
  ['runtime_minutes', '时长分钟', 'number'],
  ['studio', '制作方', 'text'],
  ['publisher', '发行方', 'text'],
  ['label', '品牌', 'text'],
  ['series', '系列', 'text'],
  ['directors', '导演', 'list'],
  ['actors', '演员', 'list'],
  ['genres', '类型', 'list'],
  ['tags', '标签', 'list']
];

function artworkURL(file) {
  return '/api/works/' + encodeURIComponent(workDetails.code) + '/artwork?file=' + encodeURIComponent(file) +
    '&v=' + Date.now();
}

function renderWorkDetail(detail) {
  workDetails = detail;
  cropRequest++;
  previewingCrop = false;
  cropPreview = null;
  $('work-detail-title').textContent = detail.code;
  $('work-detail-summary').textContent =
    `${categoryNames[detail.category] || '未分类'} · ${detail.media_files.length} 个视频文件`;
  $('work-detail-status').replaceChildren(badge(detail.status));
  $('work-loading').hidden = true;
  $('work-content').hidden = false;
  $('crop-panel').hidden = true;
  const metadata = detail.preview || detail.metadata || {};
  $('work-fields').replaceChildren();
  for (const [field, name, type] of workFields) {
    const label = document.createElement('label');
    label.append(document.createTextNode(name));
    const input = document.createElement(['textarea', 'list'].includes(type) ? 'textarea' : 'input');
    input.id = 'edit-' + field;
    if (input.tagName === 'INPUT') input.type = type;
    else input.rows = field === 'plot' ? 7 : type === 'list' ? 3 : 2;
    if (type === 'number') {
      input.min = 0;
      input.step = 1;
    }
    const value = metadata[field];
    input.value = type === 'list' ? (value || []).map(item => typeof item === 'string' ? item : item.name)
      .join('\n') : value ?? (field === 'title' ? detail.code : '');
    if (field === 'release_date') {
      input.value = value ? formatDate(value + 'T00:00:00') : '';
      input.placeholder = '2026年10月11日';
    }
    input.disabled = !detail.editable;
    input.required = field === 'title';
    label.className = ['title', 'original_title', 'plot'].includes(field) ? 'span-full' : '';
    label.append(input);
    $('work-fields').append(label);
  }
  const poster = detail.artwork.find(file => /-poster\.(jpg|jpeg|png|webp|gif)$/i.test(file));
  $('work-poster').hidden = !poster;
  $('poster-empty').hidden = Boolean(poster);
  if (poster) $('work-poster').src = artworkURL(poster);
  else $('work-poster').removeAttribute('src');
  $('work-gallery').replaceChildren();
  for (const [name, files] of [
      ['原始封面', detail.artwork.filter(file => /-fanart\.(jpg|jpeg|png|webp|gif)$/i.test(file))],
      ['作品截图', detail.artwork.filter(file => /-fanart\d+\./i.test(file))]
    ]) {
    const group = document.createElement('section');
    group.className = 'image-group';
    group.append(text('h3', name));
    const gallery = document.createElement('div');
    gallery.className = 'image-grid';
    for (const file of files) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'image-tile';
      button.title = file;
      button.disabled = !detail.editable || busy();
      const image = document.createElement('img');
      image.src = artworkURL(file);
      image.alt = file;
      image.loading = 'lazy';
      button.append(image);
      button.onclick = () => openCrop(file);
      gallery.append(button);
    }
    group.append(gallery);
    if (!files.length) group.append(text('p', '暂无', 'muted'));
    $('work-gallery').append(group);
  }
  $('work-files').replaceChildren(text('h3', '作品文件'));
  for (const file of detail.media_files) $('work-files').append(text('p', file, 'detail-path'));
  $('work-diagnostics').replaceChildren();
  renderResults($('work-diagnostics'), {
    items: [{
      key: detail.code,
      status: detail.status,
      result: {
        ...detail.last_result,
        warnings: detail.warnings
      }
    }]
  }, false);
  if (detail.metadata_error) $('work-diagnostics').append(text('p', detail.metadata_error, 'error'));
  $('work-diagnostics').hidden = !$('work-diagnostics').textContent.trim();
  renderWorkProcess(detail.history);
  workSnapshot = workFingerprint();
  updateWorkState();
}
async function loadWorkDetail() {
  const path = workDirectory,
    request = ++workRequestId;
  workSnapshot = null;
  workDetails = null;
  $('work-detail-title').textContent = works.find(work => work.path === path)?.code || '作品详情';
  $('work-detail-summary').textContent = '';
  $('work-detail-status').replaceChildren();
  $('work-loading').hidden = false;
  $('work-loading').textContent = '正在加载…';
  $('work-content').hidden = true;
  try {
    const detail = await api('works/detail?path=' + encodeURIComponent(path));
    if (path !== workDirectory || request !== workRequestId) return;
    renderWorkDetail(detail);
  } catch (error) {
    if (path !== workDirectory || request !== workRequestId) return;
    $('work-loading').textContent = error.message;
  }
}
$('work-back').onclick = () => {
  location.hash = workReturn.slice(1);
};
$('work-form').addEventListener('input', updateWorkState);
$('work-form').onsubmit = event => {
  event.preventDefault();
  void action(async () => {
    if ($('work-save').disabled) return;
    const data = {},
      path = workDirectory,
      key = workDetails.code;
    for (const [field, , type] of workFields) {
      const value = $('edit-' + field).value;
      data[field] = type === 'list' ? [...new Set(value.split('\n').map(s => s.trim()).filter(
          Boolean))] :
        type === 'number' ? value === '' ? null : Number(value) : value.trim() || null;
      if (field === 'release_date' && value.trim()) {
        const match = value.trim().match(/^(\d{4})年(\d{1,2})月(\d{1,2})日$/);
        if (!match) throw Error('日期格式：2026年10月11日');
        data[field] = `${match[1]}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`;
      }
      if (field === 'actors') data[field] = data[field].map(name => workDetails.metadata?.actors.find(
        actor => actor.name === name) || {
        name
      });
    }
    const oldValues = JSON.parse(workSnapshot),
      fields = [...$('work-fields').querySelectorAll('input,textarea')];
    for (const [index, [field]] of workFields.entries())
      if (oldValues[index] === fields[index].value) delete data[field];
    const preview = workFields.flatMap(([field, name], index) => oldValues[index] !== fields[index]
        .value ? [`${name}\n原：${oldValues[index] || '—'}\n新：${fields[index].value || '—'}`] : [])
      .join('\n\n');
    if (!await confirmAction('修改预览', preview)) return;
    if (path !== workDirectory || busy() || savingWork || previewingCrop) return;
    savingWork = true;
    updateWorkState();
    try {
      const detail = await api('works/' + encodeURIComponent(key) + '/metadata', 'PUT', data);
      if (workDirectory === path) renderWorkDetail(detail);
      notice(detail.last_result?.translation_errors?.length ? '已保存，部分内容未翻译' : '已保存。', Boolean(detail
        .last_result?.translation_errors?.length));
      await reload();
    } finally {
      savingWork = false;
      updateWorkState();
    }
  });
};

function openCrop(file) {
  if (!workDetails?.editable || busy() || savingWork || previewingCrop) return;
  $('crop-source').replaceChildren(...workDetails.artwork.map(name => {
    const option = text('option', name);
    option.value = name;
    return option;
  }));
  $('crop-source').value = file || workDetails.artwork.find(name => /-fanart\./i.test(name)) || workDetails
    .artwork[0];
  cropPreview = null;
  $('crop-panel').hidden = false;
  initDropdowns();
  void action(() => previewCrop(false));
}

function drawCropOriginal(file, box) {
  const image = new Image(),
    path = workDirectory,
    ticket = cropRequest;
  image.onload = () => {
    if (path !== workDirectory || ticket !== cropRequest || file !== $('crop-source').value) return;
    const canvas = $('crop-original'),
      context = canvas.getContext('2d');
    const scale = Math.min(1, 640 / image.naturalWidth);
    canvas.width = Math.round(image.naturalWidth * scale);
    canvas.height = Math.round(image.naturalHeight * scale);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    context.fillStyle = 'rgba(0,0,0,.45)';
    context.fillRect(0, 0, canvas.width, canvas.height);
    const [x, y, w, h] = box;
    context.drawImage(image, x, y, w, h, x * scale, y * scale, w * scale, h * scale);
    context.strokeStyle = '#fff';
    context.lineWidth = 2;
    context.strokeRect(x * scale, y * scale, w * scale, h * scale);
  };
  image.src = artworkURL(file);
}
async function previewCrop(center = false, manual = false) {
  const path = workDirectory,
    file = $('crop-source').value,
    ticket = ++cropRequest;
  if (!workDetails || savingWork) return;
  previewingCrop = true;
  updateWorkState();
  $('crop-status').textContent = '正在裁剪…';
  try {
    const request = {
      file,
      center
    };
    if (manual && cropPreview) {
      request.box = [...cropPreview.box];
      request.box[0] = Number($('crop-x').value);
      request.box[1] = Number($('crop-y').value);
      request.fingerprint = cropPreview.fingerprint;
    }
    const result = await api('works/' + encodeURIComponent(workDetails.code) + '/crop', 'POST', request);
    if (path !== workDirectory || ticket !== cropRequest || file !== $('crop-source').value) return;
    cropPreview = {
      ...result,
      file
    };
    drawCropOriginal(file, result.box);
    $('crop-preview').src = result.preview;
    $('crop-preview').hidden = false;
    $('crop-x').max = result.width - result.box[2];
    $('crop-x').value = result.box[0];
    $('crop-y').max = result.height - result.box[3];
    $('crop-y').value = result.box[1];
    $('crop-status').textContent = manual ? '已调整 · 2:3' : result.faces.length ?
      `检测到 ${result.faces.length} 张人脸 · 2:3` : '居中裁剪 · 2:3';
  } catch (error) {
    if (ticket === cropRequest && path === workDirectory) {
      cropPreview = null;
      $('crop-preview').hidden = true;
      $('crop-status').textContent = error.message;
    }
  } finally {
    if (ticket === cropRequest) {
      previewingCrop = false;
      updateWorkState();
    }
  }
}
$('crop-open').onclick = () => openCrop();
$('crop-close').onclick = () => {
  if (savingWork) return;
  cropRequest++;
  clearTimeout(cropTimer);
  previewingCrop = false;
  $('crop-panel').hidden = true;
  cropPreview = null;
  updateWorkState();
};
$('crop-source').onchange = () => {
  cropPreview = null;
  void action(() => previewCrop(false));
};
$('crop-detect').onclick = () => void action(() => previewCrop(false));
$('crop-center').onclick = () => void action(() => previewCrop(true));
for (const id of ['crop-x', 'crop-y']) $(id).oninput = () => {
  clearTimeout(cropTimer);
  $('crop-save').disabled = true;
  cropTimer = setTimeout(() => void action(() => previewCrop(false, true)), 200);
};
$('crop-save').onclick = () => void action(async () => {
  if (!cropPreview || previewingCrop || savingWork || busy()) return;
  const path = workDirectory,
    result = cropPreview;
  savingWork = true;
  updateWorkState();
  try {
    const detail = await api('works/' + encodeURIComponent(workDetails.code) + '/crop', 'PUT', {
      file: result.file,
      box: result.box,
      fingerprint: result.fingerprint
    });
    if (workDirectory === path) {
      if (workUnsaved()) {
        workDetails.artwork = detail.artwork;
        $('work-poster').src = artworkURL(detail.artwork.find(file => /-poster\./i.test(file)));
        $('work-poster').hidden = false;
        $('poster-empty').hidden = true;
      } else renderWorkDetail(detail);
      $('crop-panel').hidden = true;
      cropPreview = null;
    }
    notice('封面已保存。');
  } finally {
    savingWork = false;
    updateWorkState();
  }
});
window.addEventListener('beforeunload', event => {
  if (savingWork || workUnsaved()) {
    event.preventDefault();
    event.returnValue = '';
  }
});
void action(async () => {
  showSettings(await api('settings'));
  if (!preferences.configured) switchTab('settings');
  await reload();
  renderActors();
  renderTasks();
});
setInterval(() => {
  void action(reload);
}, 5000);
