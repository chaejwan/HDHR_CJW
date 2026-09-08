'use strict';

/* 채용공고 모니터 — GitHub Pages 설정 화면
 *
 * 저장소의 job-monitor/config.json 을 읽고 쓰는 화면입니다.
 * - 읽기: 토큰이 없으면 raw.githubusercontent.com 에서 그냥 읽습니다(공개 저장소).
 * - 쓰기: GitHub API 로 config.json 을 커밋합니다. 본인 토큰이 필요합니다.
 * - 실제 확인은 GitHub Actions 워크플로(.github/workflows/job-monitor.yml)가 합니다.
 */

const PATHS = {
  config: 'job-monitor/config.json',
  state: 'job-monitor/data/state.json',
  lastRun: 'job-monitor/data/last-run.json',
  emailStatus: 'job-monitor/data/email-status.json',
};
const WORKFLOW_FILE = 'job-monitor.yml';
const LS_TOKEN = 'jobmon.token';
const LS_REPO = 'jobmon.repo';
const API = 'https://api.github.com';

const DEFAULT_CONFIG = {
  check_interval_hours: 24,
  notify_on_first_run: false,
  recipients: [],
  email: {
    enabled: false, smtp_host: 'smtp.gmail.com', smtp_port: 587, security: 'starttls',
    username: '', password: '', password_env: 'JOBMON_SMTP_PASSWORD', from_addr: '',
    subject_prefix: '[채용 알림]',
  },
  request: { timeout_sec: 20 },
  sites: [],
};

const $ = (sel) => document.querySelector(sel);
const el = {};
['statusLine', 'saveBtn', 'runBtn', 'refreshBtn', 'addSiteBtn', 'siteList', 'notice', 'runBox',
  'intervalInput', 'firstRunInput', 'emailEnabled', 'recipientsInput', 'smtpHost', 'smtpPort',
  'smtpSecurity', 'smtpUser', 'smtpFrom', 'subjectPrefix', 'tokenInput', 'tokenSaveBtn',
  'tokenClearBtn', 'tokenState', 'copyJsonBtn', 'editOnGithub', 'actionsLink', 'secretsLink',
  'feed', 'sourceLine', 'toast', 'modal', 'modalTitle', 'modalBody', 'siteTemplate',
].forEach((id) => { el[id] = document.getElementById(id) || $('#' + id); });

let repo = null;         // {owner, repo}
let branch = '';         // 기본 브랜치
let cfg = null;          // 편집 중인 설정
let configSha = '';      // 저장할 때 필요한 파일 버전
let state = null;        // data/state.json
let lastRun = null;      // data/last-run.json
let emailStatus = null;  // data/email-status.json (마지막 메일 발송 결과)
let dirty = false;
let busy = false;

/* ------------------------------------------------------------ 유틸 */

function toast(message, isError) {
  el.toast.textContent = message;
  el.toast.classList.toggle('toast--err', !!isError);
  el.toast.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.toast.hidden = true; }, isError ? 7000 : 3200);
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function openModal(title, html) {
  el.modalTitle.textContent = title;
  el.modalBody.innerHTML = html;
  el.modal.hidden = false;
}

function markDirty() { dirty = true; el.saveBtn.disabled = false; el.saveBtn.textContent = '설정 저장 *'; }
function markClean() { dirty = false; el.saveBtn.disabled = true; el.saveBtn.textContent = '설정 저장'; }

function setBusy(on, label) {
  busy = on;
  el.runBtn.disabled = on;
  el.refreshBtn.disabled = on;
  el.addSiteBtn.disabled = on;
  el.saveBtn.disabled = on || !dirty;
  if (on && label) el.statusLine.textContent = label;
}

function fmtTime(iso) {
  if (!iso) return '없음';
  const date = new Date(iso);
  if (isNaN(date)) return iso;
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fmtInterval(hours) {
  const value = Number(hours);
  if (!value) return '';
  if (value >= 24 && value % 24 === 0) return `${value / 24}일마다`;
  if (value < 1) return `${Math.round(value * 60)}분마다`;
  return `${value}시간마다`;
}

function getPath(obj, path) {
  return path.split('.').reduce((acc, key) => (acc == null ? acc : acc[key]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split('.');
  const last = keys.pop();
  let target = obj;
  keys.forEach((key) => {
    if (typeof target[key] !== 'object' || target[key] === null) target[key] = {};
    target = target[key];
  });
  target[last] = value;
}

function toBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary);
}

function fromBase64(b64) {
  const binary = atob((b64 || '').replace(/\s/g, ''));
  const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
  return new TextDecoder('utf-8').decode(bytes);
}

function slugify(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
}

function makeSiteId(site, taken) {
  const base = slugify(site.name) || slugify((site.url || '').replace(/^https?:\/\/(www\.)?/, '')) || 'site';
  let candidate = base;
  let n = 2;
  while (taken.has(candidate)) candidate = `${base}-${n++}`;
  return candidate;
}

/* ------------------------------------------------------------ GitHub 접근 */

function getToken() { return localStorage.getItem(LS_TOKEN) || ''; }

function detectRepo() {
  const params = new URLSearchParams(location.search);
  const fromQuery = params.get('repo');
  if (fromQuery && fromQuery.includes('/')) {
    const [owner, name] = fromQuery.split('/');
    return { owner, repo: name };
  }
  const host = location.hostname;
  const segments = location.pathname.split('/').filter(Boolean);
  if (host.endsWith('.github.io')) {
    const owner = host.replace('.github.io', '');
    // 프로젝트 페이지(/저장소이름/monitor/)면 첫 칸이 저장소 이름, 사용자 페이지면 host 자체가 저장소.
    const name = segments.length >= 2 ? segments[0] : host;
    return { owner, repo: name };
  }
  const stored = localStorage.getItem(LS_REPO);
  if (stored) { try { return JSON.parse(stored); } catch (err) { /* 무시 */ } }
  return null;
}

async function gh(path, options) {
  const token = getToken();
  const headers = Object.assign({ Accept: 'application/vnd.github+json' }, (options || {}).headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(API + path, Object.assign({}, options, { headers }));
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && data.message) || `HTTP ${res.status}`;
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  return data;
}

async function readRepoFile(path) {
  // 토큰이 있으면 API 로 (sha 도 함께), 없으면 raw 로 읽는다.
  if (getToken()) {
    try {
      const data = await gh(`/repos/${repo.owner}/${repo.repo}/contents/${path}?ref=${encodeURIComponent(branch)}`);
      return { text: fromBase64(data.content), sha: data.sha };
    } catch (err) {
      if (err.status === 404) return { text: '', sha: '', missing: true };
      throw err;
    }
  }
  const url = `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${branch}/${path}?t=${Date.now()}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (res.status === 404) return { text: '', sha: '', missing: true };
  if (!res.ok) throw new Error(`파일을 읽지 못했습니다 (${path}): HTTP ${res.status}`);
  return { text: await res.text(), sha: '' };
}

async function readJsonFile(path) {
  const file = await readRepoFile(path);
  if (file.missing || !file.text.trim()) return { data: null, sha: file.sha, missing: true };
  try {
    return { data: JSON.parse(file.text), sha: file.sha };
  } catch (err) {
    throw new Error(`${path} 의 JSON 형식이 잘못되었습니다: ${err.message}`);
  }
}

async function writeConfigFile(text) {
  // 저장 직전에 최신 sha 를 다시 확인해 다른 사람이 바꾼 내용을 덮어쓰지 않게 한다.
  let sha = configSha;
  try {
    const current = await gh(`/repos/${repo.owner}/${repo.repo}/contents/${PATHS.config}?ref=${encodeURIComponent(branch)}`);
    sha = current.sha;
  } catch (err) {
    if (err.status !== 404) throw err;
    sha = '';
  }
  const body = {
    message: '채용공고 모니터 설정 변경',
    content: toBase64(text),
    branch,
  };
  if (sha) body.sha = sha;
  const result = await gh(`/repos/${repo.owner}/${repo.repo}/contents/${PATHS.config}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  configSha = (result && result.content && result.content.sha) || '';
  return result;
}

async function dispatchWorkflow() {
  await gh(`/repos/${repo.owner}/${repo.repo}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
    method: 'POST',
    body: JSON.stringify({ ref: branch, inputs: { check_all: 'true', no_email: 'false' } }),
  });
}

/* ------------------------------------------------------------ 설정 화면 */

function normalizeConfig(raw) {
  const out = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  Object.assign(out, raw || {});
  out.email = Object.assign({}, DEFAULT_CONFIG.email, (raw && raw.email) || {});
  out.request = Object.assign({}, DEFAULT_CONFIG.request, (raw && raw.request) || {});
  out.recipients = Array.isArray(out.recipients) ? out.recipients : [];
  const taken = new Set();
  out.sites = (out.sites || []).map((site) => {
    const merged = Object.assign({
      id: '', name: '', url: '', home_url: '', enabled: true, mode: 'auto', interval_hours: null,
      url_pattern: '', title_pattern: '', exclude_pattern: '', max_items: 300, note: '',
      method: 'GET', body: '', headers: {},
    }, site);
    merged.json = Object.assign(
      { items_path: '', id_field: '', title_field: '', url_field: '', url_template: '', date_field: '' },
      site.json || {}
    );
    if (!merged.id || taken.has(merged.id)) merged.id = makeSiteId(merged, taken);
    taken.add(merged.id);
    return merged;
  });
  return out;
}

function fillForm() {
  el.intervalInput.value = cfg.check_interval_hours;
  el.firstRunInput.checked = !!cfg.notify_on_first_run;
  el.emailEnabled.checked = !!cfg.email.enabled;
  el.recipientsInput.value = (cfg.recipients || []).join(', ');
  el.smtpHost.value = cfg.email.smtp_host || '';
  el.smtpPort.value = cfg.email.smtp_port || 587;
  el.smtpSecurity.value = cfg.email.security || 'starttls';
  el.smtpUser.value = cfg.email.username || '';
  el.smtpFrom.value = cfg.email.from_addr || '';
  el.subjectPrefix.value = cfg.email.subject_prefix || '';
  document.querySelectorAll('.chip[data-interval]').forEach((chip) => {
    chip.setAttribute('aria-pressed', String(Number(chip.dataset.interval) === Number(cfg.check_interval_hours)));
  });
}

function collectConfig() {
  const out = JSON.parse(JSON.stringify(cfg));
  out.check_interval_hours = Math.max(0.5, Number(el.intervalInput.value) || 24);
  out.notify_on_first_run = el.firstRunInput.checked;
  out.recipients = el.recipientsInput.value.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  out.email.enabled = el.emailEnabled.checked;
  out.email.smtp_host = el.smtpHost.value.trim();
  out.email.smtp_port = Number(el.smtpPort.value) || 587;
  out.email.security = el.smtpSecurity.value;
  out.email.username = el.smtpUser.value.trim();
  out.email.from_addr = el.smtpFrom.value.trim();
  out.email.subject_prefix = el.subjectPrefix.value.trim();
  out.email.password = '';           // 비밀번호는 저장소에 두지 않는다
  const taken = new Set();
  out.sites = (out.sites || []).filter((site) => (site.url || '').trim()).map((site) => {
    const copy = Object.assign({}, site);
    copy.url = copy.url.trim();
    if (!/^https?:\/\//.test(copy.url)) copy.url = 'https://' + copy.url;
    copy.name = (copy.name || copy.url).trim();
    if (!copy.id || taken.has(copy.id)) copy.id = makeSiteId(copy, taken);
    taken.add(copy.id);
    if (!copy.interval_hours) copy.interval_hours = null;
    return copy;
  });
  return out;
}

function siteInfo(siteId) {
  const entry = state && state.sites ? state.sites[siteId] : null;
  return entry || null;
}

function siteStatusHtml(site) {
  const entry = siteInfo(site.id);
  if (!entry) return '<span class="badge badge--wait">아직 확인 전</span>';
  const map = {
    error: ['badge--err', '오류'], new: ['badge--new', '새 공고'], changed: ['badge--new', '변경 감지'],
    baseline: ['badge--wait', '기준 저장'], ok: ['badge--ok', '변화 없음'],
  };
  const meta = map[entry.last_status] || ['badge--wait', '아직 확인 전'];
  const parts = [`<span class="badge ${meta[0]}">${meta[1]}</span>`];
  parts.push(`마지막 확인 ${fmtTime(entry.last_check)}`);
  if (entry.item_count) parts.push(`항목 ${entry.item_count}개`);
  const interval = fmtInterval(site.interval_hours || cfg.check_interval_hours);
  if (interval) parts.push(interval);
  if (entry.last_error) parts.push(`<span style="color:#c4361c">${escapeHtml(entry.last_error)}</span>`);
  else if (entry.last_note) parts.push(escapeHtml(entry.last_note));
  const health = entry.health || {};
  if (health.issue) {
    parts.push(`<span class="badge badge--warn">점검 필요</span>${escapeHtml(health.detail || health.issue)}`);
  }
  return parts.join(' · ');
}

function renderSites() {
  el.siteList.innerHTML = '';
  if (!cfg.sites.length) {
    el.siteList.innerHTML = '<p class="empty">등록된 사이트가 없습니다. ‘＋ 사이트 추가’ 를 눌러 주소를 넣어 주세요.</p>';
    return;
  }
  cfg.sites.forEach((site, index) => {
    const node = el.siteTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.index = String(index);

    const bind = (role) => {
      const input = node.querySelector(`[data-role="${role}"]`);
      if (!input) return;
      const value = getPath(site, role);
      if (input.type === 'checkbox') input.checked = !!value;
      else input.value = value == null ? '' : value;
      input.addEventListener('input', () => {
        const next = input.type === 'checkbox' ? input.checked
          : (input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value);
        setPath(cfg.sites[index], role, next);
        if (role === 'enabled') node.classList.toggle('site--off', !next);
        if (role === 'mode') toggleJsonBox(node, next);
        if (role === 'url' || role === 'home_url') {
          node.querySelector('[data-role="open"]').href =
            cfg.sites[index].home_url || cfg.sites[index].url || '#';
        }
        markDirty();
      });
    };
    ['enabled', 'name', 'url', 'home_url', 'mode', 'interval_hours', 'max_items', 'url_pattern',
      'title_pattern', 'exclude_pattern', 'note', 'json.items_path', 'json.title_field',
      'json.id_field', 'json.url_template', 'json.date_field',
      'method', 'body', 'selector'].forEach(bind);

    node.classList.toggle('site--off', !site.enabled);
    toggleJsonBox(node, site.mode);
    node.querySelector('[data-role="open"]').href = site.home_url || site.url || '#';
    node.querySelector('[data-role="meta"]').innerHTML = siteStatusHtml(site);
    const entry = siteInfo(site.id);
    const stat = node.querySelector('[data-role="stat"]');
    if (entry) {
      const samples = (entry.sample_titles || []).slice(0, 3).map((t) => escapeHtml(t)).join(' · ');
      stat.innerHTML = `기억 중인 공고 ${Object.keys(entry.seen || {}).length}건 · `
        + `추출 방식 ${escapeHtml(entry.last_method || '-')} · id ${escapeHtml(site.id)}`
        + (samples ? `<br />수집 예시: ${samples}` : '');
    } else {
      stat.textContent = `아직 확인 기록이 없습니다 · id ${site.id}`;
    }
    node.querySelector('[data-role="remove"]').addEventListener('click', () => {
      if (!confirm(`'${site.name || site.url}' 을(를) 목록에서 지울까요?`)) return;
      cfg.sites.splice(index, 1);
      markDirty();
      renderSites();
    });
    el.siteList.appendChild(node);
  });
}

function toggleJsonBox(node, mode) {
  ['jsonBox', 'jsonBox2'].forEach((role) => {
    const box = node.querySelector(`[data-role="${role}"]`);
    if (box) box.style.display = mode === 'json' ? '' : 'none';
  });
}

function renderRun() {
  if (!lastRun) {
    el.runBox.innerHTML = '<p class="empty">아직 실행 기록이 없습니다. ‘지금 확인 실행’ 을 눌러 보세요.</p>';
    return;
  }
  const mail = lastRun.email || {};
  const mailText = mail.sent ? '알림 메일 발송함'
    : mail.error ? `메일 발송 실패: ${mail.error}`
      : mail.skipped ? mail.skipped : '보낼 새 공고 없음';
  const rows = (lastRun.results || []).map((r) => {
    const label = { error: '오류', new: '새 공고', changed: '변경 감지', baseline: '기준 저장', ok: '변화 없음' }[r.status] || r.status;
    const items = (r.new_items || []).map((i) =>
      `<li><a href="${escapeHtml(i.url)}" target="_blank" rel="noopener">${escapeHtml(i.title)}</a></li>`).join('');
    return `<div class="feed__item"><b>${escapeHtml(r.site_name)}</b> — ${label} (항목 ${r.item_count}개)
      ${r.error ? `<div class="feed__meta" style="color:#c4361c">${escapeHtml(r.error)}</div>` : ''}
      ${r.note ? `<div class="feed__meta">${escapeHtml(r.note)}</div>` : ''}
      ${items ? `<ol>${items}</ol>` : ''}</div>`;
  }).join('');
  el.runBox.innerHTML = `
    <p class="runbox__line"><b>${fmtTime(lastRun.at)}</b> · 사이트 ${lastRun.checked}곳 확인 ·
      새 공고 ${lastRun.new_total}건 · ${escapeHtml(mailText)}
      ${lastRun.run_url ? ` · <a href="${escapeHtml(lastRun.run_url)}" target="_blank" rel="noopener">실행 기록</a>` : ''}</p>
    ${rows || '<p class="empty">확인한 사이트가 없습니다.</p>'}`;
}

function renderFeed() {
  const rows = [];
  const sites = (state && state.sites) || {};
  (cfg.sites || []).forEach((site) => {
    const entry = sites[site.id];
    if (!entry) return;
    (entry.history || []).forEach((record) => {
      (record.new || []).forEach((item) => rows.push(Object.assign({ site: site.name, at: record.at }, item)));
    });
  });
  rows.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  el.feed.innerHTML = rows.length
    ? rows.slice(0, 30).map((row) => `
      <div class="feed__item">
        <a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">${escapeHtml(row.title)}</a>
        <div class="feed__meta">${escapeHtml(row.site)} · 발견 ${fmtTime(row.at)}${row.date ? ' · ' + escapeHtml(row.date) : ''}</div>
      </div>`).join('')
    : '<p class="empty">아직 새로 발견한 공고가 없습니다.</p>';
}

function renderStatusLine() {
  const enabled = cfg.sites.filter((s) => s.enabled).length;
  const bits = [`${repo.owner}/${repo.repo}@${branch}`,
    `사이트 ${enabled}/${cfg.sites.length}곳`,
    `기본 주기 ${fmtInterval(cfg.check_interval_hours)}`];
  if (lastRun) bits.push(`마지막 확인 ${fmtTime(lastRun.at)}`);
  if (!getToken()) bits.push('읽기 전용 (토큰 없음)');
  el.statusLine.textContent = bits.join(' · ');
}

function renderNotices() {
  const notes = [];
  if (!getToken()) {
    notes.push(`<div class="banner"><b>지금은 보기 전용입니다.</b>
      설정을 저장하거나 확인을 실행하려면 아래 <b>저장 권한 (GitHub 토큰)</b> 에 토큰을 넣어 주세요.</div>`);
  }

  // 저장소 시크릿은 어떤 화면에서도 읽을 수 없으므로, 실제 발송 결과로 판단한다.
  const mailOk = !!(emailStatus && emailStatus.ok);
  if (cfg.email.enabled && mailOk) {
    const kind = emailStatus.kind === 'test' ? '테스트 메일' : '알림 메일';
    notes.push(`<div class="banner"><b>메일 설정 확인됨</b>
      ${fmtTime(emailStatus.at)} 에 ${kind}을(를) ${emailStatus.recipient_count}명에게 정상 발송했습니다.
      수신 주소를 시크릿(<code>JOBMON_RECIPIENTS</code>)으로 넣었다면 아래 ‘수신 이메일’ 칸은 비어 있는 것이 정상입니다.</div>`);
  } else if (cfg.email.enabled && emailStatus && emailStatus.error) {
    notes.push(`<div class="banner banner--err"><b>마지막 메일 발송이 실패했습니다.</b>
      ${escapeHtml(emailStatus.error)}<br />
      시크릿(<code>JOBMON_SMTP_USER</code>, <code>JOBMON_SMTP_PASSWORD</code>)을 확인한 뒤
      Actions 탭에서 <b>테스트 메일만 보내기</b> 로 다시 확인해 보세요.</div>`);
  } else if (cfg.email.enabled) {
    notes.push(`<div class="banner banner--warn"><b>메일이 나가는지 아직 확인되지 않았습니다.</b>
      보내는 계정은 저장소 시크릿(<code>JOBMON_SMTP_HOST</code>, <code>JOBMON_SMTP_USER</code>,
      <code>JOBMON_SMTP_PASSWORD</code>)으로 등록하고, Actions 탭 → 채용공고 확인 → Run workflow 에서
      <b>테스트 메일만 보내기</b> 로 한 번 확인하세요. 확인되면 이 안내는 사라집니다.</div>`);
  } else {
    notes.push(`<div class="banner banner--warn"><b>메일 알림이 꺼져 있습니다.</b>
      아래 ‘새 공고를 이메일로 받기’ 를 켜고 저장하세요.</div>`);
  }
  if (cfg.email.enabled && !cfg.recipients.length && !mailOk) {
    notes.push(`<div class="banner banner--warn"><b>수신 이메일이 비어 있습니다.</b>
      이 화면에 주소를 넣거나, 저장소 시크릿 <code>JOBMON_RECIPIENTS</code> 를 설정하세요.
      (시크릿을 이미 넣었다면 테스트 메일이 성공한 뒤 이 안내가 사라집니다.)</div>`);
  }
  if (!cfg.sites.some((s) => s.enabled)) {
    notes.push('<div class="banner banner--warn"><b>확인할 사이트가 없습니다.</b> 사이트를 추가하고 저장하세요.</div>');
  }
  el.notice.innerHTML = notes.join('');
}

function renderAll() {
  fillForm();
  renderSites();
  renderRun();
  renderFeed();
  renderNotices();
  renderStatusLine();
}

/* ------------------------------------------------------------ 동작 */

async function loadAll(showToast) {
  const configFile = await readJsonFile(PATHS.config);
  configSha = configFile.sha;
  cfg = normalizeConfig(configFile.data);
  if (configFile.missing) {
    toast('저장소에 config.json 이 없어 기본값으로 시작합니다.', true);
  }
  try {
    const stateFile = await readJsonFile(PATHS.state);
    state = stateFile.data || { sites: {} };
  } catch (err) { state = { sites: {} }; }
  try {
    const runFile = await readJsonFile(PATHS.lastRun);
    lastRun = runFile.data;
  } catch (err) { lastRun = null; }
  try {
    const statusFile = await readJsonFile(PATHS.emailStatus);
    emailStatus = statusFile.data;
  } catch (err) { emailStatus = null; }
  markClean();
  renderAll();
  el.sourceLine.textContent = `설정: ${repo.owner}/${repo.repo}/${PATHS.config} (${branch} 브랜치) · `
    + `확인 기록: ${PATHS.state}`;
  if (showToast) toast('최신 내용을 불러왔습니다.');
}

async function save() {
  if (!getToken()) { toast('먼저 GitHub 토큰을 저장하세요.', true); return; }
  const payload = collectConfig();
  const text = JSON.stringify(payload, null, 2) + '\n';
  setBusy(true, '저장 중…');
  try {
    await writeConfigFile(text);
    cfg = normalizeConfig(payload);
    markClean();
    renderAll();
    toast('설정을 저장했습니다. 저장 직후 확인이 한 번 실행됩니다.');
  } catch (err) {
    toast(`저장 실패: ${err.message}`, true);
  } finally {
    setBusy(false);
    renderStatusLine();
  }
}

async function runNow() {
  if (!getToken()) { toast('먼저 GitHub 토큰을 저장하세요.', true); return; }
  if (dirty && !confirm('저장하지 않은 변경이 있습니다. 저장하지 않고 실행할까요?')) return;
  setBusy(true, '확인 실행을 요청하는 중…');
  try {
    await dispatchWorkflow();
    openModal('확인을 시작했습니다', `
      <p>GitHub Actions 에서 확인이 시작되었습니다. 사이트 수에 따라 보통 1~2분 걸립니다.</p>
      <p><a href="https://github.com/${repo.owner}/${repo.repo}/actions/workflows/${WORKFLOW_FILE}"
        target="_blank" rel="noopener">실행 기록 보기</a> 에서 진행 상황을 볼 수 있고,
        끝나면 이 화면의 <b>새로고침</b> 을 눌러 결과를 확인하세요.</p>`);
    setTimeout(() => { loadAll(false).catch(() => {}); }, 60000);
  } catch (err) {
    const hint = err.status === 403 || err.status === 404
      ? ' (토큰에 Actions: Read and write 권한이 있는지, 워크플로가 기본 브랜치에 있는지 확인하세요)'
      : '';
    toast(`실행 요청 실패: ${err.message}${hint}`, true);
  } finally {
    setBusy(false);
    renderStatusLine();
  }
}

function saveToken() {
  const token = el.tokenInput.value.trim();
  if (!token) { toast('토큰을 입력하세요.', true); return; }
  localStorage.setItem(LS_TOKEN, token);
  el.tokenInput.value = '';
  updateTokenState();
  loadAll(true).catch((err) => toast(err.message, true));
}

function clearToken() {
  localStorage.removeItem(LS_TOKEN);
  updateTokenState();
  renderNotices();
  renderStatusLine();
  toast('토큰을 지웠습니다.');
}

function updateTokenState() {
  const token = getToken();
  el.tokenState.textContent = token
    ? `저장됨 (…${token.slice(-4)}) — 이 브라우저에만 보관됩니다`
    : '저장된 토큰이 없습니다 — 보기 전용';
}

function copyJson() {
  const text = JSON.stringify(collectConfig(), null, 2) + '\n';
  navigator.clipboard.writeText(text).then(
    () => toast('설정 JSON 을 복사했습니다. GitHub 에서 config.json 에 붙여 넣으세요.'),
    () => openModal('설정 JSON', `<pre class="log">${escapeHtml(text)}</pre>`)
  );
}

function wireLinks() {
  const base = `https://github.com/${repo.owner}/${repo.repo}`;
  el.editOnGithub.href = `${base}/edit/${branch}/${PATHS.config}`;
  el.actionsLink.href = `${base}/actions/workflows/${WORKFLOW_FILE}`;
  el.secretsLink.href = `${base}/settings/secrets/actions`;
}

async function boot() {
  repo = detectRepo();
  if (!repo) {
    const input = prompt('저장소를 알 수 없습니다. owner/repo 형식으로 입력하세요.', 'chaejwan/HDHR_CJW');
    if (!input || !input.includes('/')) {
      el.statusLine.textContent = '저장소를 지정해야 설정을 불러올 수 있습니다.';
      return;
    }
    const [owner, name] = input.split('/');
    repo = { owner, repo: name };
    localStorage.setItem(LS_REPO, JSON.stringify(repo));
  }
  updateTokenState();
  try {
    const info = await gh(`/repos/${repo.owner}/${repo.repo}`);
    branch = info.default_branch || 'main';
  } catch (err) {
    branch = 'main';
    toast(`저장소 정보를 읽지 못해 기본 브랜치를 main 으로 가정합니다: ${err.message}`, true);
  }
  wireLinks();
  try {
    await loadAll(false);
  } catch (err) {
    el.statusLine.textContent = '설정을 불러오지 못했습니다: ' + err.message;
    el.notice.innerHTML = `<div class="banner banner--err"><b>설정을 불러오지 못했습니다.</b>
      ${escapeHtml(err.message)}<br />저장소가 공개 상태인지, 브랜치(${escapeHtml(branch)})에
      <code>${PATHS.config}</code> 이 있는지 확인하세요.</div>`;
  }
}

/* ------------------------------------------------------------ 이벤트 */

[el.intervalInput, el.firstRunInput, el.emailEnabled, el.recipientsInput, el.smtpHost,
  el.smtpPort, el.smtpSecurity, el.smtpUser, el.smtpFrom, el.subjectPrefix,
].forEach((input) => input.addEventListener('input', () => { markDirty(); }));

document.querySelectorAll('.chip[data-interval]').forEach((chip) => {
  chip.addEventListener('click', () => {
    el.intervalInput.value = chip.dataset.interval;
    document.querySelectorAll('.chip[data-interval]').forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
    markDirty();
  });
});

el.addSiteBtn.addEventListener('click', () => {
  cfg.sites.push({
    id: '', name: '', url: '', home_url: '', enabled: true, mode: 'auto', interval_hours: null,
    url_pattern: '', title_pattern: '', exclude_pattern: '', max_items: 300,
    method: 'GET', body: '', headers: {}, selector: '',
    json: { items_path: '', id_field: '', title_field: '', url_field: '', url_template: '', date_field: '' },
    note: '',
  });
  markDirty();
  renderSites();
  const names = el.siteList.querySelectorAll('.site__name');
  if (names.length) names[names.length - 1].focus();
});

el.saveBtn.addEventListener('click', save);
el.runBtn.addEventListener('click', runNow);
el.refreshBtn.addEventListener('click', () => {
  if (dirty && !confirm('저장하지 않은 변경이 사라집니다. 계속할까요?')) return;
  loadAll(true).catch((err) => toast(err.message, true));
});
el.tokenSaveBtn.addEventListener('click', saveToken);
el.tokenClearBtn.addEventListener('click', clearToken);
el.copyJsonBtn.addEventListener('click', copyJson);
document.getElementById('modalClose').addEventListener('click', () => { el.modal.hidden = true; });
el.modal.addEventListener('click', (event) => { if (event.target === el.modal) el.modal.hidden = true; });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') el.modal.hidden = true; });
window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });

setInterval(() => { if (!busy && !dirty) loadAll(false).catch(() => {}); }, 120000);
boot();
