'use strict';

/* 채용공고 모니터 설정 화면 */

const $ = (sel) => document.querySelector(sel);
const el = {
  status: $('#statusLine'),
  saveBtn: $('#saveBtn'),
  checkAllBtn: $('#checkAllBtn'),
  addSiteBtn: $('#addSiteBtn'),
  refreshBtn: $('#refreshBtn'),
  siteList: $('#siteList'),
  interval: $('#intervalInput'),
  firstRun: $('#firstRunInput'),
  emailEnabled: $('#emailEnabled'),
  recipients: $('#recipientsInput'),
  smtpHost: $('#smtpHost'),
  smtpPort: $('#smtpPort'),
  smtpSecurity: $('#smtpSecurity'),
  smtpUser: $('#smtpUser'),
  smtpPass: $('#smtpPass'),
  smtpFrom: $('#smtpFrom'),
  subjectPrefix: $('#subjectPrefix'),
  passwordEnv: $('#passwordEnv'),
  pwState: $('#pwState'),
  envHint: $('#envHint'),
  feed: $('#feed'),
  logBox: $('#logBox'),
  configPath: $('#configPath'),
  toast: $('#toast'),
  modal: $('#modal'),
  modalTitle: $('#modalTitle'),
  modalBody: $('#modalBody'),
  siteTemplate: $('#siteTemplate'),
};

let cfg = null;          // 화면에서 편집 중인 설정
let view = null;         // 서버가 알려 준 실행 상태
let dirty = false;
let busy = false;

/* ------------------------------------------------------------ 유틸 */

function toast(message, isError) {
  el.toast.textContent = message;
  el.toast.classList.toggle('toast--err', !!isError);
  el.toast.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.toast.hidden = true; }, isError ? 6000 : 3200);
}

function openModal(title, html) {
  el.modalTitle.textContent = title;
  el.modalBody.innerHTML = html;
  el.modal.hidden = false;
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function markDirty() {
  dirty = true;
  el.saveBtn.disabled = false;
  el.saveBtn.textContent = '설정 저장 *';
}

function markClean() {
  dirty = false;
  el.saveBtn.disabled = true;
  el.saveBtn.textContent = '설정 저장';
}

function setBusy(on, label) {
  busy = on;
  [el.saveBtn, el.checkAllBtn, el.addSiteBtn, el.refreshBtn].forEach((b) => { b.disabled = on || (b === el.saveBtn && !dirty); });
  if (on && label) el.status.textContent = label;
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options));
  let data;
  try {
    data = await res.json();
  } catch (err) {
    throw new Error('서버 응답을 읽지 못했습니다. (HTTP ' + res.status + ')');
  }
  if (!res.ok && data && data.error) throw new Error(data.error);
  return data;
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

/* ------------------------------------------------------------ 값 읽기/쓰기 */

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

function fillForm() {
  el.interval.value = cfg.check_interval_hours;
  el.firstRun.checked = !!cfg.notify_on_first_run;
  el.emailEnabled.checked = !!cfg.email.enabled;
  el.recipients.value = (cfg.recipients || []).join(', ');
  el.smtpHost.value = cfg.email.smtp_host || '';
  el.smtpPort.value = cfg.email.smtp_port || 587;
  el.smtpSecurity.value = cfg.email.security || 'starttls';
  el.smtpUser.value = cfg.email.username || '';
  el.smtpFrom.value = cfg.email.from_addr || '';
  el.subjectPrefix.value = cfg.email.subject_prefix || '';
  el.passwordEnv.value = cfg.email.password_env || '';
  el.envHint.textContent = cfg.email.password_env || 'JOBMON_SMTP_PASSWORD';
  el.smtpPass.value = '';
  if (cfg.email.password_saved) el.pwState.textContent = '(저장돼 있음 — 바꿀 때만 입력)';
  else if (cfg.email.password_env_set) el.pwState.textContent = '(환경변수에서 읽는 중)';
  else el.pwState.textContent = '(설정되지 않음)';
  document.querySelectorAll('.chip[data-interval]').forEach((chip) => {
    chip.setAttribute('aria-pressed', String(Number(chip.dataset.interval) === Number(cfg.check_interval_hours)));
  });
}

function collectConfig() {
  const out = JSON.parse(JSON.stringify(cfg));
  out.check_interval_hours = Number(el.interval.value) || 24;
  out.notify_on_first_run = el.firstRun.checked;
  out.recipients = el.recipients.value.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  out.email.enabled = el.emailEnabled.checked;
  out.email.smtp_host = el.smtpHost.value.trim();
  out.email.smtp_port = Number(el.smtpPort.value) || 587;
  out.email.security = el.smtpSecurity.value;
  out.email.username = el.smtpUser.value.trim();
  out.email.from_addr = el.smtpFrom.value.trim();
  out.email.subject_prefix = el.subjectPrefix.value.trim();
  out.email.password_env = el.passwordEnv.value.trim();
  out.email.password = el.smtpPass.value ? el.smtpPass.value : (cfg.email.password || '');
  delete out.email.password_saved;
  delete out.email.password_env_set;
  return out;
}

/* ------------------------------------------------------------ 사이트 목록 */

function siteStatusHtml(info) {
  if (!info) return '<span class="badge badge--wait">아직 확인 전</span>';
  const map = {
    error: ['badge--err', '오류'],
    new: ['badge--new', '새 공고'],
    changed: ['badge--new', '변경 감지'],
    baseline: ['badge--wait', '기준 저장'],
    ok: ['badge--ok', '변화 없음'],
  };
  const meta = map[info.last_status] || ['badge--wait', '아직 확인 전'];
  const parts = [`<span class="badge ${meta[0]}">${meta[1]}</span>`];
  parts.push(`마지막 확인 ${fmtTime(info.last_check)}`);
  parts.push(info.next_due ? `다음 확인 ${fmtTime(info.next_due)}` : '다음 확인 예정: 곧');
  if (info.item_count) parts.push(`항목 ${info.item_count}개`);
  if (info.last_error) parts.push(`<span style="color:#c4361c">${escapeHtml(info.last_error)}</span>`);
  else if (info.last_note) parts.push(escapeHtml(info.last_note));
  return parts.join(' · ');
}

function infoFor(siteId) {
  if (!view) return null;
  return (view.sites || []).find((s) => s.id === siteId) || null;
}

function renderSites() {
  el.siteList.innerHTML = '';
  if (!cfg.sites.length) {
    el.siteList.innerHTML = '<p class="empty">등록된 사이트가 없습니다. ‘＋ 사이트 추가’ 를 눌러 주소를 넣어 주세요.</p>';
    return;
  }
  cfg.sites.forEach((site, index) => {
    const node = el.siteTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.siteId = site.id || `new-${index}`;
    node.dataset.index = String(index);

    const bind = (role, value) => {
      const input = node.querySelector(`[data-role="${role}"]`);
      if (!input) return;
      if (input.type === 'checkbox') input.checked = !!value;
      else input.value = value == null ? '' : value;
      input.addEventListener('input', () => {
        const next = input.type === 'checkbox' ? input.checked
          : (input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value);
        setPath(cfg.sites[index], role, next);
        if (role === 'enabled') node.classList.toggle('site--off', !next);
        if (role === 'mode') toggleJsonBox(node, next);
        markDirty();
      });
    };

    ['enabled', 'name', 'url', 'home_url', 'mode', 'interval_hours', 'max_items',
      'url_pattern', 'title_pattern', 'exclude_pattern', 'note',
      'json.items_path', 'json.title_field', 'json.id_field', 'json.url_template',
      'json.date_field', 'method', 'body', 'selector',
    ].forEach((role) => bind(role, getPath(site, role)));

    node.classList.toggle('site--off', !site.enabled);
    toggleJsonBox(node, site.mode);
    updateSiteMeta(node, site);

    node.querySelector('[data-role="check"]').addEventListener('click', () => checkSite(site, index));
    node.querySelector('[data-role="preview"]').addEventListener('click', () => previewSite(index));
    node.querySelector('[data-role="remove"]').addEventListener('click', () => {
      if (!confirm(`'${site.name || site.url}' 을(를) 목록에서 지울까요?`)) return;
      cfg.sites.splice(index, 1);
      markDirty();
      renderSites();
    });
    node.querySelector('[data-role="reset"]').addEventListener('click', () => resetSite(site));

    el.siteList.appendChild(node);
  });
}

function toggleJsonBox(node, mode) {
  ['jsonBox', 'jsonBox2'].forEach((role) => {
    const box = node.querySelector(`[data-role="${role}"]`);
    if (box) box.style.display = mode === 'json' ? '' : 'none';
  });
}

function updateSiteMeta(node, site) {
  const info = infoFor(site.id);
  const meta = node.querySelector('[data-role="meta"]');
  if (meta) {
    const interval = fmtInterval(site.interval_hours || (cfg && cfg.check_interval_hours));
    meta.innerHTML = `${siteStatusHtml(info)}${interval ? ' · ' + interval : ''}`;
  }
  const stat = node.querySelector('[data-role="stat"]');
  if (stat && info) {
    stat.textContent = `기억 중인 공고 ${info.seen_count}건 · 추출 방식 ${info.last_method || '-'}`;
  }
}

function refreshSiteMeta() {
  el.siteList.querySelectorAll('.site').forEach((node) => {
    const site = cfg.sites[Number(node.dataset.index)];
    if (site) updateSiteMeta(node, site);
  });
}

/* ------------------------------------------------------------ 동작 */

async function saveConfig(silent) {
  const payload = collectConfig();
  const data = await api('/api/config', { method: 'PUT', body: JSON.stringify({ config: payload }) });
  if (!data.ok) throw new Error(data.error || '저장에 실패했습니다.');
  cfg = data.config;
  markClean();
  fillForm();
  renderSites();
  await loadState();
  if (!silent) {
    const warnings = data.warnings || [];
    toast(warnings.length ? `저장했습니다. 확인 필요: ${warnings[0]}` : '설정을 저장했습니다.', warnings.length > 0);
  }
  return cfg;
}

async function ensureSaved() {
  if (dirty) await saveConfig(true);
}

function renderCheckSummary(summary) {
  const rows = (summary.results || []).map((r) => {
    const label = { error: '오류', new: '새 공고', changed: '변경 감지', baseline: '기준 저장', ok: '변화 없음' }[r.status] || r.status;
    const items = (r.new_items || []).map(
      (i) => `<li><a href="${escapeHtml(i.url)}" target="_blank" rel="noopener">${escapeHtml(i.title)}</a>${i.date ? ' <span class="feed__meta">' + escapeHtml(i.date) + '</span>' : ''}</li>`
    ).join('');
    return `<div class="feed__item"><b>${escapeHtml(r.site_name)}</b> — ${label}
      ${r.error ? '<div class="feed__meta" style="color:#c4361c">' + escapeHtml(r.error) + '</div>' : ''}
      ${r.note ? '<div class="feed__meta">' + escapeHtml(r.note) + '</div>' : ''}
      ${items ? '<ol>' + items + '</ol>' : '<div class="feed__meta">새 공고 없음 (항목 ' + r.item_count + '개)</div>'}
    </div>`;
  }).join('');
  const mail = summary.email || {};
  const mailLine = mail.sent ? '<p class="hint">알림 메일을 보냈습니다.</p>'
    : mail.error ? `<p class="hint" style="color:#c4361c">메일 발송 실패: ${escapeHtml(mail.error)}</p>`
      : mail.skipped ? `<p class="hint">${escapeHtml(mail.skipped)}</p>` : '';
  openModal(`확인 결과 — 새 공고 ${summary.new_total}건`, rows + mailLine);
}

async function runCheck(siteIds) {
  if (busy) return;
  setBusy(true, '확인 중…');
  try {
    await ensureSaved();
    const data = await api('/api/check', { method: 'POST', body: JSON.stringify({ site_ids: siteIds || null }) });
    if (!data.ok) throw new Error(data.error);
    renderCheckSummary(data.summary);
    await loadState();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBusy(false);
    updateStatusLine();
  }
}

async function checkSite(site) {
  if (!site.url) { toast('주소를 먼저 입력하세요.', true); return; }
  await ensureSaved();
  const fresh = cfg.sites.find((s) => s.url === site.url) || site;
  await runCheck([fresh.id]);
}

async function previewSite(index) {
  if (busy) return;
  const site = cfg.sites[index];
  if (!site.url) { toast('주소를 먼저 입력하세요.', true); return; }
  setBusy(true, '미리보기를 확인하는 중…');
  try {
    const data = await api('/api/preview', { method: 'POST', body: JSON.stringify({ site }) });
    const p = (data && data.preview) || {};
    if (!p.ok) throw new Error(p.error || '미리보기에 실패했습니다.');
    const methodLabel = {
      json: 'JSON 데이터', jsonld: '구조화 데이터(JobPosting)', 'embedded-json': '페이지 내장 JSON',
      links: '링크(HTML)', fingerprint: '본문 변경 감시', none: '찾지 못함',
    }[p.method] || p.method;
    const items = (p.items || []).map(
      (i) => `<li><a href="${escapeHtml(i.url)}" target="_blank" rel="noopener">${escapeHtml(i.title)}</a>${i.date ? ' <span class="feed__meta">' + escapeHtml(i.date) + '</span>' : ''}</li>`
    ).join('');
    openModal(
      `미리보기 — ${site.name || site.url}`,
      `<p class="hint">추출 방식: <b>${escapeHtml(methodLabel)}</b> · 찾은 항목 ${p.count}개`
      + `${p.rendered ? ' · 브라우저 렌더링 사용' : ''}</p>`
      + (p.note ? `<p class="hint">${escapeHtml(p.note)}</p>` : '')
      + (items ? `<ol>${items}</ol>` : '<p class="empty">가져온 항목이 없습니다. 방식을 ‘브라우저 렌더링’ 으로 바꾸거나 필터를 지워 보세요.</p>')
      + '<p class="hint">여기 보이는 목록이 곧 감시 대상입니다. 메뉴·푸터 링크가 섞여 있으면 주소/제목 필터로 걸러 주세요.</p>'
    );
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBusy(false);
    updateStatusLine();
  }
}

async function resetSite(site) {
  if (!confirm('이 사이트의 기록을 지우면, 다음 확인 때 현재 목록을 새 기준으로 저장합니다. 진행할까요?')) return;
  try {
    await api('/api/reset', { method: 'POST', body: JSON.stringify({ site_id: site.id }) });
    toast('기록을 초기화했습니다.');
    await loadState();
  } catch (err) {
    toast(err.message, true);
  }
}

async function sendTestMail() {
  try {
    await ensureSaved();
    const data = await api('/api/test-email', { method: 'POST', body: JSON.stringify({}) });
    toast(data.ok ? data.message : data.error, !data.ok);
  } catch (err) {
    toast(err.message, true);
  }
}

function updateStatusLine() {
  if (!view) return;
  const enabled = cfg.sites.filter((s) => s.enabled).length;
  const errors = (view.sites || []).filter((s) => s.last_status === 'error').length;
  const next = (view.sites || [])
    .filter((s) => s.enabled && s.next_due)
    .map((s) => s.next_due)
    .sort()[0];
  const bits = [`사이트 ${enabled}/${cfg.sites.length}곳 감시 중`, `기본 주기 ${fmtInterval(cfg.check_interval_hours)}`];
  if (next) bits.push(`다음 확인 ${fmtTime(next)}`);
  if (errors) bits.push(`오류 ${errors}건`);
  if (!cfg.email.enabled) bits.push('메일 알림 꺼짐');
  else if (!cfg.recipients.length) bits.push('수신 이메일 없음');
  el.status.textContent = bits.join(' · ');
}

function renderFeed() {
  const rows = [];
  (view.sites || []).forEach((site) => {
    (site.recent_new || []).forEach((item) => rows.push(Object.assign({ site: site.name }, item)));
  });
  rows.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  if (!rows.length) {
    el.feed.innerHTML = '<p class="empty">아직 새로 발견한 공고가 없습니다.</p>';
    return;
  }
  el.feed.innerHTML = rows.slice(0, 30).map((row) => `
    <div class="feed__item">
      <a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">${escapeHtml(row.title)}</a>
      <div class="feed__meta">${escapeHtml(row.site)} · 발견 ${fmtTime(row.at)}${row.date ? ' · ' + escapeHtml(row.date) : ''}</div>
    </div>`).join('');
}

async function loadState() {
  const data = await api('/api/state');
  if (!data.ok) return;
  view = data.state;
  refreshSiteMeta();
  renderFeed();
  updateStatusLine();
}

async function loadLog() {
  try {
    const data = await api('/api/log');
    if (data.ok) {
      el.logBox.textContent = (data.lines || []).join('\n');
      el.logBox.scrollTop = el.logBox.scrollHeight;
    }
  } catch (err) { /* 로그는 실패해도 무시 */ }
}

async function boot() {
  try {
    const data = await api('/api/config');
    cfg = data.config;
    el.configPath.textContent = `설정 파일: ${data.config_path}`
      + (data.browser_available ? '' : ' · 브라우저 렌더링(Playwright) 미설치');
    fillForm();
    renderSites();
    markClean();
    await loadState();
    await loadLog();
  } catch (err) {
    el.status.textContent = '설정을 불러오지 못했습니다: ' + err.message;
  }
}

/* ------------------------------------------------------------ 이벤트 */

[el.interval, el.firstRun, el.emailEnabled, el.recipients, el.smtpHost, el.smtpPort,
  el.smtpSecurity, el.smtpUser, el.smtpPass, el.smtpFrom, el.subjectPrefix, el.passwordEnv,
].forEach((input) => input.addEventListener('input', markDirty));

document.querySelectorAll('.chip[data-interval]').forEach((chip) => {
  chip.addEventListener('click', () => {
    el.interval.value = chip.dataset.interval;
    document.querySelectorAll('.chip[data-interval]').forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
    markDirty();
  });
});

el.saveBtn.addEventListener('click', async () => {
  setBusy(true, '저장 중…');
  try { await saveConfig(); } catch (err) { toast(err.message, true); }
  setBusy(false);
  updateStatusLine();
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
  const inputs = el.siteList.querySelectorAll('.site .site__name');
  if (inputs.length) inputs[inputs.length - 1].focus();
});

el.checkAllBtn.addEventListener('click', () => runCheck(null));
el.refreshBtn.addEventListener('click', async () => {
  try { await loadState(); await loadLog(); toast('최신 상태를 불러왔습니다.'); }
  catch (err) { toast(err.message, true); }
});
$('#testMailBtn').addEventListener('click', sendTestMail);
$('#clearPassBtn').addEventListener('click', () => {
  if (!confirm('저장된 SMTP 비밀번호를 지울까요?')) return;
  cfg.email.password = '__CLEAR__';
  el.smtpPass.value = '';
  el.pwState.textContent = '(저장 시 삭제됩니다)';
  markDirty();
});
$('#modalClose').addEventListener('click', () => { el.modal.hidden = true; });
el.modal.addEventListener('click', (event) => { if (event.target === el.modal) el.modal.hidden = true; });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') el.modal.hidden = true; });
window.addEventListener('beforeunload', (event) => {
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});

setInterval(() => { if (!busy) { loadState().catch(() => {}); loadLog(); } }, 15000);
boot();
