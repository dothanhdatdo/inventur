// Chạy ứng dụng Python (FastAPI) trong trình duyệt bằng Pyodide – dành cho GitHub Pages.
// Mỗi cú click link / submit form được chuyển thành request ASGI gửi tới app.browser.handle().

const PYODIDE_VERSION = '314.0.7';
const params = new URLSearchParams(location.search);
// ?pyodide=<url> chỉ dùng khi phát triển/kiểm thử với bản Pyodide cục bộ.
const INDEX_URL = params.get('pyodide') || `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
// Các gói có sẵn trong Pyodide (phụ thuộc được tải tự động).
const PACKAGES = ['fastapi', 'sqlalchemy', 'jinja2', 'micropip'];
const PYPI_PACKAGES = ['python-multipart', 'itsdangerous', 'pypdf'];
const APP_ROOT = new URL('../', import.meta.url); // thư mục inventur/
const COOKIE_KEY = 'inventur.cookies';

let py = null;
let handle = null;
let busy = false;

const $ = (id) => document.getElementById(id);
function bootStatus(text, pct) {
  const s = $('boot-status');
  if (s) s.textContent = text;
  if (pct !== undefined && $('boot-progress')) $('boot-progress').style.width = pct + '%';
}
function bootError(err) {
  console.error(err);
  const el = $('boot-error');
  if (!el) { alert(String(err)); return; }
  el.hidden = false;
  el.textContent = 'Không khởi động được ứng dụng:\n' + (err && err.message ? err.message : err) +
    '\n\nHãy tải lại trang. Nếu vẫn lỗi, thử trình duyệt Chrome/Edge/Firefox/Safari bản mới.';
}

// ------------------------------------------------------------------ khởi động
async function boot() {
  bootStatus('Đang tải Python (Pyodide)…', 10);
  const { loadPyodide } = await import(INDEX_URL + 'pyodide.mjs');
  py = await loadPyodide({ indexURL: INDEX_URL, env: { DATA_DIR: '/data', HOME: '/home/pyodide' } });

  bootStatus('Đang mở dữ liệu đã lưu…', 30);
  py.FS.mkdirTree('/data');
  py.FS.mount(py.FS.filesystems.IDBFS, {}, '/data');
  await syncFS(true);

  bootStatus('Đang tải thư viện (FastAPI, SQLAlchemy, Jinja2)…', 40);
  if (window.INVENTUR_TEST_WHEELS) {
    // Chế độ kiểm thử offline: cài wheel cục bộ thay vì tải từ CDN.
    await installWheels(window.INVENTUR_TEST_WHEELS);
  } else {
    await py.loadPackage(PACKAGES);
    await py.runPythonAsync(`import micropip\nawait micropip.install(${JSON.stringify(PYPI_PACKAGES)})`);
  }

  bootStatus('Đang tải mã nguồn ứng dụng…', 75);
  const manifest = await (await fetch(new URL('web/manifest.json', APP_ROOT), { cache: 'no-cache' })).json();
  await Promise.all(manifest.files.map(async (rel) => {
    const res = await fetch(new URL(rel + '?v=' + manifest.version, APP_ROOT));
    if (!res.ok) throw new Error('Không tải được ' + rel + ' (' + res.status + ')');
    const path = '/app/' + rel;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
    py.FS.writeFile(path, new Uint8Array(await res.arrayBuffer()));
  }));

  bootStatus('Đang chuẩn bị dữ liệu…', 90);
  window.inventurSendEmail = sendEmail;
  await py.runPythonAsync(`
import sys
sys.path.insert(0, '/app')
from app import browser
browser.startup()
`);
  handle = py.pyimport('app.browser').handle;
  await syncFS(false);

  // form.submit() (vd. onchange="this.form.submit()") không phát sự kiện submit -> chuyển sang requestSubmit().
  HTMLFormElement.prototype.submit = function submit() { this.requestSubmit(); };
  document.addEventListener('click', onClick);
  document.addEventListener('submit', onSubmit);
  window.addEventListener('popstate', () => navigate('GET', currentRoute(), null, null, false));
  await navigate('GET', currentRoute(), null, null, false);
}

async function installWheels(urls) {
  const code = `
import io, zipfile, site
def _install(data):
    zipfile.ZipFile(io.BytesIO(bytes(data))).extractall(site.getsitepackages()[0])
_install`;
  const install = await py.runPythonAsync(code);
  for (const u of urls) install(new Uint8Array(await (await fetch(u)).arrayBuffer()));
  py.runPython('import importlib; importlib.invalidate_caches()');
}

// Ghi dữ liệu xuống IndexedDB; xếp hàng để không chạy hai lần cùng lúc.
let syncChain = Promise.resolve();
function syncFS(populate) {
  syncChain = syncChain.then(() => new Promise((resolve) => {
    py.FS.syncfs(populate, (err) => { if (err) console.warn('syncfs', err); resolve(); });
  }));
  return syncChain;
}

// ------------------------------------------------------------------ cookie (phiên đăng nhập/flash)
function loadCookies() {
  try { return JSON.parse(localStorage.getItem(COOKIE_KEY) || '{}'); } catch { return {}; }
}
function saveCookies(jar) {
  try { localStorage.setItem(COOKIE_KEY, JSON.stringify(jar)); } catch { /* chế độ riêng tư */ }
}
let cookieJar = loadCookies();
function storeSetCookie(value) {
  const [pair, ...attrs] = value.split(';');
  const eq = pair.indexOf('=');
  const name = pair.slice(0, eq).trim();
  const val = pair.slice(eq + 1).trim();
  const expired = attrs.some((a) => {
    const [k, v] = a.trim().split('=');
    return (k.toLowerCase() === 'max-age' && Number(v) <= 0) || (k.toLowerCase() === 'expires' && new Date(v) < new Date());
  });
  if (expired || val === 'null' || val === '') delete cookieJar[name];
  else cookieJar[name] = val;
  saveCookies(cookieJar);
}
const cookieHeader = () => Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join('; ');

// ------------------------------------------------------------------ gọi app Python
async function request(method, route, body, contentType) {
  const url = new URL(route, 'https://inventur.local');
  const headers = [['accept', 'text/html'], ['cookie', cookieHeader()]];
  if (contentType) headers.push(['content-type', contentType]);
  const res = await handle(method, url.pathname, url.search.slice(1), headers, body ? new Uint8Array(body) : null);
  const out = res.toJs({ dict_converter: Object.fromEntries });
  res.destroy();
  const hdrs = out.headers.map(([k, v]) => [k.toLowerCase(), v]);
  for (const [k, v] of hdrs) if (k === 'set-cookie') storeSetCookie(v);
  return { status: out.status, headers: Object.fromEntries(hdrs), body: out.body };
}

async function navigate(method, route, body, contentType, push = true) {
  if (busy) return;
  busy = true;
  document.body.classList.add('busy');
  try {
    let res = await request(method, route, body, contentType);
    let hops = 0;
    while ([301, 302, 303, 307].includes(res.status) && res.headers.location && hops++ < 5) {
      route = new URL(res.headers.location, 'https://inventur.local' + route).pathname +
        new URL(res.headers.location, 'https://inventur.local').search;
      res = await request('GET', route, null, null);
    }
    // Lưu xuống IndexedDB sau mọi request (cả GET: vd. thông báo một lần được đánh dấu đã xem).
    // IDBFS chỉ ghi file đã thay đổi nên GET không đổi dữ liệu gần như không tốn gì.
    if (method !== 'GET') await syncFS(false);
    else syncFS(false);
    const type = res.headers['content-type'] || '';
    if (type.startsWith('text/html')) {
      render(new TextDecoder().decode(res.body), route, push || method !== 'GET' || hops > 0);
    } else {
      downloadBlob(res, route);
    }
  } catch (err) {
    console.error(err);
    alert('Lỗi: ' + (err.message || err));
  } finally {
    busy = false;
    document.body.classList.remove('busy');
  }
}

function currentRoute() {
  const h = location.hash.slice(1);
  return h.startsWith('/') ? h : '/';
}

// ------------------------------------------------------------------ hiển thị HTML
const blobUrls = [];
function render(html, route, push) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  document.title = doc.title || document.title;
  rewriteStatic(doc);
  const scripts = [...doc.body.querySelectorAll('script')];
  scripts.forEach((s) => s.remove());
  while (blobUrls.length) URL.revokeObjectURL(blobUrls.pop());
  document.body.className = '';
  document.body.replaceChildren(...doc.body.childNodes);
  const bar = document.createElement('div');
  bar.id = 'app-loading';
  document.body.appendChild(bar);
  if (push) history.pushState(null, '', '#' + route);
  else history.replaceState(null, '', '#' + route);
  window.scrollTo(0, 0);
  resolveAppResources();
  for (const s of scripts) {
    if (s.src) continue; // app.js và chart.js đã được nạp sẵn trong index.html
    try { (0, eval)(s.textContent); } catch (err) { console.error('inline script', err); }
  }
}

function rewriteStatic(doc) {
  const fix = (v) => (v && v.startsWith('/static/') ? new URL('app' + v, APP_ROOT).href : v);
  doc.querySelectorAll('[src]').forEach((el) => el.setAttribute('src', fix(el.getAttribute('src'))));
  doc.querySelectorAll('a[href^="/static/"]').forEach((el) => el.setAttribute('href', fix(el.getAttribute('href'))));
  // Ảnh/PDF do app Python phục vụ: bỏ src để trình duyệt không tải nhầm, resolveAppResources() sẽ điền blob URL.
  doc.querySelectorAll('img[src^="/"], iframe[src^="/"]').forEach((el) => {
    el.dataset.appSrc = el.getAttribute('src');
    el.removeAttribute('src');
  });
}

// Ảnh hoá đơn, PDF và file sao lưu do app Python trả về -> blob URL.
async function resolveAppResources() {
  const els = [
    ...document.querySelectorAll('[data-app-src]'),
    ...document.querySelectorAll('a[href$="/file"], a[href^="/settings/backup"]'),
  ];
  for (const el of els) {
    const attr = el.tagName === 'A' ? 'href' : 'src';
    const route = el.dataset.appSrc || el.getAttribute(attr);
    if (el.tagName === 'A' && el.hasAttribute('download')) {
      el.dataset.download = route;
      el.setAttribute('href', '#' + route);
      continue;
    }
    try {
      const res = await request('GET', route, null, null);
      if (res.status !== 200) continue;
      const url = URL.createObjectURL(new Blob([res.body], { type: res.headers['content-type'] || '' }));
      blobUrls.push(url);
      el.setAttribute(attr, url);
    } catch (err) { console.warn(route, err); }
  }
}

function downloadBlob(res, route) {
  const disp = res.headers['content-disposition'] || '';
  const m = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const name = m ? decodeURIComponent(m[1]) : route.split('/').pop();
  const url = URL.createObjectURL(new Blob([res.body], { type: res.headers['content-type'] || 'application/octet-stream' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ------------------------------------------------------------------ chặn click & submit
function onClick(e) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const a = e.target.closest('a');
  if (!a) return;
  if (a.dataset.download) {
    e.preventDefault();
    navigate('GET', a.dataset.download, null, null, false);
    return;
  }
  const href = a.getAttribute('href');
  if (!href || !href.startsWith('/') || href.startsWith('//') || a.target === '_blank') return;
  e.preventDefault();
  navigate('GET', href, null, null, true);
}

async function onSubmit(e) {
  // Lắng nghe ở pha bubble: onsubmit="return confirm(...)" trên form chạy trước.
  if (e.defaultPrevented) return;
  const form = e.target;
  const action = form.getAttribute('action') || currentRoute();
  if (!action.startsWith('/')) return;
  e.preventDefault();
  const data = new FormData(form, e.submitter || undefined);
  const method = (form.getAttribute('method') || 'GET').toUpperCase();
  if (method === 'GET') {
    const qs = new URLSearchParams([...data.entries()].filter(([, v]) => typeof v === 'string'));
    navigate('GET', action.split('?')[0] + '?' + qs.toString(), null, null, true);
    return;
  }
  const req = new Request('https://inventur.local/', { method: 'POST', body: data });
  const body = await req.arrayBuffer();
  navigate('POST', action, body, req.headers.get('content-type'), true);
}

// ------------------------------------------------------------------ email cảnh báo (FormSubmit.co)
async function sendEmail(alertId, recipientsJson, subject, text) {
  const recipients = JSON.parse(recipientsJson);
  let status = 'sent';
  const details = [];
  for (const to of recipients) {
    try {
      const res = await fetch('https://formsubmit.co/ajax/' + encodeURIComponent(to), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ _subject: subject, _template: 'box', _captcha: 'false', 'Cảnh báo': text }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || String(json.success) !== 'true') {
        status = 'failed';
        details.push(to + ': ' + (json.message || res.status));
      }
    } catch (err) {
      status = 'failed';
      details.push(to + ': ' + err.message);
    }
  }
  try {
    py.pyimport('app.browser').set_alert_status(alertId, status, details.join(' · '));
    await syncFS(false);
  } catch (err) { console.warn(err); }
}

window.inventur = { navigate, request, get py() { return py; } }; // tiện gỡ lỗi trong DevTools
boot().catch(bootError);
