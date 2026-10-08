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
  unknown: '未知'
};
const statusNames = {
  pending: '未抓取',
  running: '进行中',
  scraped: '已完成',
  translation_partial: '翻译未完成',
  failed: '抓取失败',
  success: '已完成',
  finished: '已结束',
  invalid_key: '作品名称异常',
  conflict: '番号冲突'
};
const taskNames = {
  scrape_full: '完整抓取',
  scrape_missing: '补齐缺失',
  scrape_keys: '抓取作品',
  scrape_source: '指定来源抓取',
  force_update: '重新抓取',
  actor_images: '演员头像'
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
const selected = new Set();
let detailId = null,
  resultPage = 1,
  detailRequest = 0,
  resultTimer = null;
let movies = [],
  tasks = [],
  config = null,
  actors = [],
  page = 1,
  taskPage = 1,
  actorPage = 1,
  settingsSnapshot = null,
  saving = false;
let noticeTimer = null;
let siteDrafts = {},
  scanTimer = null,
  reloading = false,
  executing = false,
  currentTab = 'library';

let moviePath = null,
  movieData = null,
  movieSnapshot = null,
  movieRequest = 0,
  movieReturn = '#library',
  movieSaving = false,
  cropBusy = false,
  cropped = null,
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
  if (noticeTimer) clearTimeout(noticeTimer);
  if (value && !error) noticeTimer = setTimeout(() => notice(''), 5000);
}

function active(task) {
  return ['running', 'pending'].includes(task.status);
}

function busy() {
  return tasks.some(active) || movies.some(movie => movie.status === 'running');
}

function valid(movie) {
  return !['invalid_key', 'conflict'].includes(movie.status);
}

function badge(status) {
  const tone = ['failed', 'invalid_key', 'conflict'].includes(status) ? 'error' : status ===
    'translation_partial' ? 'warn' : ['success', 'scraped'].includes(status) ? 'success' : active({
      status
    }) ? 'active' : '';
  return text('span', statusNames[status] || status, 'badge ' + tone);
}

function readableError(detail) {
  if (Array.isArray(detail)) return detail.map(item => {
    const name = (item.loc || []).filter(s => s !== 'body').join('.');
    return `${name}：${item.msg}`;
  }).join('；');
  const errors = {
    'cannot change configuration while tasks are active': '任务正在执行，请完成或取消后再保存设置。',
    'cannot scan while a task is active': '任务执行期间不能重新扫描，请稍后重试。',
    'task overlaps an active write task': '所选作品正在执行其他任务，请等待完成。',
    'no eligible movies or requested directory is invalid/conflicting': '没有可处理的作品，或所选目录名有错误 / 冲突。',
    'explicit movie ID or URL requires one key and a source': '填写作品 ID 或 URL 时，请只选择一个作品并指定来源。',
    'cannot retry active task': '任务尚未结束，不能重试。',
    'task contains no failed items': '该任务没有可重试的失败项。',
    'directory is outside the available mounts': '目录不在可用范围内，请通过浏览选择。',
    'configure media and metadata directories first': '请先设置作品与元数据目录。',
    'configuration change in progress': '正在切换配置，请稍后。',
    'filesystem access failed': '目录无法读取或写入，请检查访问权限。',
    'directory does not exist': '目录不存在。'
  };
  return errors[detail] || String(detail || '请求失败，请查看 Docker 日志。');
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
    throw Error(`服务返回异常（HTTP ${response.status}）`);
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
  const nextMovie = path[0] === 'library' && path[1] === 'movie' ? decodeURIComponent(path[2] || '') : null;
  if (movieSaving && nextMovie !== moviePath) {
    history.replaceState(null, '', '#library/movie/' + encodeURIComponent(moviePath));
    notice('正在保存…');
    return;
  }
  if (movieUnsaved() && nextMovie !== moviePath) {
    if ($('confirm-dialog').open) return;
    void action(async () => {
      if (await confirmAction('离开作品详情', '未保存的修改将丢失。')) {
        movieSnapshot = movieFingerprint();
        switchTab(tab, focus);
      } else history.replaceState(null, '', '#library/movie/' + encodeURIComponent(moviePath));
    });
    return;
  }
  const changedMovie = nextMovie !== moviePath;
  moviePath = nextMovie;
  $('movie-overview').hidden = Boolean(moviePath);
  $('movie-page').hidden = !moviePath;
  tab = path[0];
  if (tab === 'tasks') {
    const id = path[1] || null;
    if (id !== detailId) {
      resultPage = 1;
      $('result-search').value = '';
      $('result-filter').value = 'all';
      $('task-results').replaceChildren();
      $('task-detail-title').textContent = '正在读取…';
    }
    detailId = id;
    $('task-overview').hidden = Boolean(detailId);
    $('task-detail').hidden = !detailId;
  }
  if (!['library', 'tasks', 'actors', 'settings'].includes(tab)) tab = 'library';
  if (currentTab !== tab && !$('notice').classList.contains('error')) notice('');
  currentTab = tab;
  for (const button of document.querySelectorAll('[data-tab]')) {
    const on = button.dataset.tab === tab;
    button.setAttribute('aria-selected', String(on));
    button.tabIndex = on ? 0 : -1;
    $('panel-' + button.dataset.tab).hidden = !on;
  }
  const hash = '#' + tab + (tab === 'tasks' && detailId ? '/' + detailId : moviePath ? '/movie/' +
    encodeURIComponent(moviePath) : '');
  if (location.hash !== hash) history.replaceState(null, '', hash);
  if (focus) $('tab-' + tab).focus();
  if (tab === 'tasks' && detailId) void action(loadTaskDetail);
  if (tab === 'actors') void action(reload);
  if (moviePath && changedMovie) void action(loadMovieDetail);
}
for (const button of document.querySelectorAll('[data-tab]')) {
  button.onclick = () => switchTab(button.dataset.tab);
  button.onkeydown = event => {
    const tabs = ['library', 'tasks', 'actors', 'settings'],
      index = tabs.indexOf(currentTab);
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

function filteredMovies() {
  const query = $('search').value.trim().toLowerCase(),
    status = $('status-filter').value,
    category = $('category-filter').value;
  return movies.filter(movie => (!query || movie.key.toLowerCase().includes(query) || movie.path.toLowerCase()
    .includes(query)) && (status === 'all' || statusGroups[status]?.includes(movie.status)) && (
    category === 'all' || movie.category === category)).sort((a, b) => (statusOrder[a.status] ?? 7) - (
    statusOrder[b.status] ?? 7) || a.key.localeCompare(b.key, 'en', {
    numeric: true
  }) || a.path.localeCompare(b.path));
}

function selectedMovies() {
  return movies.filter(movie => selected.has(movie.path));
}

function targetKeys() {
  return [...new Set(selectedMovies().filter(valid).map(movie => movie.key))];
}

function linkSource(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    const matches = Object.entries(config?.source_origins || {}).filter(([, origins]) => origins.some(
      origin => new URL(origin).host === url.host));
    return matches.length === 1 ? matches[0][0] : null;
  } catch {
    return null;
  }
}

function updateActions() {
  const chosen = selectedMovies(),
    keys = targetKeys(),
    isSource = $('operation').value === 'scrape_source',
    single = chosen.length === 1 && keys.length === 1;
  for (const id of ['movie-id', 'movie-url']) {
    $(id).disabled = !single;
    if (!single) $(id).value = '';
  }
  const url = $('movie-url').value.trim();
  const overlap = tasks.some(task => active(task) && (task.params?.keys || []).some(key => keys.includes(
    key))) || movies.some(movie => movie.status === 'running' && keys.includes(movie.key));
  const badUrl = isSource && url && linkSource(url) !== $('source').value;
  $('source-field').hidden = !isSource;
  $('precise').hidden = !isSource;
  $('selection-summary').textContent = chosen.length ? `已选 ${chosen.length} 部作品` : '';
  $('selection-summary').hidden = !chosen.length;
  $('clear-selection').hidden = !chosen.length;
  $('execute').disabled = executing || !keys.length || overlap || Boolean(badUrl);
  const hint = overlap ? '所选作品正在执行任务。' : badUrl ? '链接不属于所选来源。' : '';
  $('action-help').textContent = hint;
  $('action-help').hidden = !hint;
  const eligible = filteredMovies().filter(valid);
  $('select-filtered').disabled = !eligible.length || eligible.every(movie => selected.has(movie.path));
  refreshDropdowns();
}

function renderMovies() {
  const rows = filteredMovies(),
    pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  page = Math.min(page, pages);
  const visible = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    eligible = visible.filter(valid),
    scroll = $('movies').closest('.table-scroll'),
    top = scroll.scrollTop;
  $('movies').replaceChildren();
  for (const movie of visible) {
    const row = document.createElement('tr');
    row.dataset.status = movie.status;
    row.dataset.key = movie.key;
    row.dataset.path = movie.path;
    row.className = selected.has(movie.path) ? 'selected' : '';
    const cell = document.createElement('td');
    cell.className = 'check-cell';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.disabled = !valid(movie);
    check.checked = selected.has(movie.path);
    check.setAttribute('aria-label', `选择 ${movie.key}`);
    check.onchange = () => {
      check.checked ? selected.add(movie.path) : selected.delete(movie.path);
      row.classList.toggle('selected', check.checked);
      const checks = [...$('movies').querySelectorAll('input:not(:disabled)')],
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
    const link = text('button', movie.key, 'key-link');
    link.type = 'button';
    link.onclick = () => action(() => showMovieDetail(movie));
    key.append(link);
    const status = document.createElement('td');
    status.append(badge(movie.status));
    row.onclick = event => {
      if (!event.target.closest('button,a,input,label') && !window.getSelection()?.toString())
        showMovieDetail(movie);
    };
    row.append(cell, key, text('td', categoryNames[movie.category] || '未知', 'category-cell'), status);
    $('movies').append(row);
  }
  scroll.scrollTop = top;
  $('movie-empty').hidden = rows.length !== 0;
  $('select-page').disabled = !eligible.length;
  $('select-page').checked = eligible.length > 0 && eligible.every(movie => selected.has(movie.path));
  $('select-page').indeterminate = eligible.some(movie => selected.has(movie.path)) && !$('select-page')
    .checked;
  $('page-summary').textContent = rows.length ?
    `第 ${(page-1)*PAGE_SIZE+1}–${Math.min(page*PAGE_SIZE,rows.length)} 条 / 共 ${rows.length} 条` : '0 条';
  $('page-number').replaceChildren();
  for (let n = 1; n <= pages; n++) {
    const option = text('option', `第 ${n} / ${pages} 页`);
    option.value = n;
    $('page-number').append(option);
  }
  $('page-number').value = String(page);
  $('page-prev').disabled = page === 1;
  $('page-next').disabled = page === pages;

  updateActions();
}

function pageChange(value) {
  page = value;
  $('movies').closest('.table-scroll').scrollTop = 0;
  renderMovies();
}
$('page-prev').onclick = () => pageChange(page - 1);
$('page-next').onclick = () => pageChange(page + 1);
$('page-number').onchange = () => pageChange(Number($('page-number').value));
for (const id of ['search', 'status-filter', 'category-filter']) $(id).addEventListener(id === 'search' ?
  'input' : 'change', () => pageChange(1));
$('select-page').onchange = () => {
  for (const movie of filteredMovies().slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).filter(valid)) $(
    'select-page').checked ? selected.add(movie.path) : selected.delete(movie.path);
  renderMovies();
};
$('clear-selection').onclick = () => {
  selected.clear();
  renderMovies();
};
$('select-filtered').onclick = () => {
  for (const movie of filteredMovies().filter(valid)) selected.add(movie.path);
  renderMovies();
};
$('operation').onchange = updateActions;
$('source').onchange = () => {
  $('movie-id').value = '';
  if ($('movie-url').value && linkSource($('movie-url').value) !== $('source').value) $('movie-url').value =
    '';
  updateActions();
};
$('movie-id').oninput = updateActions;
$('movie-url').oninput = () => {
  const source = linkSource($('movie-url').value);
  if (source && source !== $('source').value) {
    $('source').value = source;
    $('movie-id').value = '';
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
  const keys = targetKeys(),
    type = $('operation').value;
  if (!keys.length) return;
  if (type === 'force_update' || keys.length > 100)
    if (!await confirmAction(taskNames[type],
        `处理所选 ${keys.length} 部作品。${type==='force_update'?'\n将重新抓取并更新已有信息。':''}`)) return;
  executing = true;
  updateActions();
  try {
    const body = {
      type,
      keys
    };
    if (type === 'scrape_source') {
      body.source_id = $('source').value;
      if ($('movie-id').value.trim()) body.external_id = $('movie-id').value.trim();
      if ($('movie-url').value.trim()) body.source_url = $('movie-url').value.trim();
    }
    await api('tasks', 'POST', body);
    taskPage = 1;
    $('task-filter').value = 'all';
    switchTab('tasks');
    notice('任务已提交。');
    await reload();
  } finally {
    executing = false;
    updateActions();
  }
});
$('scan').onclick = async () => {
  clearTimeout(scanTimer);
  $('scan').disabled = true;
  const feedback = $('scan-feedback');
  feedback.classList.remove('error');
  feedback.textContent = '正在扫描…';
  feedback.title = '';
  try {
    await api('scan', 'POST');
    await reload();
    feedback.textContent = '扫描完成';
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
  if (!movies.some(movie => movie.key === item.key && valid(movie))) {
    notice('该作品已移除或存在命名冲突，无法选择候选。', true);
    return;
  }
  selected.clear();
  selected.add(movies.find(movie => movie.key === item.key && valid(movie)).path);
  $('operation').value = 'scrape_source';
  $('source').value = source.source;
  $('movie-id').value = candidate.external_id || '';
  $('movie-url').value = candidate.url || '';
  page = 1;
  renderMovies();
  switchTab('library');
  notice('候选已填入。');
}

const stageNames = {
  source: '数据源',
  challenge: '过墙',
  translation: '翻译',
  metadata: '保存信息',
  images: '下载图片',
  portraits: '演员头像',
  crop: '裁剪封面',
  retry: '重试',
  finished: '完成'
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
  identity_mismatch: '番号不符',
  unsupported: '不支持',
  success: '已完成',
  failed: '失败'
};

function processList(events, running = false) {
  const list = document.createElement('ol');
  list.className = 'process-list';
  for (const event of events || []) {
    const row = document.createElement('li');
    const time = new Date(event.time).toLocaleTimeString('zh-CN', {
      hour12: false
    });
    const name = event.source ?
      `${sourceLabel(event.source)}${event.message ? ' · '+event.message : ''}` : stageNames[
        event.stage] || event.stage;
    row.append(text('time', time), text('span', name), text('span', event.status === 'running' ? running &&
      event === events.at(-1) ? '进行中' : '已执行' : sourceStatusNames[event.status] || event.status, 'muted'));
    list.append(row);
  }
  return list;
}

function renderMovieProcess(history) {
  $('movie-process').replaceChildren();
  const current = history?.[0];
  if (!current) return;
  const heading = document.createElement('div');
  heading.className = 'section-heading';
  const link = text('a', '抓取过程', 'key-link');
  link.href = '#tasks/' + current.task_id;
  const stage = current.stage;
  heading.append(link, text('span', current.status === 'running' ? (stage?.source ? sourceLabel(stage
      .source) : stageNames[stage?.stage]) || '进行中' : current.status === 'pending' ? '等待中' :
    sourceStatusNames[current.status] || '已结束', 'muted'));
  $('movie-process').append(heading, processList(current.events, current.status === 'running'));
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
    const movieLink = text('button', item.key, 'key-link');
    movieLink.onclick = () => openMovieKey(item.key);
    heading.append(movieLink, badge(item.status));
    box.onclick = event => {
      if (!event.target.closest('button,a,input') && !window.getSelection()?.toString()) openMovieKey(item
        .key);
    };
    if (showHeading) box.append(heading);
    if (item.stage && item.status === 'running') box.append(text('p', item.stage.source ? sourceLabel(item
      .stage.source) : stageNames[item.stage.stage], 'muted'));
    if (item.events?.length) box.append(processList(item.events, item.status === 'running'));
    const result = item.result || {},
      notices = [result.error, ...(result.warnings || []), ...(result.translation_errors || []).map(e =>
          '翻译：' + e.error), ...(result.actor_issues || []).map(e => '演员名称待确认：' + e.name), ...(result.images ||
          []).filter(e => e.status === 'error').map(e => '图片：' + (e.message || e.error)),
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
        choice.append(text('div', `${candidate.number} · ${candidate.title||''}`), text('p',
          `ID ${candidate.external_id||'—'} · ${candidate.url||''}`));
        const button = text('button', '选择此候选', 'button');
        button.onclick = () => chooseCandidate(item, source, candidate);
        choice.append(button);
        box.append(choice);
      }
    }
    container.append(box);
  }
}

function taskTitle(task) {
  const when = new Date(task.created_at).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
  return `${taskNames[task.type]||task.type} · ${when}`;
}

function taskBadge(task) {
  return text('span', task.status === 'pending' ? '等待中' : task.status === 'running' ? '进行中' : '已结束',
    'badge ' + (active(task) ? 'active' : ''));
}

function taskWhen(task) {
  return new Date(task.created_at).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
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
  if (!active(task) && task.failed) {
    const retry = text('button', '重试失败作品', 'button');
    retry.onclick = event => {
      event.stopPropagation();
      void action(async () => {
        retry.disabled = true;
        try {
          await api('tasks/' + task.id + '/retry', 'POST');
          await reload();
        } finally {
          retry.disabled = false;
        }
      });
    };
    controls.append(retry);
  }
  const remove = text('button', active(task) ? '取消任务' : '删除任务', 'button quiet');
  remove.onclick = event => {
    event.stopPropagation();
    void action(async () => {
      if (!await confirmAction(active(task) ? '取消任务' : '删除任务', active(task) ?
          '任务将从列表移除。当前作品处理结束后停止，已保存的元数据保留。' : '移除这条任务记录，已保存的元数据保留。')) return;
      await api('tasks/' + task.id, 'DELETE');
      if (detailId === task.id) switchTab('tasks');
      await reload();
    });
  };
  controls.append(remove);
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
    const type = document.createElement('td'),
      link = text('a', taskNames[task.type] || task.type, 'task-link');
    link.href = '#tasks/' + task.id;
    type.append(link);
    if (task.params?.source_id) type.append(text('small', sourceNames[task.params.source_id] || task.params
      .source_id, 'muted'));
    const process = document.createElement('td');
    process.className = 'task-process';
    process.append(text('span', `${task.progress} / ${task.total}`), text('small',
      task.status === 'running' && task.current ?
      `${task.current.key} · ${task.current.source ? sourceLabel(task.current.source) : stageNames[task.current.stage] || '进行中'}` :
      `成功 ${task.success} · 失败 ${task.failed}`, 'muted'));
    const status = document.createElement('td');
    status.append(taskBadge(task));
    const controls = document.createElement('td');
    controls.append(taskButtons(task));
    row.append(type, text('td', taskWhen(task), 'task-date'), text('td', task.total), process, status,
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
  if (!detailId || currentTab !== 'tasks') return;
  const id = detailId,
    request = ++detailRequest;
  try {
    const record = await api('tasks/' + id + '?page=' + resultPage + '&status=' + $('result-filter').value +
      '&query=' + encodeURIComponent($('result-search').value.trim()));
    if (request !== detailRequest || detailId !== id || currentTab !== 'tasks') return;
    resultPage = record.page;
    $('task-detail-title').textContent = taskTitle(record);
    $('task-detail-status').replaceChildren(taskBadge(record));
    $('task-detail-summary').textContent =
      `已处理 ${record.progress} / ${record.total} · 成功 ${record.success} · 失败 ${record.failed}`;
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
    if (request !== detailRequest || id !== detailId) return;
    if (error.message === 'not found') {
      switchTab('tasks');
      await reload();
    } else throw error;
  }
}
$('task-back').onclick = () => {
  location.hash = 'tasks';
};

function changeResultPage(page) {
  resultPage = page;
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
  const query = $('actor-search').value.trim().toLowerCase(),
    filter = $('actor-filter').value,
    portraitFilter = $('actor-portrait-filter').value;
  const rows = actors.filter(issue => (filter === 'all' || issue.status === filter) && (portraitFilter ===
    'all' || issue.portrait_status === portraitFilter) && (!query || [issue.name,
      ...(issue.candidates || [])
    ].join(' ').toLowerCase()
    .includes(query)));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  actorPage = Math.min(actorPage, pages);
  $('actors').replaceChildren();
  $('actors-summary').textContent = `${actors.length} 位演员`;
  $('actor-fill').disabled = busy() || !actors.some(actor => actor.keys?.length);
  for (const issue of rows.slice((actorPage - 1) * PAGE_SIZE, actorPage * PAGE_SIZE)) {
    const row = document.createElement('tr'),
      name = text('td', issue.name, 'actor-name');
    const status = document.createElement('td');
    const labels = {
      known: '已收录',
      ambiguous: '共享名称',
      unknown: '未收录'
    };
    status.append(text('span', labels[issue.status] || '未收录', 'badge ' + (issue.status === 'known' ? '' :
      'warn')));
    const avatar = document.createElement('td');
    if (issue.portrait) {
      const image = document.createElement('img');
      image.src = '/api/actors/' + encodeURIComponent(issue.name) + '/portrait';
      image.alt = issue.name;
      image.loading = 'lazy';
      image.className = 'actor-portrait';
      avatar.append(image);
    } else avatar.append(text('span', '缺少头像', 'muted'));
    const operation = document.createElement('td'),
      button = text('button', issue.portrait ? '更新头像' : '获取头像', 'button quiet');
    button.disabled = busy() || !issue.keys?.length || issue.status === 'ambiguous';
    button.onclick = () => action(async () => {
      await api('tasks', 'POST', {
        type: 'actor_images',
        keys: issue.keys,
        actor_names: [issue.name],
        replace_avatars: issue.portrait
      });
      await reload();
    });
    operation.append(button);
    row.append(avatar, name, status, text('td', (issue.candidates || []).join('、') || '—',
      'actor-candidates'), text(
      'td', issue.count ?? 1), operation);
    $('actors').append(row);
  }
  $('actor-empty').hidden = rows.length !== 0;
  $('actor-page-summary').textContent = `第 ${actorPage} / ${pages} 页 · ${rows.length} 个名称`;
  $('actor-prev').disabled = actorPage === 1;
  $('actor-next').disabled = actorPage === pages;
}
$('actor-fill').onclick = () => action(async () => {
  const keys = [...new Set(actors.filter(actor => actor.status !== 'ambiguous').flatMap(actor => actor
    .keys || []))];
  await api('tasks', 'POST', {
    type: 'actor_images',
    keys
  });
  await reload();
});
for (const id of ['actor-search', 'actor-filter', 'actor-portrait-filter']) $(id).addEventListener(id ===
  'actor-search' ? 'input' :
  'change', () => {
    actorPage = 1;
    renderActors();
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
function settingsFingerprint() {
  return JSON.stringify([...$('settings').querySelectorAll('input,select,textarea')].map(field => [field.id ||
    field.getAttribute('aria-label') || field.className, field.type === 'checkbox' ? field.checked :
    field.value
  ]));
}

function settingsChanged() {
  return Boolean(config && settingsSnapshot !== null && settingsFingerprint() !== settingsSnapshot);
}

function updateSettingsState() {
  const writing = busy();
  $('save-settings').disabled = saving || writing || !settingsChanged();
  $('save-settings').title = writing ? '任务处理结束后可保存' : '';
}
async function reload() {
  if (reloading) return;
  reloading = true;
  try {
    const [nextMovies, nextTasks, nextActors] = await Promise.all([api('movies'), api('tasks'),
      currentTab === 'actors' ? api('actors/library') : Promise.resolve(actors)
    ]);
    const changedMovies = JSON.stringify(movies) !== JSON.stringify(nextMovies),
      changedTasks = JSON.stringify(tasks) !== JSON.stringify(nextTasks),
      changedActors = JSON.stringify(actors) !== JSON.stringify(nextActors);
    movies = nextMovies;
    tasks = nextTasks;
    actors = nextActors;
    const paths = new Set(movies.filter(valid).map(movie => movie.path));
    for (const path of selected)
      if (!paths.has(path)) selected.delete(path);
    const runningCount = tasks.filter(active).length;
    $('task-count').textContent = runningCount;
    $('task-count').hidden = !runningCount;
    $('scan-summary').textContent = `${movies.length} 部作品`;
    if (changedMovies || !$('movies').children.length) renderMovies();
    if (changedTasks) {
      renderTasks();
      if (detailId) void action(loadTaskDetail);
    }
    if (changedActors || changedTasks) renderActors();
    updateActions();
    updateSettingsState();
    $('scan').disabled = busy();
    updateMovieState();
    if (moviePath && movieData && (busy() || movieData.history?.some(item => ['pending', 'running']
        .includes(item.status)))) {
      const path = moviePath;
      const detail = await api('movies/detail?path=' + encodeURIComponent(path));
      if (path === moviePath) {
        movieData.history = detail.history;
        renderMovieProcess(detail.history);
        $('movie-detail-status').replaceChildren(badge(detail.status));
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
      stored = config.sites[site] || {},
      defaultURL = config.source_defaults[site],
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
        config.source_intervals[site]) || ('cookie' in draft && draft.cookie !== (stored.cookie || '')))
      siteDrafts[site] = draft;
  }
}

function renderSites() {
  $('site-rows').replaceChildren();
  for (const [site, name] of Object.entries(sourceNames)) {
    const stored = config.sites[site] || {},
      row = document.createElement('tr');
    row.dataset.source = site;
    row.append(text('td', name));
    const address = document.createElement('input');
    address.type = 'url';
    address.required = true;
    address.className = 'site-url';
    address.value = stored.base_url || config.source_defaults[site];
    address.setAttribute('aria-label', name + ' 站点地址');
    const interval = document.createElement('input');
    interval.type = 'number';
    interval.min = 0;
    interval.required = true;
    interval.className = 'site-interval';
    interval.value = stored.interval_seconds ?? config.source_intervals[site];
    interval.setAttribute('aria-label', name + ' 请求间隔');
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
      cookie.setAttribute('aria-label', 'JavDB Cookie');
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

function applyConfig(value) {
  config = value;
  siteDrafts = {};
  $('translation').checked = value.translation.enabled;
  $('actor-images').checked = value.actor_images;
  $('endpoint').value = value.translation.endpoint;
  $('model').value = value.translation.model;
  showSecret('api-key', value.translation.api_key);
  $('solver-url').value = value.flaresolverr_url || '';
  for (const [id, field] of Object.entries(settingFields)) $(id).value = value[field];
  for (const [id, field] of Object.entries(translationFields)) $(id).value = value.translation[field];
  for (const category of Object.keys(categoryNames)) $('route-' + category).value = value.routes[category]
    .join(', ');
  renderMediaRoots(value.media_roots);
  $('metadata-root').value = value.metadata_root || '';
  renderSites();
  settingsSnapshot = settingsFingerprint();
  updateSettingsState();
  refreshDropdowns();
}
$('settings').addEventListener('input', updateSettingsState);
$('settings').addEventListener('change', updateSettingsState);

function validateSeconds(value, name, zero = false) {
  if (!Number.isFinite(value) || (value === 0 ? !zero : value < 0 || (value !== 1 && value % 5 !== 0)))
    throw Error(`${name}应为 1 秒或 5 的倍数${zero?'，也可设为 0':''}。`);
}

function settingsValue() {
  storeSiteDraft();
  const value = {
    media_roots: [...document.querySelectorAll('.media-path')].map(input => input.value.trim()).filter(
      Boolean),
    metadata_root: $('metadata-root').value.trim() || null,
    actor_images: $('actor-images').checked,
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
    if (saving || busy() || !settingsChanged()) return;
    const value = settingsValue();
    for (const id of ['interval', 'timeout', 'retry-delay', 'translation-timeout']) validateSeconds(
      Number($(id).value), $(id).closest('label').textContent.trim().replace(/\s+/g, ' '));
    validateSeconds(value.translation.interval_seconds, '翻译请求间隔', true);
    for (const draft of Object.values(siteDrafts)) validateSeconds(draft.interval_seconds, '数据源请求间隔',
      true);
    if (value.translation.enabled && (!value.translation.model || !value.translation.endpoint || !(
        value.translation.api_key ?? config.translation.api_key))) throw Error(
      '启用翻译前，请填写 baseURL、模型和 apiKey。');
    if (!value.media_roots.length || !value.metadata_root) throw Error('请选择作品扫描目录和元数据保存目录。');
    if (config.metadata_root && config.metadata_root !== value.metadata_root && !await confirmAction(
        '切换元数据保存目录', '现有资料保留在原目录。')) return;
    saving = true;
    updateSettingsState();
    try {
      const saved = await api('config', 'PUT', value);
      applyConfig(saved);
      notice('已保存。');
      await reload();
    } finally {
      saving = false;
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
  input.placeholder = '视频与 STRM 目录';
  input.setAttribute('aria-label', '作品扫描目录');
  const browse = text('button', '浏览', 'button');
  browse.type = 'button';
  browse.onclick = () => action(() => openDirectory('media', input.value, path => {
    input.value = path;
    updateSettingsState();
  }));
  const remove = text('button', '×', 'button quiet');
  remove.type = 'button';
  remove.setAttribute('aria-label', '移除此目录');
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
  directoryCallback = null;
async function loadDirectory(path) {
  $('directory-error').hidden = true;
  try {
    const view = await api('directories?kind=' + directoryKind + (path ? '&path=' + encodeURIComponent(
      path) : ''));
    directoryView = view;
    $('directory-path').textContent = view.path || '';
    $('directory-up').disabled = !view.path;
    $('directory-list').replaceChildren();
    for (const entry of view.directories) {
      const button = text('button', entry.name, 'directory-entry');
      button.type = 'button';
      button.onclick = () => void loadDirectory(entry.path);
      $('directory-list').append(button);
    }
    if (!view.directories.length) $('directory-list').append(text('p', '没有子目录', 'muted'));
    $('directory-choose').disabled = !view.path;
    $('directory-create').hidden = directoryKind !== 'metadata' || !view.path;
  } catch (error) {
    $('directory-error').textContent = error.message;
    $('directory-error').hidden = false;
    $('directory-choose').disabled = true;
  }
}
async function openDirectory(kind, path, callback) {
  directoryKind = kind;
  directoryCallback = callback;
  directoryView = null;
  $('directory-up').disabled = true;
  $('directory-title').textContent = '选择目录';
  $('directory-dialog').showModal();
  await loadDirectory(path || null);
  if (!directoryView) await loadDirectory(null);
}
$('directory-close').onclick = () => $('directory-dialog').close();
$('directory-up').onclick = () => void loadDirectory(directoryView.parent);
$('directory-choose').onclick = () => {
  directoryCallback(directoryView.path);
  $('directory-dialog').close();
};
$('create-folder').onclick = async () => {
  const name = $('new-folder-name').value.trim();
  if (!name) return;
  try {
    const created = await api('directories', 'POST', {
      parent: directoryView.path,
      name
    });
    $('new-folder-name').value = '';
    await loadDirectory(created.path);
  } catch (error) {
    $('directory-error').textContent = error.message;
    $('directory-error').hidden = false;
  }
};

function showMovieDetail(movie) {
  if (movie.path === moviePath) return;
  movieReturn = location.hash || '#library';
  location.hash = 'library/movie/' + encodeURIComponent(movie.path);
}

function openMovieKey(key) {
  const movie = movies.find(item => item.key === key);
  if (movie) showMovieDetail(movie);
  else notice('作品已不在媒体库中。', true);
}

function movieFingerprint() {
  return JSON.stringify([...$('movie-fields').querySelectorAll('input,textarea')].map(input => input.value));
}

function movieUnsaved() {
  return Boolean(moviePath && movieSnapshot !== null && movieFingerprint() !== movieSnapshot);
}

function updateMovieState() {
  $('movie-back').disabled = movieSaving;
  $('movie-save').disabled = !movieData?.editable || busy() || movieSaving || cropBusy || !movieUnsaved();
  $('crop-open').disabled = !movieData?.editable || !movieData?.artwork.length || busy() || movieSaving || cropBusy;
  $('crop-save').disabled = !cropped || cropBusy || movieSaving || busy();
  for (const input of $('movie-fields').querySelectorAll('input,textarea'))
    input.disabled = !movieData?.editable || movieSaving || busy();
  for (const id of ['crop-source', 'crop-detect', 'crop-center', 'crop-x', 'crop-y'])
    $(id).disabled = cropBusy || movieSaving || busy();
  $('crop-close').disabled = movieSaving;
}
const movieFields = [
  ['title', '标题', 'textarea'],
  ['original_title', '原标题', 'textarea'],
  ['plot', '介绍', 'textarea'],
  ['release_date', '发行日期', 'date'],
  ['runtime_minutes', '时长（分钟）', 'number'],
  ['studio', '片商', 'text'],
  ['publisher', '发行商', 'text'],
  ['label', '厂牌', 'text'],
  ['series', '系列', 'text'],
  ['directors', '导演', 'list'],
  ['actors', '演员', 'list'],
  ['genres', '类型', 'list'],
  ['tags', '标签', 'list']
];

function artworkURL(file) {
  return '/api/movies/' + encodeURIComponent(movieData.key) + '/artwork?file=' + encodeURIComponent(file) +
    '&v=' + Date.now();
}

function renderMovieDetail(detail) {
  movieData = detail;
  cropRequest++;
  cropBusy = false;
  cropped = null;
  $('movie-detail-title').textContent = detail.key;
  $('movie-detail-summary').textContent =
    `${categoryNames[detail.category] || '未知'} · ${detail.media_files.length} 个媒体文件`;
  $('movie-detail-status').replaceChildren(badge(detail.status));
  $('movie-loading').hidden = true;
  $('movie-content').hidden = false;
  $('crop-panel').hidden = true;
  const metadata = detail.metadata || {};
  $('movie-fields').replaceChildren();
  for (const [field, name, type] of movieFields) {
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
      .join('\n') : value ?? (field === 'title' ? detail.key : '');
    input.disabled = !detail.editable;
    input.required = field === 'title';
    label.className = ['title', 'original_title', 'plot'].includes(field) ? 'span-full' : '';
    label.append(input);
    $('movie-fields').append(label);
  }
  const poster = detail.artwork.find(file => /-poster\.(jpg|jpeg|png|webp|gif)$/i.test(file));
  $('movie-poster').hidden = !poster;
  $('poster-empty').hidden = Boolean(poster);
  if (poster) $('movie-poster').src = artworkURL(poster);
  else $('movie-poster').removeAttribute('src');
  $('movie-gallery').replaceChildren();
  for (const file of detail.artwork) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'image-tile';
    button.title = file;
    const image = document.createElement('img');
    image.src = artworkURL(file);
    image.alt = file;
    image.loading = 'lazy';
    button.append(image);
    button.onclick = () => openCrop(file);
    $('movie-gallery').append(button);
  }
  $('movie-files').replaceChildren(text('h3', '文件'), text('p', detail.path, 'detail-path'));
  for (const file of detail.media_files) $('movie-files').append(text('p', file.split('/').pop(),
    'detail-path'));
  $('movie-diagnostics').replaceChildren();
  renderResults($('movie-diagnostics'), {
    items: [{
      key: detail.key,
      status: detail.status,
      result: {
        ...detail.last_result,
        warnings: detail.warnings
      }
    }]
  }, false);
  if (detail.metadata_error) $('movie-diagnostics').append(text('p', detail.metadata_error, 'error'));
  renderMovieProcess(detail.history);
  movieSnapshot = movieFingerprint();
  updateMovieState();
}
async function loadMovieDetail() {
  const path = moviePath,
    request = ++movieRequest;
  movieSnapshot = null;
  movieData = null;
  $('movie-detail-title').textContent = movies.find(movie => movie.path === path)?.key || '作品详情';
  $('movie-detail-summary').textContent = '';
  $('movie-detail-status').replaceChildren();
  $('movie-loading').hidden = false;
  $('movie-loading').textContent = '正在读取…';
  $('movie-content').hidden = true;
  try {
    const detail = await api('movies/detail?path=' + encodeURIComponent(path));
    if (path !== moviePath || request !== movieRequest) return;
    renderMovieDetail(detail);
  } catch (error) {
    if (path !== moviePath || request !== movieRequest) return;
    $('movie-loading').textContent = error.message;
  }
}
$('movie-back').onclick = () => {
  location.hash = movieReturn.slice(1);
};
$('movie-form').addEventListener('input', updateMovieState);
$('movie-form').onsubmit = event => {
  event.preventDefault();
  void action(async () => {
    if ($('movie-save').disabled) return;
    const data = {},
      path = moviePath,
      key = movieData.key;
    for (const [field, , type] of movieFields) {
      const value = $('edit-' + field).value;
      data[field] = type === 'list' ? [...new Set(value.split('\n').map(s => s.trim()).filter(
          Boolean))] :
        type === 'number' ? value === '' ? null : Number(value) : value.trim() || null;
      if (field === 'actors') data[field] = data[field].map(name => movieData.metadata?.actors.find(
        actor => actor.name === name) || {
        name
      });
    }
    const oldValues = JSON.parse(movieSnapshot),
      fields = [...$('movie-fields').querySelectorAll('input,textarea')];
    for (const [index, [field]] of movieFields.entries())
      if (oldValues[index] === fields[index].value) delete data[field];
    const preview = movieFields.flatMap(([field, name], index) => oldValues[index] !== fields[index]
        .value ? [`${name}\n原：${oldValues[index] || '—'}\n新：${fields[index].value || '—'}`] : [])
      .join('\n\n');
    if (!await confirmAction('修改预览', preview)) return;
    if (path !== moviePath || busy() || movieSaving || cropBusy) return;
    movieSaving = true;
    updateMovieState();
    try {
      const detail = await api('movies/' + encodeURIComponent(key) + '/metadata', 'PUT', data);
      if (moviePath === path) renderMovieDetail(detail);
      notice(detail.last_result?.translation_errors?.length ? '信息已保存，翻译未完成。' : '已保存。', Boolean(detail
        .last_result?.translation_errors?.length));
      await reload();
    } finally {
      movieSaving = false;
      updateMovieState();
    }
  });
};

function openCrop(file) {
  if (!movieData?.editable || busy() || movieSaving || cropBusy) return;
  $('crop-source').replaceChildren(...movieData.artwork.map(name => {
    const option = text('option', name);
    option.value = name;
    return option;
  }));
  $('crop-source').value = file || movieData.artwork.find(name => /-fanart\./i.test(name)) || movieData
    .artwork[0];
  cropped = null;
  $('crop-panel').hidden = false;
  initDropdowns();
  void action(() => previewCrop(false));
}

function drawCropOriginal(file, box) {
  const image = new Image(),
    path = moviePath,
    ticket = cropRequest;
  image.onload = () => {
    if (path !== moviePath || ticket !== cropRequest || file !== $('crop-source').value) return;
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
  const path = moviePath,
    file = $('crop-source').value,
    ticket = ++cropRequest;
  if (!movieData || movieSaving) return;
  cropBusy = true;
  updateMovieState();
  $('crop-status').textContent = '正在裁剪…';
  try {
    const request = {
      file,
      center
    };
    if (manual && cropped) {
      request.box = [...cropped.box];
      request.box[0] = Number($('crop-x').value);
      request.box[1] = Number($('crop-y').value);
      request.fingerprint = cropped.fingerprint;
    }
    const result = await api('movies/' + encodeURIComponent(movieData.key) + '/crop', 'POST', request);
    if (path !== moviePath || ticket !== cropRequest || file !== $('crop-source').value) return;
    cropped = {
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
    if (ticket === cropRequest && path === moviePath) {
      cropped = null;
      $('crop-preview').hidden = true;
      $('crop-status').textContent = error.message;
    }
  } finally {
    if (ticket === cropRequest) {
      cropBusy = false;
      updateMovieState();
    }
  }
}
$('crop-open').onclick = () => openCrop();
$('crop-close').onclick = () => {
  if (movieSaving) return;
  cropRequest++;
  clearTimeout(cropTimer);
  cropBusy = false;
  $('crop-panel').hidden = true;
  cropped = null;
  updateMovieState();
};
$('crop-source').onchange = () => {
  cropped = null;
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
  if (!cropped || cropBusy || movieSaving || busy()) return;
  const path = moviePath,
    result = cropped;
  movieSaving = true;
  updateMovieState();
  try {
    const detail = await api('movies/' + encodeURIComponent(movieData.key) + '/crop', 'PUT', {
      file: result.file,
      box: result.box,
      fingerprint: result.fingerprint
    });
    if (moviePath === path) {
      if (movieUnsaved()) {
        movieData.artwork = detail.artwork;
        $('movie-poster').src = artworkURL(detail.artwork.find(file => /-poster\./i.test(file)));
        $('movie-poster').hidden = false;
        $('poster-empty').hidden = true;
      } else renderMovieDetail(detail);
      $('crop-panel').hidden = true;
      cropped = null;
    }
    notice('封面已保存。');
  } finally {
    movieSaving = false;
    updateMovieState();
  }
});
window.addEventListener('beforeunload', event => {
  if (movieSaving || movieUnsaved()) {
    event.preventDefault();
    event.returnValue = '';
  }
});
void action(async () => {
  applyConfig(await api('config'));
  if (!config.configured) switchTab('settings');
  await reload();
  renderActors();
  renderTasks();
});
setInterval(() => {
  void action(reload);
}, 5000);
