/* 电之脉动 2.0 — 控制台逻辑
   纯原生 JS，不引框架：这个页面只需要「拉状态 → 渲染 → 提交动作」三步。 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  env: {}, devices: [], protocols: [], smart: {}, capture: {}, captures: [],
  view: 'overview',
  selectedExchange: null,
  analyze: null,
  pickedOffset: null,
  pollTimer: null,
  smartTimer: null,
  capTimer: null,
  hubs: new Set(),
  seconds: 60,
  step: 1,
};

/* ---------------------------------------------------------------- 工具 */
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opt);
  const text = await res.text();
  try { return JSON.parse(text); } catch { return { ok: false, error: text || res.statusText }; }
}

function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = `<div class="bar"></div><div>${esc(msg)}</div>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; el.style.transition = '.25s'; }, 3200);
  setTimeout(() => el.remove(), 3600);
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function pctColor(p) {
  if (p === null || p === undefined) return '#5d6c7e';
  if (p <= 20) return '#f2555a';
  if (p <= 45) return '#f2b544';
  return '#2ee6a8';
}

/* ---------------------------------------------------------------- 导航 */
const VIEW_META = {
  overview: ['概览', '实时电量、连接状态与数据来源'],
  protocols: ['协议库', '按需启用厂商协议；启用的会立刻生效'],
  smart: ['智能测试', '自动识别可用接口，验证协议是否仍然有效'],
  capture: ['抓包破解', '跟着提示抓一次报文，就能造一条新协议'],
  settings: ['设置', '刷新、提醒与程序行为'],
};

function switchView(name) {
  S.view = name;
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  const [t, d] = VIEW_META[name];
  $('#viewTitle').textContent = t;
  $('#viewDesc').textContent = d;
  history.replaceState(null, '', '#' + name);
  if (name === 'protocols') renderProtocols();
  if (name === 'settings') renderSettings();
  if (name === 'capture') renderCaptureStep();
}

/* ---------------------------------------------------------------- 状态 */
async function loadState() {
  const st = await api('/api/state');
  if (st.error) { toast(st.error, 'err'); return; }
  S.env = st.env || {};
  S.devices = st.devices || [];
  S.protocols = st.protocols || [];
  S.smart = st.smarttest || S.smart;
  S.capture = st.capture || {};
  S.captures = st.captures || [];
  S.settings = st.settings || S.settings || {};
  renderEnv();
  renderOverview();
  renderProtocols();
  renderSmart();
  if (S.view === 'capture') renderCaptureStep();
}

/* ---------------------------------------------------------------- 环境 */
function renderEnv() {
  const env = S.env;
  const admin = $('#envAdmin');
  admin.className = 'env-line ' + (env.admin ? 'ok' : 'warn');
  admin.textContent = env.admin ? '管理员权限 · 可抓包' : '普通权限 · 抓包需提权';

  const usb = $('#envUsbpcap');
  usb.className = 'env-line ' + (env.usbpcap ? 'ok' : 'warn');
  usb.textContent = env.usbpcap ? 'USBPcap 就绪' : '未检测到 USBPcap';

  $('#envUptime').textContent = env.uptime ? `运行 ${Math.floor(env.uptime / 60)} 分钟` : '';
  $('#appVer').textContent = env.app ? env.app.version : '2.0';
  $('#pillDevices').textContent = `${S.devices.filter(d => d.connected).length} / ${S.devices.length} 台在线`;
}

/* ---------------------------------------------------------------- 概览 */
function renderOverview() {
  const grid = $('#deviceGrid');
  const online = S.devices.filter(d => d.connected);
  $('#statOnline').textContent = online.length;
  $('#statOnlineSub').textContent = `共 ${S.devices.length} 台`;
  $('#pillDevices').textContent = `${online.length} / ${S.devices.length} 台在线`;

  const withPct = online.filter(d => d.percent !== null);
  if (withPct.length) {
    const low = withPct.reduce((a, b) => (a.percent <= b.percent ? a : b));
    $('#statLowest').textContent = low.percent + '%';
    $('#statLowest').style.color = pctColor(low.percent);
    $('#statLowestName').textContent = low.name;
  } else {
    $('#statLowest').textContent = '—';
    $('#statLowest').style.color = '';
    $('#statLowestName').textContent = '暂无读数';
  }

  const active = S.protocols.filter(p => p.enabled).length;
  $('#statProto').textContent = active;
  $('#statProtoSub').textContent = `协议库共 ${S.protocols.length} 条`;
  $('#protoCount').textContent = S.protocols.length;

  $('#deviceEmpty').hidden = S.devices.length > 0;
  grid.innerHTML = S.devices.map(d => {
    const C = 2 * Math.PI * 26;
    const pct = d.percent;
    const offset = pct === null ? C : C * (1 - Math.max(0, Math.min(100, pct)) / 100);
    const ring = pct === null
      ? `<div class="ring empty"><svg width="62" height="62"><circle class="track" cx="31" cy="31" r="26" fill="none" stroke-width="4"/></svg><span>?</span></div>`
      : `<div class="ring">
           <svg width="62" height="62">
             <circle class="track" cx="31" cy="31" r="26" fill="none" stroke-width="4"/>
             <circle class="bar" cx="31" cy="31" r="26" fill="none" stroke-width="4"
               stroke="${pctColor(pct)}" stroke-dasharray="${C.toFixed(1)}"
               stroke-dashoffset="${offset.toFixed(1)}"/>
           </svg>
           <span>${pct}<small>%</small></span>
         </div>`;
    const statusChip = d.charging
      ? `<span class="chip accent">充电中</span>`
      : d.connected ? '' : `<span class="chip">未连接</span>`;
    return `<div class="device-card ${d.connected ? '' : 'offline'}">
      ${ring}
      <div class="device-main">
        <div class="device-name"><span class="dot ${d.connected ? '' : 'off'}"></span>${esc(d.name)}</div>
        <div class="device-meta">
          <span class="chip">${esc(d.kind_label)}</span>
          <span class="chip blue">${esc(d.transport_label)}</span>
          ${statusChip}
        </div>
        <div class="device-source" title="${esc(d.source)}">${esc(d.source || '—')}</div>
      </div>
    </div>`;
  }).join('');
}

/* ---------------------------------------------------------------- 协议库 */
function renderProtocols() {
  const q = ($('#protoSearch').value || '').trim().toLowerCase();
  const list = S.protocols.filter(p => !q ||
    (p.name + p.vendor + p.model + p.match_text).toLowerCase().includes(q));

  $('#protoCount').textContent = S.protocols.length;
  $('#protoEmpty').hidden = list.length > 0;
  $('#protoGrid').innerHTML = list.map(p => {
    const srcLabel = { builtin: '内置', user: '自建', imported: '导入', cracked: '破解' }[p.source] || p.source;
    return `<div class="proto-card ${p.enabled ? '' : 'off'}">
      <div class="proto-top">
        <div class="proto-title">
          <h4>${esc(p.name)}<span class="badge ${p.source}">${srcLabel}</span></h4>
          <p>${esc(p.vendor || '未知厂商')}${p.model ? ' · ' + esc(p.model) : ''}</p>
        </div>
        <label class="switch" title="${p.enabled ? '已启用' : '已停用'}">
          <input type="checkbox" ${p.enabled ? 'checked' : ''} onchange="toggleProto('${p.id}', this.checked)"><i></i>
        </label>
      </div>
      <dl class="kv">
        <dt>VID:PID</dt><dd>${esc(p.match_text)}</dd>
        <dt>通道</dt><dd>${esc(p.channel_text)}</dd>
        <dt>电量字节</dt><dd>byte[${p.response_offset}]</dd>
        <dt>校验</dt><dd>${esc(p.checksum_label)}</dd>
      </dl>
      <div class="proto-actions">
        ${p.active ? '<span class="chip accent">在线</span>' : '<span class="chip">未检测到</span>'}
        <div class="spacer"></div>
        <button class="icon-btn" title="复制一份" onclick="duplicateProto('${p.id}')">
          <svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5h10"/></svg>
        </button>
        <button class="icon-btn" title="导出 JSON" onclick="exportProto('${p.id}')">
          <svg viewBox="0 0 24 24"><path d="M12 4v12M8 12l4 4 4-4"/><path d="M4 20h16"/></svg>
        </button>
        <button class="icon-btn danger" title="删除" onclick="deleteProto('${p.id}','${esc(p.name)}')">
          <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>
        </button>
      </div>
    </div>`;
  }).join('');
}

async function toggleProto(id, enabled) {
  const res = await api('/api/protocols/toggle', { id, enabled });
  if (!res.ok) return toast('操作失败', 'err');
  toast(enabled ? '已启用，正在重新加载通道' : '已停用', 'ok');
  setTimeout(loadState, 700);
}

async function deleteProto(id, name) {
  if (!confirm(`删除协议「${name}」？\n\n这会同时从生效列表里移除。抓包文件和其它协议不受影响。`)) return;
  const res = await api('/api/protocols/delete', { id });
  toast(res.ok ? '已删除' : '删除失败', res.ok ? 'ok' : 'err');
  loadState();
}

async function duplicateProto(id) {
  const p = S.protocols.find(x => x.id === id);
  if (!p) return;
  const copy = JSON.parse(JSON.stringify(p));
  copy.id = '';
  copy.name = p.name + ' 副本';
  copy.enabled = false;
  const res = await api('/api/protocols/save', { protocol: copy });
  toast(res.ok ? '已复制（默认停用）' : '复制失败', res.ok ? 'ok' : 'err');
  loadState();
}

async function exportProto(id) {
  const res = await api('/api/protocols/export?id=' + encodeURIComponent(id));
  if (!res.ok) return toast('导出失败', 'err');
  const blob = new Blob([JSON.stringify(res.protocol, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${res.protocol.name || 'protocol'}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast('已导出到下载目录', 'ok');
}

/* ---------------------------------------------------------------- 智能测试 */
function renderSmart() {
  const st = S.smart || {};
  const steps = st.steps || [];
  $('#smartSteps').innerHTML = steps.map((s, i) => `<div class="step-row ${s.status}">
      <div class="step-dot">${s.status === 'ok' ? '✓' : s.status === 'fail' ? '×' : s.status === 'warn' ? '!' : i + 1}</div>
      <div class="step-body">
        <h5>${esc(s.title)}</h5>
        <p>${esc(s.detail || s.desc || '')}</p>
      </div>
    </div>`).join('');

  const btn = $('#btnSmartStart');
  btn.disabled = !!st.running;
  btn.innerHTML = st.running
    ? `<span class="spinner"></span> 正在测试… ${st.elapsed || 0}s`
    : (st.finished ? '再跑一次' : '开始智能测试');

  // 已验证
  const verified = st.verified || [];
  $('#smartVerified').hidden = !verified.length;
  $('#verifyList').innerHTML = verified.map(v => {
    const score = v.ble_ref !== undefined && v.score_boost ? '与蓝牙一致' : '';
    return `<div class="verify-card ${v.ok ? '' : 'bad'}">
      <div class="verify-val">${v.ok ? v.value + '%' : '无读数'}</div>
      <div class="verify-info">
        <h5>${esc(v.name)} <span class="chip">${esc(v.usage_page)}/${esc(v.usage)}</span></h5>
        <p>${esc(v.note || '')}${score ? ' · ' + score : ''}</p>
      </div>
      ${v.ok ? '' : `<button class="btn ghost small" onclick="goCrack('${esc(v.name)}','${esc(v.vid)}','${esc(v.pid)}')">去破解</button>`}
    </div>`;
  }).join('');

  // 待破解接口
  const un = st.uncovered || [];
  $('#smartUncovered').hidden = !un.length;
  if (un.length) {
    $('#uncoveredTable').innerHTML = `<thead><tr>
        <th>VID:PID</th><th>通道</th><th>长度</th><th>产品名</th><th>双向</th></tr></thead><tbody>
      ${un.map(u => `<tr>
        <td><code>${esc(u.vid)}:${esc(u.pid)}</code></td>
        <td><code>${esc(u.usage_page)}/${esc(u.usage)}</code></td>
        <td>${u.in_len} / ${u.out_len}</td>
        <td>${esc(u.product || '—')}${u.likely === false ? ' <span class="chip">疑似非外设</span>' : ''}</td>
        <td>${u.bidirectional ? '<span class="chip accent">是</span>' : '<span class="chip">只读</span>'}</td>
      </tr>`).join('')}</tbody>`;
  }

  // 被动嗅探
  const pa = st.passive || [];
  $('#smartPassive').hidden = !pa.length;
  if (pa.length) {
    $('#passiveTable').innerHTML = `<thead><tr>
        <th>通道</th><th>收到报文</th><th>疑似电量字节</th></tr></thead><tbody>
      ${pa.map(p => `<tr>
        <td><code>${esc(p.usage_page)}/${esc(p.usage)}</code></td>
        <td>${p.frames} 帧</td>
        <td>${p.candidates.length
          ? p.candidates.map(c => `<span class="chip amber">byte[${c.offset}] = ${c.value}</span>`).join(' ')
          : '<span class="hint">无</span>'}</td>
      </tr>`).join('')}</tbody>`;
  }
}

async function startSmart() {
  const res = await api('/api/smarttest/start', {});
  if (!res.ok) return toast(res.error || '启动失败', 'err');
  clearInterval(S.smartTimer);
  S.smartTimer = setInterval(async () => {
    const st = await api('/api/smarttest');
    S.smart = st;
    renderSmart();
    if (!st.running) {
      clearInterval(S.smartTimer);
      toast(st.error ? '测试出错：' + st.error : (st.summary || '测试完成'), st.error ? 'err' : 'ok');
      loadState();
    }
  }, 800);
}

function goCrack(name, vid, pid) {
  switchView('capture');
  S.prefill = { name, vid, pid };
  toast('去「抓包破解」按提示抓一次报文即可补上这条协议');
}

/* ---------------------------------------------------------------- 抓包向导 */
function gotoStep(n) {
  S.step = n;
  $$('.wizard-pane').forEach(p => p.classList.toggle('active', +p.dataset.pane === n));
  $$('.step-pill').forEach(p => {
    const i = +p.dataset.step;
    p.classList.toggle('active', i === n);
    p.classList.toggle('done', i < n);
  });
}

function renderCaptureStep() {
  renderEnvChecks();
  renderHubs();
  gotoStep(S.step);
  if (S.step === 3 && S.capture.active) startCapturePoll();
}

function renderEnvChecks() {
  const env = S.env || {};
  const rows = [
    {
      ok: env.usbpcap, title: 'USBPcap 已安装',
      sub: env.usbpcap ? env.usbpcap_path : '去 usbpcap 官网下载安装后重开本程序',
    },
    {
      ok: env.admin, title: '管理员权限',
      sub: env.admin ? '具备抓取 USB 报文的权限' : '普通权限抓不到 USB 报文，需要提权重启',
      action: env.admin ? '' : `<button class="btn primary small" onclick="relaunchAdmin()">以管理员重启</button>`,
    },
    {
      ok: (env.hubs || []).length > 0, title: '发现可用集线器',
      sub: (env.hubs || []).map(h => h.text).join('、') || '没有探测到 USBPcap 设备',
    },
  ];
  $('#envChecks').innerHTML = rows.map(r => `<div class="check-row ${r.ok ? 'ok' : 'bad'}">
      <div class="st">${r.ok ? '✓' : '!'}</div>
      <div class="txt"><h5>${esc(r.title)}</h5><p>${esc(r.sub)}</p></div>
      ${r.action || ''}
    </div>`).join('');
}

function renderHubs() {
  const hubs = (S.env.hubs || []);
  if (!S.hubs.size) hubs.forEach(h => S.hubs.add(h.index));
  $('#hubList').innerHTML = hubs.map(h => `<div class="hub-chip ${S.hubs.has(h.index) ? 'on' : ''}"
      onclick="toggleHub(${h.index})"><i>${S.hubs.has(h.index) ? '✓' : ''}</i>${esc(h.text)}</div>`).join('')
    || '<p class="note">没有可用集线器</p>';
}

function toggleHub(i) {
  if (S.hubs.has(i)) S.hubs.delete(i); else S.hubs.add(i);
  renderHubs();
}

async function relaunchAdmin() {
  const res = await api('/api/relaunch-admin', {});
  if (res.already) return toast('已经是管理员权限了');
  toast(res.ok ? '已发起提权，请在弹窗里点「是」' : '提权被取消', res.ok ? 'ok' : 'err');
}

async function startCapture() {
  if (!S.hubs.size) return toast('至少选一个集线器', 'err');
  const res = await api('/api/capture/start', { hubs: [...S.hubs], seconds: S.seconds });
  if (!res.ok) {
    if (res.code === 'need_admin') {
      toast('需要管理员权限才能抓包', 'err');
      renderEnvChecks();
      return;
    }
    return toast(res.error || '启动失败', 'err');
  }
  toast(`开始录制，${S.seconds} 秒后自动停止`, 'ok');
  gotoStep(3);
  startCapturePoll();
}

function startCapturePoll() {
  clearInterval(S.capTimer);
  S.capTimer = setInterval(async () => {
    const st = await api('/api/capture/status');
    S.capture = st;
    $('#captureTimer').textContent = (st.elapsed || 0).toFixed(1) + 's';
    $('#capFiles').textContent = (st.files || []).map(f => f.name).join('、') || '—';
    $('#capBytes').textContent = fmtBytes(st.total_bytes);
    $('#capRemain').textContent = st.remaining ? st.remaining.toFixed(0) + 's' : '—';
    if (!st.active) {
      clearInterval(S.capTimer);
      if (S.step === 3) finishCapture();
    }
  }, 900);
}

async function stopCapture() {
  await api('/api/capture/stop', {});
  clearInterval(S.capTimer);
  finishCapture();
}

async function finishCapture() {
  const res = await api('/api/capture/stop', {});
  if (!res.ok) toast('停止失败', 'err');
  gotoStep(4);
  await loadCaptures();
  const useful = (res.useful || []);
  if (!useful.length) {
    toast('这次没抓到有效数据（文件太小），可以重录一次', 'err');
  }
  analyzeFile(useful.length ? useful[0].name : ($('#captureFileSelect').value || ''));
}

async function loadCaptures() {
  const res = await api('/api/captures');
  S.captures = res.captures || [];
  const sel = $('#captureFileSelect');
  sel.innerHTML = S.captures.map(c => `<option value="${esc(c.name)}">${esc(c.name)} · ${fmtBytes(c.size)}</option>`).join('')
    || '<option value="">（没有抓包文件）</option>';
}

async function analyzeFile(name) {
  if (!name) { $('#captureSummary').textContent = '没有可分析的文件'; return; }
  $('#captureSummary').innerHTML = '<span class="spinner"></span> 正在解析…';
  const expect = $('#expectInput').value;
  const res = await api('/api/analyze?file=' + encodeURIComponent(name) + (expect ? '&expect=' + expect : ''));
  if (!res.ok) { $('#captureSummary').textContent = res.error || '解析失败'; return; }
  S.analyze = res;
  const sum = res.summary;
  const devs = (sum.devices || []).filter(d => d.name || d.packets > 100)
    .slice(0, 5).map(d => `dev=${d.dev}${d.name ? ' ' + esc(d.name) : ''}`).join('、');
  $('#captureSummary').innerHTML =
    `共 <b>${sum.packets}</b> 个报文、<b>${sum.duration}</b> 秒 · 设备：${devs || '—'}`;

  const ex = res.detail.exchanges || [];
  if (!ex.length) {
    $('#exchangeTable').innerHTML = '';
    $('#frameView').hidden = true;
    $('#btnToStep5').disabled = true;
    toast('没找到「请求→应答」配对，可能抓的时长不够，或厂商软件没发起查询', 'err');
    return;
  }
  $('#exchangeTable').innerHTML = `<thead><tr>
      <th>#</th><th>设备</th><th>端点</th><th>长度</th><th>时间</th><th>请求（前 8 字节）</th><th>候选</th></tr></thead><tbody>
    ${ex.map((e, i) => `<tr onclick="pickExchange(${i})" data-i="${i}">
      <td>${i + 1}</td>
      <td>dev=${e.dev}${e.name ? ' ' + esc(e.name) : ''}</td>
      <td>${e.ep}</td>
      <td>${e.len}</td>
      <td>${e.t}s</td>
      <td><code>${esc(e.out.split(' ').slice(0, 8).join(' '))}…</code></td>
      <td>${e.candidates.length} 个${(e.hits && e.hits.length) ? ` <span class="chip accent">命中 ${e.hits.length}</span>` : ''}</td>
    </tr>`).join('')}</tbody>`;
}

function pickExchange(i) {
  const ex = (S.analyze.detail.exchanges || [])[i];
  if (!ex) return;
  S.selectedExchange = ex;
  $$('#exchangeTable tbody tr').forEach(tr => tr.classList.toggle('selected', +tr.dataset.i === i));
  $('#frameView').hidden = false;
  $('#frameOut').textContent = ex.out;
  $('#frameIn').textContent = ex.in;
  const meta = [];
  meta.push(`<span class="chip ${ex.echo ? 'blue' : ''}">${ex.echo ? '第二字节为命令回显' : '无回显特征'}</span>`);
  meta.push(`<span class="chip">校验：${ex.checksum === 'none' ? '未检出' : ex.checksum}</span>`);
  meta.push(`<span class="chip">${ex.same_len ? '请求应答等长' : '请求应答不等长'}</span>`);
  meta.push(`<span class="chip amber">${ex.candidates.length} 个候选字节</span>`);
  $('#frameMeta').innerHTML = meta.join('');
  $('#btnToStep5').disabled = false;
}

function toStep5() {
  const ex = S.selectedExchange;
  if (!ex) return;
  gotoStep(5);
  S.pickedOffset = null;
  renderByteMap(ex.in, null);
  $('#offsetInput').value = ex.candidates.length === 1 ? ex.candidates[0].offset : '';
  $('#protoChecksum').value = ex.checksum === 'unknown' ? 'unknown' : ex.checksum;
  if (S.prefill) {
    $('#protoName').value = S.prefill.name || '';
    $('#protoVid').value = '0x' + S.prefill.vid;
    $('#protoPid').value = '0x' + S.prefill.pid;
  } else if (ex.name) {
    $('#protoName').value = ex.name;
  }
}

function renderByteMap(hex, expect) {
  const bytes = hex.split(' ').filter(Boolean);
  const target = expect === null || expect === undefined || expect === '' ? null : Number(expect);
  $('#byteMap').innerHTML = bytes.map((b, i) => {
    const v = parseInt(b, 16);
    let cls = 'byte';
    if (v >= 1 && v <= 100) cls += ' cand';
    if (target !== null && !Number.isNaN(target) && v === target) cls += ' hit';
    if (S.pickedOffset === i) cls += ' picked';
    return `<div class="${cls}" onclick="pickByte(${i}, this)">
      <span class="idx">${i}</span><span class="val">${b.toUpperCase()}</span></div>`;
  }).join('');
}

function pickByte(i, el) {
  S.pickedOffset = i;
  $('#offsetInput').value = i;
  $$('#byteMap .byte').forEach(b => b.classList.remove('picked'));
  el.classList.add('picked');
}

function locate() {
  const ex = S.selectedExchange;
  if (!ex) return;
  const expect = $('#expectInput').value;
  renderByteMap(ex.in, expect);
  const nums = ex.in.split(' ').map(b => parseInt(b, 16));
  const hits = nums.filter(v => v === Number(expect)).length;
  const near = nums.filter(v => Math.abs(v - Number(expect)) <= 1 && v !== Number(expect)).length;
  if (expect === '') toast('已列出所有 1~100 的字节', 'ok');
  else if (hits) toast(`找到 ${hits} 个等于 ${expect} 的字节`, 'ok');
  else if (near) toast(`没有正好等于 ${expect} 的字节，但有 ${near} 个相差 1 —— 电量会抖，点黄色字节试试`, 'err');
  else toast(`应答里没有接近 ${expect} 的字节，可能选错了帧`, 'err');
}

async function saveCracked() {
  const ex = S.selectedExchange;
  if (!ex) return toast('还没有选中报文', 'err');
  const name = $('#protoName').value.trim();
  if (!name) return toast('给协议起个名字', 'err');
  const offset = parseInt($('#offsetInput').value, 10);
  if (Number.isNaN(offset)) return toast('先指定电量所在下标', 'err');

  const reportId = 0x00;
  const request = [reportId, ...ex.out.split(' ').map(b => parseInt(b, 16))]
    .map(b => b.toString(16).padStart(2, '0')).join(' ');

  const proto = {
    name,
    vendor: $('#protoVendor').value.trim(),
    model: '',
    kind: $('#protoKind').value,
    transport: '24g',
    match: { vid: $('#protoVid').value.trim() || '0x0', pid: $('#protoPid').value.trim() || '0x0' },
    channel: {
      usage_page: $('#protoPage').value.trim() || '0x0',
      usage: $('#protoUsage').value.trim() || '0x0',
      report_len: ex.len + 1,
      via: 'output',
    },
    request,
    // 抓包帧的下标 +1 才是读取缓冲区里的下标：真正读设备时会前置 1 个报告 ID 字节。
    // 漏这一步会一直读错位置（迈从就会把 byte[8] 的电量读成 byte[9] 的 0）。
    response_offset: offset + 1,
    checksum: $('#protoChecksum').value,
    min_interval: 20,
    enabled: true,
    source: 'cracked',
    notes: $('#protoNotes').value.trim(),
  };
  const res = await api('/api/protocols/save', { protocol: proto });
  if (!res.ok) return toast(res.error || '保存失败', 'err');
  toast(`「${name}」已加入协议库并启用`, 'ok');
  S.step = 1;
  await loadState();
  switchView('protocols');
}

/* ---------------------------------------------------------------- 设置 */
function renderSettings() {
  const st = S.env || {};
  const cfg = S.settings || {};
  $('#setInterval').value = cfg.refresh_interval ?? 15;
  $('#setThreshold').value = cfg.low_battery_threshold ?? 20;
  $('#setShowDisconnected').checked = !!cfg.show_disconnected;
  $('#setNotify').checked = cfg.notify_on_device_change !== false;
  $('#setAutostart').checked = !!st.autostart;
}

async function saveSettings() {
  const body = {
    refresh_interval: Number($('#setInterval').value) || 15,
    low_battery_threshold: Number($('#setThreshold').value) || 20,
    show_disconnected: $('#setShowDisconnected').checked,
    notify_on_device_change: $('#setNotify').checked,
  };
  const res = await api('/api/settings', body);
  toast(res.ok ? '设置已保存' : '保存失败', res.ok ? 'ok' : 'err');
  loadState();
}

/* ---------------------------------------------------------------- 弹窗 */
function openModal(title, bodyHtml, footHtml) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = bodyHtml;
  $('#modalFoot').innerHTML = footHtml || '<button class="btn ghost" onclick="closeModal()">关闭</button>';
  $('#modal').hidden = false;
}
function closeModal() { $('#modal').hidden = true; }

function openNewProto() {
  openModal('新建协议', `
    <p>不确定帧结构时，别手填 —— 去「抓包破解」抓一次，能自动带出大部分字段。</p>
    <div class="field"><label>协议名 *</label><input class="input" id="npName" placeholder="例如 某某 K87"></div>
    <div class="field"><label>厂商</label><input class="input" id="npVendor" placeholder="例如 某某科技"></div>
    <div class="form-grid">
      <div class="field"><label>类型</label><select class="input select" id="npKind">
        <option value="keyboard">键盘</option><option value="mouse">鼠标</option>
        <option value="gamepad">手柄</option><option value="headset">耳机</option><option value="other">其它</option>
      </select></div>
      <div class="field"><label>报告长度</label><input class="input mono" id="npLen" value="33"></div>
      <div class="field"><label>VID</label><input class="input mono" id="npVid" placeholder="0x1234"></div>
      <div class="field"><label>PID</label><input class="input mono" id="npPid" placeholder="0x5678"></div>
      <div class="field"><label>UsagePage</label><input class="input mono" id="npPage" placeholder="0xFF00"></div>
      <div class="field"><label>Usage</label><input class="input mono" id="npUsage" placeholder="0x01"></div>
    </div>
    <div class="field"><label>请求帧（十六进制）*</label><input class="input mono" id="npFrame" placeholder="06 20 01 00 … 21"></div>
    <div class="field"><label>电量字节下标</label><input class="input mono" id="npOffset" value="4"></div>
  `, `<button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="createProto()">创建</button>`);
}

async function createProto() {
  const name = $('#npName').value.trim();
  if (!name) return toast('协议名必填', 'err');
  const proto = {
    name,
    vendor: $('#npVendor').value.trim(),
    kind: $('#npKind').value,
    transport: '24g',
    match: { vid: $('#npVid').value.trim() || '0x0', pid: $('#npPid').value.trim() || '0x0' },
    channel: {
      usage_page: $('#npPage').value.trim() || '0x0',
      usage: $('#npUsage').value.trim() || '0x0',
      report_len: Number($('#npLen').value) || 33,
      via: 'output',
    },
    request: $('#npFrame').value.trim(),
    response_offset: Number($('#npOffset').value) || 4,
    checksum: 'unknown',
    enabled: true,
    source: 'user',
  };
  const res = await api('/api/protocols/save', { protocol: proto });
  if (!res.ok) return toast(res.error || '创建失败', 'err');
  closeModal();
  toast('已创建', 'ok');
  loadState();
}

function openImport() {
  openModal('导入协议', `
    <p>粘贴别的机器导出的 JSON，或选择一个 .json 文件。导入不会覆盖本地已有协议（会自动改名生成新 id）。</p>
    <div class="field"><label>选择文件</label><input type="file" accept=".json,application/json" id="impFile" class="input"></div>
    <div class="field"><label>或者粘贴 JSON</label><textarea class="input area" id="impText" rows="8" placeholder='{ "name": "...", "match": {...} }'></textarea></div>
    <div class="field"><label>重命名为（可选）</label><input class="input" id="impRename" placeholder="留空则用文件里的名字"></div>
  `, `<button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="doImport()">导入</button>`);
  $('#impFile').addEventListener('change', async ev => {
    const f = ev.target.files[0];
    if (f) $('#impText').value = await f.text();
  });
}

async function doImport() {
  const text = $('#impText').value.trim();
  if (!text) return toast('先选文件或粘贴 JSON', 'err');
  let data;
  try { data = JSON.parse(text); } catch (e) { return toast('JSON 格式不对：' + e.message, 'err'); }
  const res = await api('/api/protocols/import', { protocol: data, rename: $('#impRename').value.trim() });
  if (!res.ok) return toast(res.error || '导入失败', 'err');
  closeModal();
  toast('已导入并启用', 'ok');
  loadState();
}

/* ---------------------------------------------------------------- 绑定 */
function bind() {
  $$('.nav-item').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));

  $('#btnRefresh').addEventListener('click', async () => {
    await api('/api/refresh', {});
    setTimeout(loadState, 900);
    toast('已请求刷新');
  });

  $('#protoSearch').addEventListener('input', renderProtocols);
  $('#btnNewProto').addEventListener('click', openNewProto);
  $('#btnImport').addEventListener('click', openImport);

  $('#btnSmartStart').addEventListener('click', startSmart);

  $('#btnStep1Next').addEventListener('click', () => gotoStep(2));
  $$('[data-goto]').forEach(b => b.addEventListener('click', () => gotoStep(+b.dataset.goto)));
  $('#btnStartCapture').addEventListener('click', startCapture);
  $('#btnStopCapture').addEventListener('click', stopCapture);
  $('#btnToStep5').addEventListener('click', toStep5);
  $('#captureFileSelect').addEventListener('change', e => analyzeFile(e.target.value));
  $('#btnLocate').addEventListener('click', locate);
  $('#btnSaveProto').addEventListener('click', saveCracked);

  $$('#durSeg button').forEach(b => b.addEventListener('click', () => {
    $$('#durSeg button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    S.seconds = +b.dataset.sec;
  }));

  $('#btnSaveSettings').addEventListener('click', saveSettings);
  $('#btnRelaunch').addEventListener('click', relaunchAdmin);
  $('#btnOpenCaps').addEventListener('click', async () => {
    const res = await api('/api/open-folder', {});
    if (res.ok) toast('已在资源管理器打开：' + res.path); else toast('打开失败', 'err');
  });
  $('#setAutostart').addEventListener('change', async e => {
    const res = await api('/api/autostart', { enable: e.target.checked });
    toast(res.ok ? (res.enabled ? '已设为开机启动' : '已取消开机启动') : '操作失败', res.ok ? 'ok' : 'err');
  });
  $('#btnQuit').addEventListener('click', async () => {
    if (!confirm('退出电之脉动？托盘图标会一起消失。')) return;
    await api('/api/quit', {});
    document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;color:#5d6c7e;font:14px system-ui">电之脉动已退出</div>';
  });

  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
}

/* ---------------------------------------------------------------- 启动 */
(async function boot() {
  bind();
  await loadState();
  renderSettings();
  const hash = (location.hash || '').replace('#', '');
  if (VIEW_META[hash]) switchView(hash);
  setInterval(async () => {
    if (S.view === 'overview' || S.view === 'protocols') {
      const st = await api('/api/state');
      if (st.env) {
        S.env = st.env; S.devices = st.devices || []; S.protocols = st.protocols || [];
        renderEnv(); renderOverview();
        if (S.view === 'protocols') renderProtocols();
      }
    }
  }, 5000);
  setInterval(async () => {
    if (S.step === 4 && S.view === 'capture') await loadCaptures();
  }, 8000);
})();
