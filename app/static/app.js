// Tiện ích giao diện (không cần framework).
const num = (v) => {
  if (v === null || v === undefined) return 0;
  let s = String(v).trim().replace(/\s|€/g, '');
  if (s.includes(',') && s.includes('.')) {
    s = s.lastIndexOf(',') > s.lastIndexOf('.') ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
  } else {
    s = s.replace(',', '.');
  }
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : 0;
};
const money = (v) => (v || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
const qty = (v) => (Math.round((v || 0) * 1000) / 1000).toLocaleString('de-DE');

// ---------- nhập hàng: kéo thả / xem trước ----------
function initDropzone() {
  const zone = document.getElementById('dropzone');
  const input = document.getElementById('fileInput');
  const img = document.getElementById('previewImg');
  const name = document.getElementById('fileName');
  const show = () => {
    const f = input.files[0];
    if (!f) return;
    name.textContent = f.name + ' · ' + Math.round(f.size / 1024) + ' KB';
    if (f.type.startsWith('image/')) {
      img.src = URL.createObjectURL(f);
      img.style.display = 'block';
    } else {
      img.style.display = 'none';
    }
  };
  input.addEventListener('change', show);
  // Nút "Chụp ảnh": mở camera (input riêng có capture) rồi chuyển ảnh sang ô file chính.
  const camera = document.getElementById('cameraInput');
  if (camera) camera.addEventListener('change', () => {
    if (!camera.files.length) return;
    try {
      const dt = new DataTransfer();
      dt.items.add(camera.files[0]);
      input.files = dt.files;
      show();
    } catch (err) { console.warn(err); }
  });
  ['dragenter', 'dragover'].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.remove('drag'); }));
  zone.addEventListener('drop', (ev) => { input.files = ev.dataTransfer.files; show(); });
  document.getElementById('scanForm').addEventListener('submit', () => {
    const btn = document.getElementById('scanBtn');
    btn.disabled = true;
    btn.textContent = 'Đang đọc hoá đơn… (có thể mất 10–30 giây)';
  });
}

// ---------- đối soát hoá đơn ----------
let nextRow = 0;
function initInvoiceReview(count) {
  nextRow = count;
  const lines = document.getElementById('lines');
  lines.querySelectorAll('.line').forEach(bindLine);
  if (count === 0 && !document.querySelector('fieldset[disabled]')) addLine();
  document.getElementById('i-vat')?.addEventListener('input', updateTotals);
  updateTotals();
}

function addLine() {
  const tpl = document.getElementById('lineTemplate').innerHTML.replaceAll('__ROW__', 'n' + nextRow++);
  const wrap = document.createElement('div');
  wrap.innerHTML = tpl.trim();
  const el = wrap.firstElementChild;
  document.getElementById('lines').appendChild(el);
  bindLine(el);
  el.querySelector('input[name^=raw_name]').focus();
}

function bindLine(el) {
  const f = (cls) => el.querySelector(cls);
  const qtyEl = f('.f-qty'), price = f('.f-price'), total = f('.f-total'), factor = f('.f-factor'), sel = f('.ing-select'), unit = f('.f-unit');
  const recalc = (src) => {
    if (src === 'qty' || src === 'price') {
      if (num(qtyEl.value) && num(price.value)) total.value = (num(qtyEl.value) * num(price.value)).toFixed(2);
    } else if (src === 'total' && num(qtyEl.value)) {
      price.value = (num(total.value) / num(qtyEl.value)).toFixed(2);
    }
    describe();
    updateTotals();
  };
  const describe = () => {
    const calc = f('.calc');
    el.classList.toggle('unmatched', !sel.value && !f('.del-box').checked);
    el.classList.toggle('removed', f('.del-box').checked);
    const ing = window.INGREDIENTS[sel.value];
    if (sel.value === 'new') { calc.textContent = 'Sẽ tạo nguyên liệu mới với tên trên hoá đơn.'; return; }
    if (!ing) { calc.textContent = 'Chưa gắn nguyên liệu – dòng này sẽ bị bỏ qua khi nhập kho.'; return; }
    const base = num(qtyEl.value) * (num(factor.value) || 1);
    const cost = base ? num(total.value) / base : 0;
    const areaInfo = (window.AREA_LABELS || {})[ing.area];
    let txt = `→ Nhập kho ${areaInfo ? areaInfo.icon + ' ' + areaInfo.label + ' · ' : ''}${qty(base)} ${ing.unit} · ${money(cost)}/${ing.unit}`;
    if (ing.last_price && cost) {
      const pct = (cost - ing.last_price) / ing.last_price * 100;
      if (Math.abs(pct) >= 3) txt += ` · ${pct > 0 ? '▲' : '▼'} ${Math.abs(pct).toFixed(1)}% so với lần trước (${money(ing.last_price)})`;
      if (Math.abs(pct) > 60) txt += ' — kiểm tra lại quy đổi!';
    }
    calc.textContent = txt;
  };
  qtyEl.addEventListener('input', () => recalc('qty'));
  price.addEventListener('input', () => recalc('price'));
  total.addEventListener('input', () => recalc('total'));
  factor.addEventListener('input', () => recalc());
  f('.del-box').addEventListener('change', () => recalc());
  sel.addEventListener('change', () => {
    const ing = window.INGREDIENTS[sel.value];
    if (ing) {
      const u = unit.value.trim().toLowerCase();
      if (u && u === ing.unit.toLowerCase()) factor.value = 1;
      else if (ing.pack_unit && u === ing.pack_unit.toLowerCase()) factor.value = ing.pack_size;
    }
    recalc();
  });
  describe();
}

function updateTotals() {
  let sub = 0, matched = 0, all = 0;
  document.querySelectorAll('#lines .line').forEach((el) => {
    if (el.querySelector('.del-box').checked) return;
    all++;
    sub += num(el.querySelector('.f-total').value);
    if (el.querySelector('.ing-select').value) matched++;
  });
  const vat = num(document.getElementById('i-vat')?.value);
  document.getElementById('sumSubtotal').textContent = money(sub);
  document.getElementById('sumTotal').textContent = money(sub + vat);
  document.getElementById('sumMatched').textContent = `${matched}/${all} dòng đã gắn nguyên liệu`;
}

function confirmImport() {
  const unmatched = [...document.querySelectorAll('#lines .line')].filter(
    (el) => !el.querySelector('.del-box').checked && !el.querySelector('.ing-select').value
  ).length;
  return confirm(unmatched ? `Còn ${unmatched} dòng chưa gắn nguyên liệu sẽ bị bỏ qua. Nhập kho?` : 'Xác nhận nhập kho hoá đơn này?');
}

// ---------- định lượng món ----------
function initRecipe() {
  document.getElementById('recipeRows').addEventListener('input', recipeCost);
  document.getElementById('recipeRows').addEventListener('change', recipeCost);
  document.getElementById('d-price').addEventListener('input', recipeCost);
  if (!document.querySelector('#recipeRows .recipe-row')) addRecipeRow();
  recipeCost();
}
function addRecipeRow() {
  document.getElementById('recipeRows').insertAdjacentHTML('beforeend', document.getElementById('recipeTemplate').innerHTML);
  recipeCost();
}
function recipeCost() {
  let cost = 0;
  document.querySelectorAll('#recipeRows .recipe-row').forEach((row) => {
    const ing = window.INGREDIENTS[row.querySelector('select').value];
    row.querySelector('.unit').textContent = ing ? ing.unit : '';
    if (ing) cost += num(row.querySelector('input').value) * ing.price;
  });
  document.getElementById('recipeCost').textContent = money(cost);
  const price = num(document.getElementById('d-price').value);
  document.getElementById('recipeFc').textContent = price ? `· food cost ${(cost / price * 100).toFixed(1)} %` : '';
}

// ---------- kiểm kê ----------
function initStocktake() {
  document.querySelectorAll('.count-input').forEach((input) => {
    input.addEventListener('input', () => {
      const row = input.closest('tr');
      const expected = parseFloat(row.querySelector('[data-expected]').dataset.expected);
      const cell = row.querySelector('.diff');
      if (input.value.trim() === '') { cell.textContent = ''; return; }
      const d = num(input.value) - expected;
      cell.textContent = (d > 0 ? '+' : '') + qty(d);
      cell.style.color = d < -0.0005 ? 'var(--danger)' : d > 0.0005 ? 'var(--ok)' : '';
    });
  });
}

// ---------- biểu đồ ----------
const PALETTE = ['#0f766e', '#c2410c', '#6d28d9', '#0369a1', '#a16207'];
function chartColors() {
  const css = getComputedStyle(document.documentElement);
  return { text: css.getPropertyValue('--muted').trim(), grid: css.getPropertyValue('--border').trim() };
}
function priceChart(canvas, series, unit) {
  if (!canvas || !window.Chart) return;
  const names = Object.keys(series);
  const labels = [...new Set(names.flatMap((n) => series[n].map((p) => p.x)))].sort();
  const c = chartColors();
  new Chart(canvas, {
    type: 'line',
    data: {
      labels: labels.map((d) => d.split('-').reverse().slice(0, 2).join('.')),
      datasets: names.map((n, i) => {
        const byDate = Object.fromEntries(series[n].map((p) => [p.x, p.y]));
        return { label: n, data: labels.map((d) => byDate[d] ?? null), borderColor: PALETTE[i % 5], backgroundColor: PALETTE[i % 5], spanGaps: true, tension: .25, pointRadius: 3 };
      }),
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: c.text } }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${money(ctx.parsed.y)}${unit ? '/' + unit : ''}` } } },
      scales: { x: { ticks: { color: c.text }, grid: { color: c.grid } }, y: { ticks: { color: c.text, callback: (v) => money(v) }, grid: { color: c.grid } } },
    },
  });
}
function dailyChart(canvas, rows, splitAreas) {
  if (!canvas || !window.Chart) return;
  const c = chartColors();
  const datasets = splitAreas
    ? [
        { label: '🍳 Bếp – doanh thu', data: rows.map((r) => r.kitchen || 0), backgroundColor: '#ea580c', stack: 'rev' },
        { label: '🍹 Quầy – doanh thu', data: rows.map((r) => r.bar || 0), backgroundColor: '#2563eb', stack: 'rev' },
        { label: 'Giá vốn', data: rows.map((r) => r.cost), backgroundColor: '#a3a3a3', stack: 'cost' },
      ]
    : [
        { label: 'Doanh thu', data: rows.map((r) => r.rev), backgroundColor: '#0f766e' },
        { label: 'Giá vốn', data: rows.map((r) => r.cost), backgroundColor: '#f59e0b' },
      ];
  new Chart(canvas, {
    type: 'bar',
    data: { labels: rows.map((r) => r.d.split('-').reverse().slice(0, 2).join('.')), datasets },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: c.text } }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${money(ctx.parsed.y)}` } } },
      scales: {
        x: { stacked: !!splitAreas, ticks: { color: c.text }, grid: { display: false } },
        y: { stacked: !!splitAreas, ticks: { color: c.text, callback: (v) => money(v) }, grid: { color: c.grid } },
      },
    },
  });
}

// ---------- copy tin nhắn đặt hàng ----------
function copyText(id, btn) {
  const el = document.getElementById(id);
  const done = () => { const old = btn.textContent; btn.textContent = '✓ Đã copy'; setTimeout(() => { btn.textContent = old; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(el.value).then(done, () => { el.select(); document.execCommand('copy'); done(); });
  } else {
    el.select(); document.execCommand('copy'); done();
  }
}
