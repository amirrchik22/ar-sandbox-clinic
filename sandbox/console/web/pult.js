// Пульт специалиста: три экрана — «Сейчас», «Дети», «Записи».
//
// Главное правило: в живом занятии специалист не крутит ползунки, а нажимает
// одну крупную кнопку. Поэтому наверху экрана «Сейчас» — режимы, пауза и
// заморозка, а мелкие настройки спрятаны в сворачиваемый блок.
//
// С сервером общаемся короткими запросами к /api/... по договору:
//   GET  /api/state                    общее состояние (опрашиваем раз в секунду)
//   GET  /api/people                   специалисты и дети
//   POST /api/people/staff|child       добавить
//   PATCH/DELETE /api/people/child/{id}
//   POST /api/session/start|stop, /api/mode, /api/pause, /api/freeze
//   POST /api/calibrate, /api/snapshot, /api/record/start|stop
//   GET  /api/sessions, /api/sessions/{id}, PATCH /api/sessions/{id}
//   GET  /api/media?session={id}, DELETE /api/media/{id}, GET /media/{id}
//
// Программа на сервере может быть старее пульта: части API ещё нет. Тогда
// интерфейс не падает и не сыплет ошибками — он показывает спокойную надпись
// «появится после обновления программы» и продолжает работать тем, что есть.

'use strict';

// ------------------------------------------------------------------ константы

// Режимы придуманы для занятия, а не для настройки картинки: одно нажатие.
const MODES = [
  { id: 'map', title: 'Карта', hint: 'классическая топография', palette: 'topo' },
  { id: 'calm', title: 'Спокойный', hint: 'приглушённые цвета, мягкие переходы', palette: 'calm' },
  { id: 'two', title: 'Два цвета', hint: 'высоко и низко, максимальный контраст', palette: 'contrast' },
  { id: 'water', title: 'Только вода', hint: 'песок песочный, светится вода', palette: 'classic' },
];
const MODE_TITLES = {};
MODES.forEach((m) => { MODE_TITLES[m.id] = m.title; });

// Пока сервер не умеет /api/mode, ближайшая палитра старого API.
const PALETTE_TO_MODE = {};
MODES.forEach((m) => { PALETTE_TO_MODE[m.palette] = m.id; });

const DEFAULT_STEPS = [
  { value: 0, title: 'Авто' }, { value: 5, title: '5 мм' }, { value: 10, title: '10 мм' },
  { value: 20, title: '20 мм' }, { value: 50, title: '50 мм' },
];

const GUEST = '__guest__';
const SOON = 'Эта часть появится после обновления программы на компьютере.';

// ------------------------------------------------------------------ состояние

let st = null;              // разобранный ответ /api/state
let live = null;            // пульт живой картинки из live.js
let screen = 'now';
let running = false;
let secondsBase = 0;
let secondsAt = 0;

let localMode = null;       // выбранный режим, пока сервер его не помнит
let localPaused = false;
let localFrozen = false;
let localBright = 100;
let recording = false;

// Что умеет сервер. Программа на компьютере может быть старее пульта, поэтому
// перед работой спрашиваем у неё саму опись адресов (/openapi.json — её отдаёт
// FastAPI) и не стучимся туда, чего ещё нет. Опись не отдали — считаем, что
// сервер умеет всё по договору, и узнаём по ходу, по ответу «не знаю адреса».
const caps = { people: true, sessions: true, media: true, record: true, modes: true, toggles: true };

async function detectCaps() {
  const r = await api('GET', '/openapi.json');
  const paths = (r.ok && r.data && r.data.paths) ? Object.keys(r.data.paths) : null;
  if (!paths || !paths.length) return;
  const has = (prefix) => paths.some((x) => x === prefix || x.indexOf(prefix + '/') === 0);
  caps.people = has('/api/people');
  caps.sessions = has('/api/sessions');
  caps.media = has('/api/media');
  caps.record = has('/api/record');
  caps.modes = has('/api/mode');
  caps.toggles = has('/api/pause') || has('/api/freeze');
}

const people = { staff: [], children: [] };
let peopleLoaded = false;
let peopleLoading = false;
let peopleSignature = '';

const sel = { staff: null, child: null };

let sessionsCache = [];
let openReport = null;      // id открытого занятия

const el = (id) => document.getElementById(id);

// ------------------------------------------------------------------ мелочи

function toast(text) {
  const t = el('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.remove('show'), 2800);
}

// Любой запрос к серверу: никогда не бросает исключение, всегда даёт ответ.
async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(path, options);
    let data = null;
    const text = await r.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, offline: true };
  }
}

function fail(answer, quiet) {
  // Сервер ответил «не знаю такого адреса» — значит эта часть ещё не готова.
  if (answer.status === 404 || answer.status === 405 || answer.status === 501) return 'нет';
  if (answer.offline) { if (!quiet) toast('Нет связи с программой'); return 'связь'; }
  const detail = answer.data && (answer.data.detail || answer.data.message);
  if (!quiet) toast(typeof detail === 'string' ? detail : 'Не получилось');
  return 'ошибка';
}

function mmss(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  return String(m).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
}

// Длительность занятия. Без пояснения «19:40» читается как время на часах.
function durText(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return sec + ' сек';
  return mmss(sec) + ' мин';
}

function human(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(sec / 60);
  if (m < 1) return sec + ' сек';
  return m + ' мин';
}

function asDate(value) {
  if (value == null) return null;
  if (typeof value === 'number') return new Date(value < 1e11 ? value * 1000 : value);
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d;
}

function whenText(value) {
  const d = asDate(value);
  if (!d) return '';
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit' });
}

function asList(value) { return Array.isArray(value) ? value : []; }

function nameOf(p) {
  if (!p) return '';
  return p.name || p.alias || p.title || ('№' + p.id);
}

function aliasOf(c) {
  if (!c) return '';
  return c.alias || c.name || c.title || ('№' + c.id);
}

function childSettings(c) {
  const s = (c && c.settings) || {};
  return {
    mode: s.mode || c.mode || null,
    brightness: s.brightness != null ? s.brightness : (c.brightness != null ? c.brightness : null),
    duration_min: s.duration_min != null ? s.duration_min : (c.duration_min != null ? c.duration_min : null),
  };
}

function remember(key, value) {
  try { localStorage.setItem('sandbox.' + key, String(value)); } catch (e) { /* память браузера закрыта */ }
}

function recall(key) {
  try { return localStorage.getItem('sandbox.' + key); } catch (e) { return null; }
}

// ------------------------------------------------------------------ экраны

function showScreen(name) {
  screen = name;
  ['now', 'kids', 'media'].forEach((n) => {
    el('screen-' + n).hidden = n !== name;
    const tab = el('tab-' + n);
    tab.setAttribute('aria-selected', String(n === name));
  });
  el('screenTitle').textContent = { now: 'Сейчас', kids: 'Дети', media: 'Записи' }[name];
  window.scrollTo(0, 0);

  // Поток картинки держим только там, где он виден.
  if (live) { if (name === 'now') live.start(); else live.stop(); }
  if (name === 'kids') loadPeople({ reload: true });
  if (name === 'media') { loadSessions(); loadCamera(); }
}

// ------------------------------------------------------------------ люди

// opts.reload — перечитать список, opts.probe — спросить сервер, даже если
// раньше он про людей не знал (кнопка «Проверить ещё раз»).
async function loadPeople(opts) {
  const o = opts || {};
  if (!caps.people && !o.probe) { renderPeople(); renderKids(); return; }
  if (peopleLoading) return;
  if (peopleLoaded && !o.reload && !o.probe) return;
  peopleLoading = true;
  const r = await api('GET', '/api/people');
  peopleLoading = false;
  if (!r.ok) {
    if (fail(r, true) === 'нет') caps.people = false;
    renderPeople();
    renderKids();
    return;
  }
  caps.people = true;
  const d = r.data || {};
  people.staff = asList(d.staff || d.specialists || d.people);
  people.children = asList(d.children || d.kids);
  peopleLoaded = true;
  renderPeople();
  renderKids();
}

function personCard(item, chosen, onClick, extraClass) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'person' + (chosen ? ' on' : '') + (extraClass ? ' ' + extraClass : '');
  const title = document.createElement('span');
  title.textContent = item.label;
  b.appendChild(title);
  if (item.sub) {
    const sub = document.createElement('span');
    sub.className = 'sub';
    sub.textContent = item.sub;
    b.appendChild(sub);
  }
  b.onclick = onClick;
  return b;
}

function renderPeople() {
  const signature = JSON.stringify([people.staff, people.children, sel.staff, sel.child, caps.people]);
  if (signature === peopleSignature) return;
  peopleSignature = signature;

  const staffBox = el('staffList');
  staffBox.innerHTML = '';
  people.staff.forEach((p) => {
    staffBox.appendChild(personCard({ label: nameOf(p) }, String(sel.staff) === String(p.id),
      () => { sel.staff = p.id; remember('staff', p.id); renderPeople(); updateStartButton(); }));
  });
  staffBox.appendChild(personCard({ label: 'Гость', sub: 'без карточки' }, sel.staff === GUEST,
    () => { sel.staff = GUEST; remember('staff', GUEST); renderPeople(); updateStartButton(); }));
  staffBox.appendChild(personCard({ label: '+ Добавить' }, false, () => {
    el('staffAdder').hidden = false;
    el('staffName').focus();
  }, 'add'));

  const childBox = el('childList');
  childBox.innerHTML = '';
  people.children.forEach((c) => {
    const s = childSettings(c);
    const sub = s.mode ? MODE_TITLES[s.mode] || s.mode : '';
    childBox.appendChild(personCard({ label: aliasOf(c), sub: sub }, String(sel.child) === String(c.id),
      () => { chooseChild(c); }));
  });
  childBox.appendChild(personCard({ label: 'Гость', sub: 'без карточки' }, sel.child === GUEST,
    () => { sel.child = GUEST; renderPeople(); updateStartButton(); }));
  childBox.appendChild(personCard({ label: '+ Добавить' }, false, () => {
    el('childAdder').hidden = false;
    el('childAlias').focus();
  }, 'add'));

  el('pickHint').textContent = caps.people
    ? 'Имена детей не записываем — только условное имя, которое придумывает специалист.'
    : 'Списки появятся после обновления программы. Пока занятие можно начать как «Гость».';
}

// Выбрали ребёнка — его настройки применились сами, без перенастройки вручную.
async function chooseChild(c) {
  sel.child = c.id;
  renderPeople();
  updateStartButton();
  const s = childSettings(c);
  const parts = [];
  if (s.mode && MODE_TITLES[s.mode]) { await setMode(s.mode, true); parts.push(MODE_TITLES[s.mode]); }
  if (s.brightness != null) { await setBrightness(s.brightness, true); parts.push('яркость ' + s.brightness + ' %'); }
  if (parts.length) toast('Настройки карточки: ' + parts.join(', '));
}

function updateStartButton() {
  const ready = sel.staff != null && sel.child != null;
  el('btnStart').disabled = !ready;
  el('btnStart').textContent = ready ? 'Начать занятие' : 'Выберите, кто ведёт и с кем';
}

async function addPerson(kind, value) {
  const path = kind === 'staff' ? '/api/people/staff' : '/api/people/child';
  const body = kind === 'staff' ? { name: value } : { alias: value };
  const r = await api('POST', path, body);
  if (!r.ok) {
    if (fail(r, true) === 'нет') { caps.people = false; toast(SOON); renderPeople(); }
    else fail(r);
    return null;
  }
  peopleLoaded = false;
  await loadPeople({ reload: true });
  const added = (r.data && (r.data.staff || r.data.child || r.data.person)) || null;
  toast(kind === 'staff' ? 'Специалист добавлен' : 'Карточка создана');
  return added;
}

// ------------------------------------------------------------------ режимы

function buildModes() {
  const box = el('modes');
  box.innerHTML = '';
  MODES.forEach((m) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.mode = m.id;
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = m.title;
    const h = document.createElement('span');
    h.className = 'h';
    h.textContent = m.hint;
    b.appendChild(t);
    b.appendChild(h);
    b.onclick = () => setMode(m.id);
    box.appendChild(b);
  });
}

// Пока сервер не умеет /api/mode, его поле mode нам ничего не говорит —
// показываем то, что выбрал специалист.
function currentMode() {
  if (caps.modes && st && st.mode) return st.mode;
  if (localMode) return localMode;
  if (st && st.mode) return st.mode;
  const palette = st && st.view && st.view.palette;
  if (palette && PALETTE_TO_MODE[palette]) return PALETTE_TO_MODE[palette];
  return 'map';
}

async function setMode(id, quiet) {
  const mode = MODES.find((m) => m.id === id);
  if (!mode) return;
  localMode = id;
  markModes();
  if (caps.modes) {
    const r = await api('POST', '/api/mode', { mode: id });
    if (r.ok) { if (!quiet) toast('Режим: ' + mode.title); refresh(); return; }
    if (fail(r, true) !== 'нет') return;
    caps.modes = false;
  }
  // Старый сервер знает только палитры — берём ближайшую, чтобы пульт работал.
  const alt = await api('POST', '/api/settings', { palette: mode.palette });
  if (alt.ok) { if (!quiet) toast('Режим: ' + mode.title); refresh(); return; }
  if (!quiet) toast(SOON);
}

function markModes() {
  const now = currentMode();
  document.querySelectorAll('#modes button').forEach((b) => {
    b.classList.toggle('on', b.dataset.mode === now);
  });
}

// ------------------------------------------------------------------ пауза, заморозка

function isPaused() {
  if (caps.toggles && st && st.paused != null) return !!st.paused;
  return localPaused;
}

function isFrozen() {
  if (caps.toggles && st && st.frozen != null) return !!st.frozen;
  return localFrozen;
}

async function toggleSwitch(kind) {
  const on = kind === 'pause' ? !isPaused() : !isFrozen();
  if (kind === 'pause') localPaused = on; else localFrozen = on;
  markSwitches();
  const path = kind === 'pause' ? '/api/pause' : '/api/freeze';
  const r = caps.toggles
    ? await api('POST', path, { on: on })
    : { ok: false, status: 404, data: null };
  if (r.ok) { refresh(); return; }
  if (fail(r, true) === 'нет') {
    caps.toggles = false;
    toast(kind === 'pause'
      ? 'Пауза появится после обновления программы: проекция пока не гаснет.'
      : 'Заморозка появится после обновления программы: картинка пока живая.');
  }
}

function markSwitches() {
  const paused = isPaused();
  const frozen = isFrozen();
  const p = el('btnPause');
  p.classList.toggle('on', paused);
  p.setAttribute('aria-pressed', String(paused));
  el('pauseState').textContent = paused ? 'проекция погашена' : 'выключена';
  const f = el('btnFreeze');
  f.classList.toggle('on', frozen);
  f.setAttribute('aria-pressed', String(frozen));
  el('freezeState').textContent = frozen ? 'картинка замерла' : 'выключена';

  el('previewVeil').hidden = !paused;
  el('previewChip').hidden = !frozen;
  if (live) {
    if (frozen) live.freeze();
    else if (screen === 'now') live.thaw();
  }
}

// ------------------------------------------------------------------ мелкие настройки

function buildSteps(list) {
  const box = el('steps');
  const steps = (list && list.length) ? list : DEFAULT_STEPS;
  const signature = steps.map((s) => s.value).join(',');
  if (box.dataset.built === signature) return;
  box.dataset.built = signature;
  box.innerHTML = '';
  steps.forEach((s) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.step = s.value;
    b.textContent = s.title;
    b.onclick = async () => {
      const r = await api('POST', '/api/settings', { contour_step_mm: s.value });
      if (r.ok) { toast('Линии: ' + s.title); refresh(); }
      else if (fail(r, true) === 'нет') toast(SOON);
    };
    box.appendChild(b);
  });
}

function buildPlaces(list) {
  const box = el('places');
  if (!box) return;
  const places = (list && list.length) ? list : [];
  const signature = places.map((p) => p.id).join(',');
  if (box.dataset.built === signature) return;
  box.dataset.built = signature;
  box.innerHTML = '';
  places.forEach((p) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.place = p.id;
    b.textContent = p.title;
    b.title = p.hint || '';
    b.onclick = async () => {
      const r = await api('POST', '/api/placement', { placement: p.id });
      if (r.ok) { toast(p.title); refresh(); }
      else if (fail(r, true) === 'нет') toast(SOON);
    };
    box.appendChild(b);
  });
}

async function setBrightness(value, quiet) {
  localBright = value;
  el('bright').value = String(value);
  el('brightVal').textContent = value + ' %';
  const r = await api('POST', '/api/settings', { brightness: value });
  if (!r.ok && fail(r, true) === 'нет' && !quiet) toast(SOON);
}

// ------------------------------------------------------------------ занятие

async function startSession() {
  const staffId = sel.staff === GUEST ? null : sel.staff;
  const childId = sel.child === GUEST ? null : sel.child;
  const staff = people.staff.find((p) => String(p.id) === String(staffId));
  const child = people.children.find((c) => String(c.id) === String(childId));
  const body = {
    staff_id: staffId,
    child_id: childId,
    // подпись под записью, если карточки нет
    staff_name: staff ? nameOf(staff) : 'Гость',
    child_alias: child ? aliasOf(child) : 'Гость',
    mode: currentMode(),
  };
  const r = await api('POST', '/api/session/start', body);
  if (!r.ok) { fail(r); return; }
  // Говорим вслух, что настройки ребёнка применились: иначе связь между
  // карточкой и тем, что происходит на песке, остаётся невидимой.
  if (child) {
    const cs = childSettings(child);
    const bits = [];
    if (cs.mode) bits.push(MODE_TITLES[cs.mode] || cs.mode);
    if (cs.brightness != null) bits.push('яркость ' + cs.brightness + ' %');
    toast(bits.length
      ? 'Занятие началось · из карточки: ' + bits.join(', ')
      : 'Занятие началось · в карточке настроек пока нет');
  } else {
    toast('Занятие началось');
  }
  refresh();
}

async function stopSession() {
  const r = await api('POST', '/api/session/stop');
  if (!r.ok) { fail(r); return; }
  toast('Занятие завершено');
  recording = false;
  sessionsCache = [];
  refresh();
  const report = r.data && (r.data.report || r.data.session);
  if (caps.sessions && report && report.id) {
    openReport = report.id;
    showScreen('media');
    loadSessions().then(() => showReport(report.id));
  }
}

// ------------------------------------------------------------------ дети

function renderKids() {
  const box = el('kidsList');
  const note = el('kidsOffline');
  if (!caps.people) {
    box.innerHTML = '';
    note.hidden = false;
    el('kidsRetry').hidden = false;
    note.textContent = 'Карточки детей появятся после обновления программы: сервер ещё не '
      + 'отвечает на /api/people. Занятие пока можно вести как «Гость».';
    el('kidsEmpty').hidden = true;
    el('btnAddKid').disabled = true;
    return;
  }
  note.hidden = true;
  el('kidsRetry').hidden = true;
  el('btnAddKid').disabled = false;
  el('kidsEmpty').hidden = people.children.length > 0;

  const openIds = new Set();
  box.querySelectorAll('details.kid[open]').forEach((d) => openIds.add(d.dataset.id));
  box.innerHTML = '';

  people.children.forEach((c) => {
    const s = childSettings(c);
    const d = document.createElement('details');
    d.className = 'kid';
    d.dataset.id = String(c.id);
    // Карточка раскрыта сразу, если детей немного: настройки не должны прятаться
    // за нажатием, которое надо угадать. Проверено на Амире 24.09 — не нашёл их.
    if (openIds.has(String(c.id)) || people.children.length <= 4) d.open = true;

    const sum = document.createElement('summary');
    const left = document.createElement('div');
    const nm = document.createElement('div');
    nm.className = 'name';
    nm.textContent = aliasOf(c);
    left.appendChild(nm);
    const meta = document.createElement('div');
    meta.className = 'meta';
    const bits = [];
    if (s.mode) bits.push('режим: ' + (MODE_TITLES[s.mode] || s.mode));
    if (s.brightness != null) bits.push('яркость ' + s.brightness + ' %');
    if (s.duration_min != null) bits.push(s.duration_min + ' мин');
    meta.textContent = bits.join(' · ') || 'настройки не заданы — нажмите, чтобы задать';
    left.appendChild(meta);
    sum.appendChild(left);
    const right = document.createElement('div');
    right.style.display = 'flex';
    right.style.alignItems = 'center';
    right.style.gap = '10px';
    const count = document.createElement('span');
    count.className = 'count';
    const n = c.sessions_count != null ? c.sessions_count : asList(c.sessions).length;
    count.textContent = 'занятий: ' + n;
    const arrow = document.createElement('span');
    arrow.className = 'arrow';
    arrow.textContent = '▾';
    right.appendChild(count);
    right.appendChild(arrow);
    sum.appendChild(right);
    d.appendChild(sum);

    const body = document.createElement('div');
    body.className = 'body';

    const lab1 = document.createElement('div');
    lab1.className = 'lab';
    lab1.innerHTML = '<span>Любимый режим</span>';
    body.appendChild(lab1);
    const modes = document.createElement('div');
    modes.className = 'mode-chips';
    MODES.forEach((m) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = s.mode === m.id ? 'on' : '';
      b.textContent = m.title;
      b.onclick = () => saveChild(c, { mode: m.id });
      modes.appendChild(b);
    });
    body.appendChild(modes);

    const f = document.createElement('div');
    f.className = 'field';
    f.style.marginTop = '14px';
    const lab2 = document.createElement('div');
    lab2.className = 'lab';
    const bval = s.brightness != null ? s.brightness : 100;
    lab2.innerHTML = '<span>Яркость</span><b>' + bval + ' %</b>';
    const range = document.createElement('input');
    range.type = 'range';
    range.min = '30';
    range.max = '100';
    range.step = '5';
    range.value = String(bval);
    range.oninput = () => { lab2.querySelector('b').textContent = range.value + ' %'; };
    range.onchange = () => saveChild(c, { brightness: Number(range.value) }, true);
    f.appendChild(lab2);
    f.appendChild(range);
    body.appendChild(f);

    const area = document.createElement('textarea');
    area.placeholder = 'Заметки специалиста: что получается, что отвлекает, на что обратить внимание';
    area.value = c.note || c.notes || '';
    body.appendChild(area);

    const row = document.createElement('div');
    row.className = 'row2 stack';
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'green';
    save.textContent = 'Сохранить заметку';
    save.onclick = () => saveChild(c, { note: area.value });
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'ghost';
    del.textContent = 'Убрать из списка';
    del.onclick = () => removeChild(c);
    row.appendChild(save);
    row.appendChild(del);
    body.appendChild(row);

    const last = asList(c.last_sessions || c.sessions);
    if (last.length) {
      const h = document.createElement('div');
      h.className = 'lab';
      h.style.marginTop = '14px';
      h.innerHTML = '<span>Последние занятия</span>';
      body.appendChild(h);
      const ul = document.createElement('ul');
      ul.className = 'mini-list';
      last.slice(0, 5).forEach((item) => {
        const li = document.createElement('li');
        const a = document.createElement('span');
        a.textContent = whenText(item.started_at || item.date || item.t);
        const b = document.createElement('span');
        b.textContent = human(item.seconds != null ? item.seconds : item.duration);
        li.appendChild(a);
        li.appendChild(b);
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }

    d.appendChild(body);
    box.appendChild(d);
  });
}

// Запомнить то, что сейчас на экране, за ребёнком этого занятия.
// Это главный способ настроить ребёнка: специалист подбирает режим и яркость
// прямо во время занятия и одним нажатием закрепляет их за карточкой.
async function rememberForChild() {
  const cur = st || {};
  const ses = cur.session;
  if (!ses) { toast('Сначала начните занятие'); return; }
  const id = ses.child && ses.child.id;
  const child = id != null ? people.children.find((c) => String(c.id) === String(id)) : null;
  if (!child) { toast('Занятие идёт с «Гостем» — запоминать не за кем'); return; }
  const mode = cur.mode || currentMode();
  const rawState = cur.raw || {};
  const brightness = rawState.brightness != null ? rawState.brightness
    : (localBright != null ? localBright : undefined);
  await saveChild(child, { mode: mode, brightness: brightness }, true);
  const bits = [MODE_TITLES[mode] || mode];
  if (brightness != null) bits.push('яркость ' + brightness + ' %');
  toast('Сохранили в карточке: ' + aliasOf(child) + ' · ' + bits.join(', '));
}

async function saveChild(child, patch, quiet) {
  const current = childSettings(child);
  const settings = {
    mode: patch.mode !== undefined ? patch.mode : current.mode,
    brightness: patch.brightness !== undefined ? patch.brightness : current.brightness,
    duration_min: current.duration_min,
  };
  const body = { settings: settings };
  if (patch.note !== undefined) body.note = patch.note;
  const r = await api('PATCH', '/api/people/child/' + encodeURIComponent(child.id), body);
  if (!r.ok) {
    if (fail(r, true) === 'нет') toast(SOON);
    return;
  }
  // Обновляем карточку на месте, чтобы не схлопывать открытый блок.
  child.settings = settings;
  if (patch.note !== undefined) child.note = patch.note;
  if (!quiet) toast('Карточка сохранена');
  renderKids();
  peopleSignature = '';
  renderPeople();
}

async function removeChild(child) {
  if (!window.confirm('Убрать «' + aliasOf(child) + '» из списка? Записи занятий останутся.')) return;
  const r = await api('DELETE', '/api/people/child/' + encodeURIComponent(child.id));
  if (!r.ok) { if (fail(r, true) === 'нет') toast(SOON); return; }
  if (String(sel.child) === String(child.id)) sel.child = null;
  peopleLoaded = false;
  await loadPeople({ reload: true });
  updateStartButton();
  toast('Убрали из списка');
}

// ------------------------------------------------------------------ записи

async function loadSessions(probe) {
  const note = el('mediaOffline');
  const retry = el('mediaRetry');
  const offline = () => {
    note.hidden = false;
    retry.hidden = false;
    note.textContent = 'Список прошлых занятий появится после обновления программы: сервер '
      + 'ещё не отвечает на /api/sessions. Ниже — снимки текущего занятия.';
    renderSessions(legacySessions());
  };
  if (!caps.sessions && !probe) { offline(); return; }
  const r = await api('GET', '/api/sessions');
  if (!r.ok) {
    if (fail(r, true) === 'нет') caps.sessions = false;
    offline();
    return;
  }
  caps.sessions = true;
  note.hidden = true;
  retry.hidden = true;
  const d = r.data;
  sessionsCache = asList(Array.isArray(d) ? d : (d && d.sessions));
  renderSessions(sessionsCache);
}

// Пока нет /api/sessions, показываем хотя бы текущее занятие из /api/state.
function legacySessions() {
  if (!st || !st.raw || !st.raw.session) return [];
  const s = st.raw.session;
  return [{
    id: s.id,
    started_at: s.started_at,
    seconds: s.seconds,
    mode: currentMode(),
    child_alias: 'Занятие на этом компьютере',
    snapshots: asList(st.raw.snapshots),
    legacy: true,
  }];
}

function renderSessions(list) {
  const box = el('sessionsList');
  box.innerHTML = '';
  el('sessionsEmpty').hidden = list.length > 0;
  list.slice().sort((a, b) => (asDate(b.started_at) || 0) - (asDate(a.started_at) || 0)).forEach((s) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'session-row';
    const main = document.createElement('div');
    main.className = 'main';
    const who = document.createElement('div');
    who.className = 'who2';
    who.textContent = s.child_alias || (s.child && aliasOf(s.child)) || 'Гость';
    const when = document.createElement('div');
    when.className = 'when';
    const bits = [whenText(s.started_at)];
    const mode = s.mode || (s.settings && s.settings.mode);
    if (mode) bits.push(MODE_TITLES[mode] || mode);
    if (s.staff_name || s.staff) bits.push(s.staff_name || nameOf(s.staff));
    when.textContent = bits.filter(Boolean).join(' · ');
    main.appendChild(who);
    main.appendChild(when);
    const dur = document.createElement('span');
    dur.className = 'dur';
    dur.textContent = durText(s.seconds != null ? s.seconds : s.duration);
    b.appendChild(main);
    b.appendChild(dur);
    b.onclick = () => showReport(s.id, s);
    box.appendChild(b);
  });
}

async function showReport(id, fallback) {
  openReport = id;
  el('sessionsCard').hidden = true;
  el('reportCard').hidden = false;

  let report = fallback || null;
  if (caps.sessions && id != null) {
    const r = await api('GET', '/api/sessions/' + encodeURIComponent(id));
    if (r.ok && r.data) report = r.data.session || r.data.report || r.data;
    else if (fail(r, true) === 'нет') caps.sessions = false;
  }
  if (!report) report = { id: id };

  el('reportWho').textContent = report.child_alias || (report.child && aliasOf(report.child)) || 'Занятие';
  el('reportWhen').textContent = whenText(report.started_at);

  const mode = report.mode || (report.settings && report.settings.mode);
  const rows = [
    ['Длительность', durText(report.seconds != null ? report.seconds : report.duration)],
    ['Режим', mode ? (MODE_TITLES[mode] || mode) : '—'],
    ['Кто вёл', report.staff_name || (report.staff && nameOf(report.staff)) || 'Гость'],
    ['Занятие', 'развивающее и коррекционное, под контролем специалиста'],
  ];
  const ul = el('reportFacts');
  ul.innerHTML = '';
  rows.forEach(([k, v]) => {
    const li = document.createElement('li');
    const a = document.createElement('span');
    a.className = 'k';
    a.textContent = k;
    const b = document.createElement('span');
    b.className = 'v';
    b.textContent = v;
    li.appendChild(a);
    li.appendChild(b);
    ul.appendChild(li);
  });

  el('reportNote').value = report.note || report.notes || '';

  // Записи спрашиваем у библиотеки, а не берём из отчёта занятия. У библиотеки
  // и у удаления один и тот же адрес записи; у отчёта — свой, и кнопка «Удалить»
  // раньше всегда получала 404. Видео в отчёте тоже нет — оно есть только в
  // библиотеке. Проверено живьём 23.09.
  let media = [];
  if (caps.media && id != null && !report.legacy) {
    const r = await api('GET', '/api/media?session=' + encodeURIComponent(id));
    if (r.ok && r.data) {
      media = asList(Array.isArray(r.data) ? r.data : (r.data.media || r.data.items));
    } else if (fail(r, true) === 'нет') caps.media = false;
  }
  if (!media.length) media = asList(report.media);
  if (!media.length) media = asList(report.snapshots).map((s, i) => Object.assign({ id: s.id || ('snap' + i) }, s));

  const shots = asList(report.snapshots).filter((s) => s.exists !== false);
  renderThree(shots.length ? shots : media.filter(isPhoto));
  renderMedia(media);
}

function isPhoto(m) {
  const kind = String(m.kind || m.type || '').toLowerCase();
  if (kind.indexOf('video') >= 0) return false;
  const url = mediaUrl(m);
  return !/\.(mp4|mov|webm|m4v)(\?|$)/i.test(url);
}

function mediaUrl(m) {
  return m.url || (m.id != null ? '/media/' + encodeURIComponent(m.id) : '');
}

// Три снимка отчёта: начало, середина, конец занятия.
function renderThree(photos) {
  const box = el('reportThree');
  box.innerHTML = '';
  const labels = ['Начало', 'Середина', 'Конец'];
  const picked = [];
  if (photos.length >= 3) {
    picked.push(photos[0], photos[Math.floor(photos.length / 2)], photos[photos.length - 1]);
  } else {
    for (let i = 0; i < 3; i += 1) picked.push(photos[i] || null);
  }
  picked.forEach((p, i) => {
    const fig = document.createElement('figure');
    if (p) {
      const img = document.createElement('img');
      img.src = p.thumb || mediaUrl(p);
      img.alt = labels[i] + ' занятия';
      img.loading = 'lazy';
      fig.appendChild(img);
    } else {
      const blank = document.createElement('div');
      blank.className = 'blank';
      blank.textContent = 'снимка нет';
      fig.appendChild(blank);
    }
    const cap = document.createElement('figcaption');
    cap.textContent = labels[i] + (p && p.time ? ' · ' + p.time : '');
    fig.appendChild(cap);
    box.appendChild(fig);
  });
}

function renderMedia(list) {
  const box = el('reportMedia');
  box.innerHTML = '';
  el('reportMediaEmpty').hidden = list.length > 0;
  list.forEach((m) => {
    const url = mediaUrl(m);
    const item = document.createElement('div');
    item.className = 'item';
    if (isPhoto(m)) {
      const img = document.createElement('img');
      img.src = m.thumb || url;
      img.alt = 'Снимок занятия';
      img.loading = 'lazy';
      item.appendChild(img);
    } else {
      const video = document.createElement('video');
      video.src = url;
      video.controls = true;
      video.preload = 'metadata';
      video.playsInline = true;
      item.appendChild(video);
    }
    const bar = document.createElement('div');
    bar.className = 'bar';
    if (m.hint) {                       // например: видео не собралось, нет ffmpeg
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = m.hint;
      item.appendChild(hint);
    }
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = m.title || m.time || whenText(m.t || m.created_at)
      || (isPhoto(m) ? 'снимок' : 'видео');
    const acts = document.createElement('span');
    acts.className = 'acts';
    const down = document.createElement('a');
    down.href = url;
    down.download = '';
    down.textContent = 'Скачать';
    const del = document.createElement('button');
    del.type = 'button';
    del.textContent = 'Удалить';
    del.onclick = () => removeMedia(m);
    acts.appendChild(down);
    acts.appendChild(del);
    bar.appendChild(time);
    bar.appendChild(acts);
    item.appendChild(bar);
    box.appendChild(item);
  });
}

async function removeMedia(m) {
  if (!window.confirm('Удалить эту запись навсегда?')) return;
  const r = await api('DELETE', '/api/media/' + encodeURIComponent(m.id));
  if (!r.ok) { if (fail(r, true) === 'нет') toast(SOON); return; }
  toast('Запись удалена');
  if (openReport != null) showReport(openReport);
}

async function saveReportNote() {
  if (openReport == null) return;
  const r = await api('PATCH', '/api/sessions/' + encodeURIComponent(openReport),
    { note: el('reportNote').value });
  if (!r.ok) { if (fail(r, true) === 'нет') toast(SOON); return; }
  toast('Заметка сохранена');
}

// ------------------------------------------------------------------ состояние

function parseState(raw) {
  const view = raw.view || {};
  const session = raw.session || null;
  const pick = (key) => (raw[key] != null ? raw[key] : (view[key] != null ? view[key] : null));
  return {
    raw: raw,
    view: view,
    session: session,
    mode: pick('mode'),
    paused: pick('paused'),
    frozen: pick('frozen'),
    recording: pick('recording'),
  };
}

function tickTimer() {
  const extra = running ? (Date.now() - secondsAt) / 1000 : 0;
  el('timer').textContent = mmss(secondsBase + extra);
}

function renderFacts(view) {
  const stats = view.stats || {};
  const rows = [
    ['Датчик', view.sensor || '—'],
    ['Кадров в секунду', view.fps ? Number(view.fps).toFixed(1) : '—'],
    ['Ровный песок запомнен', view.calibrated
      ? (view.calibrated_at ? new Date(view.calibrated_at * 1000).toLocaleString('ru-RU') : 'да')
      : 'нет, нажмите «Запомнить ровный песок»'],
    ['Расстояние до песка', stats.distance_mm ? (stats.distance_mm / 1000).toFixed(2) + ' м' : '—'],
    ['Дрожание датчика', stats.noise_mm != null ? Number(stats.noise_mm).toFixed(1) + ' мм' : '—'],
    ['Перепад рельефа', stats.relief_mm != null ? Math.round(stats.relief_mm) + ' мм' : '—'],
    ['Руки в кадре', stats.hand_pct != null ? Math.round(stats.hand_pct) + ' %' : '—'],
  ];
  if (view.error) rows.push(['Сообщение', view.error]);
  if (view.demo && view.sensor_error) rows.push(['Почему демо', view.sensor_error]);
  const ul = el('facts');
  ul.innerHTML = '';
  rows.forEach(([k, v]) => {
    const li = document.createElement('li');
    const a = document.createElement('span');
    a.className = 'k';
    a.textContent = k;
    const b = document.createElement('span');
    b.className = 'v';
    b.textContent = v;
    li.appendChild(a);
    li.appendChild(b);
    ul.appendChild(li);
  });
}

function render(raw) {
  st = parseState(raw);
  const view = st.view;

  loadPeople();
  buildSteps(raw.contour_steps);
  buildPlaces(raw.placements);

  const badge = el('badge');
  badge.textContent = view.opening ? 'ищу датчик…' : (view.demo ? 'демо-режим' : 'датчик работает');
  badge.className = 'badge ' + (view.opening || view.demo ? 'demo' : 'live');

  const s = st.session;
  running = !!(s && s.running !== false && (s.running || s.ended_at == null));
  if (s && s.running === false) running = false;
  secondsBase = s ? (s.seconds || 0) : 0;
  secondsAt = Date.now();

  el('liveHead').hidden = !running;
  el('liveControls').hidden = !running;
  // Признак для раскладки: на ноутбуке до занятия и во время него удобны
  // разные расстановки карточек (см. style.css, блок «Ноутбук»).
  document.body.classList.toggle('running', !!running);
  el('pickCard').hidden = running;

  if (running) {
    const staffName = s.staff_name || (s.staff && nameOf(s.staff)) || 'Гость';
    const childAlias = s.child_alias || (s.child && aliasOf(s.child)) || 'Гость';
    const who = el('who');
    who.innerHTML = '';
    who.appendChild(document.createTextNode('ведёт '));
    const a = document.createElement('b');
    a.textContent = staffName;
    who.appendChild(a);
    who.appendChild(document.createTextNode(' · ребёнок: '));
    const b = document.createElement('b');
    b.textContent = childAlias;
    who.appendChild(b);
  }
  el('timer').classList.toggle('idle', !running);
  tickTimer();

  markModes();
  markSwitches();
  updateStartButton();

  // Блок «запомнить для ребёнка»: виден только когда есть за кем запоминать.
  const remBox = el('rememberBox');
  if (remBox) {
    const ses = st.session;
    const kid = ses && ses.child ? (ses.child.alias || ses.child_alias) : null;
    const named = kid && kid !== 'Гость';
    remBox.hidden = !named;
    if (named) {
      el('btnRemember').textContent = 'Запомнить эти настройки';
      el('rememberHint').textContent = 'Подберите режим и яркость — и сохраните в карточке: '
        + kid + '. В следующее занятие всё включится само.';
    }
  }
  if (st.recording != null) recording = !!st.recording;
  el('btnRecord').hidden = !caps.record;
  el('btnRecord').textContent = recording ? 'Остановить запись' : 'Записать видео';
  el('btnRecord').className = recording ? 'red' : '';

  const step = view.contour_step_mm;
  const place = raw.placement;
  const placeVal = el('placeVal');
  if (placeVal) placeVal.textContent = raw.placement_title || '—';
  document.querySelectorAll('#places button').forEach((b) => {
    b.classList.toggle('on', b.dataset.place === place);
  });
  const placeHint = el('placeHint');
  if (placeHint && Array.isArray(raw.placements)) {
    const cur = raw.placements.find((p) => p.id === place);
    if (cur && cur.hint) placeHint.textContent = cur.hint;
  }
  el('stepVal').textContent = step === 0 ? 'Авто' : (step != null ? step + ' мм' : '—');
  document.querySelectorAll('#steps button').forEach((b) => {
    b.classList.toggle('on', Number(b.dataset.step) === Number(step));
  });
  if (view.brightness != null && Number(view.brightness) !== localBright) {
    localBright = Number(view.brightness);
    el('bright').value = String(localBright);
    el('brightVal').textContent = localBright + ' %';
  }

  const stats = view.stats || {};
  const parts = [];
  if (view.calibrating) parts.push('Запоминаю ровный песок…');
  if (stats.step_mm) parts.push('линии через ' + stats.step_mm + ' мм');
  if (stats.relief_mm != null) parts.push('перепад ' + Math.round(stats.relief_mm) + ' мм');
  if (stats.noise_mm != null) parts.push('дрожание ' + Number(stats.noise_mm).toFixed(1) + ' мм');
  if (view.demo) parts.push('датчик не подключён, показан демо-рельеф');
  el('viewStats').textContent = view.opening
    ? 'Подключаюсь к датчику, это занимает до 20 секунд…'
    : (parts.join(' · ') || 'Жду первый кадр…');

  renderFacts(view);

  const addr = raw.addresses;
  if (addr) {
    el('projectorUrl').textContent = addr.projector || '/projector';
    el('consoleUrl').textContent = addr.console || location.href;
    el('consoleUrl').href = addr.console || location.href;
  }
}

async function refresh() {
  const r = await api('GET', '/api/state');
  if (!r.ok || !r.data) {
    el('badge').textContent = 'нет связи с программой';
    el('badge').className = 'badge demo';
    return;
  }
  render(r.data);
}

// ------------------------------------------------------------------ события

document.querySelectorAll('.tabs button').forEach((b) => {
  b.onclick = () => showScreen(b.dataset.screen);
});

el('btnStart').onclick = startSession;
el('btnStop').onclick = stopSession;

el('btnCalib').onclick = async () => {
  const r = await api('POST', '/api/calibrate');
  if (!r.ok) { fail(r); return; }
  toast('Разровняйте песок и не трогайте пару секунд');
  setTimeout(refresh, 1500);
};

// --- запись с камеры датчика: решение клиники, живёт на экране «Записи» ---

let camState = null;

async function loadCamera() {
  const card = el('cameraCard');
  if (!card) return;
  const r = await api('GET', '/api/record/camera');
  if (!r.ok || !r.data || !r.data.camera) { card.hidden = true; return; }
  camState = r.data.camera;
  card.hidden = !camState.available;
  renderCamera();
}

function renderCamera() {
  if (!camState) return;
  el('camVal').textContent = camState.mode_title || '—';
  const box = el('camModes');
  box.innerHTML = '';
  (camState.modes || []).forEach((m) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = m.title;
    if (m.id === camState.mode) b.className = 'on';
    b.onclick = async () => {
      const r = await api('POST', '/api/record/camera', { mode: m.id });
      if (!r.ok) { fail(r); return; }
      toast('Запись с камеры: ' + m.title);
      loadCamera();
    };
    box.appendChild(b);
  });
  const cur = (camState.modes || []).find((m) => m.id === camState.mode);
  el('camNote').textContent = cur ? (cur.note || '') : '';
  const warn = el('camWarn');
  warn.hidden = !(cur && cur.warning);
  if (cur && cur.warning) warn.textContent = cur.warning;
}

el('btnCamPreview').onclick = () => {
  const box = el('camPreviewBox');
  box.hidden = false;
  el('camPreview').src = '/api/record/camera/preview?t=' + Date.now();
};

el('btnRemember').onclick = () => rememberForChild();

el('btnSnap').onclick = async () => {
  const r = await api('POST', '/api/snapshot');
  if (!r.ok) { fail(r); return; }
  toast('Снимок сохранён');
  sessionsCache = [];
  refresh();
};

el('btnRecord').onclick = async () => {
  const path = recording ? '/api/record/stop' : '/api/record/start';
  const r = await api('POST', path);
  if (!r.ok) {
    if (fail(r, true) === 'нет') { caps.record = false; el('btnRecord').hidden = true; toast(SOON); }
    return;
  }
  recording = !recording;
  toast(recording ? 'Идёт запись видео' : 'Запись сохранена');
  refresh();
};

el('btnPause').onclick = () => toggleSwitch('pause');
el('btnFreeze').onclick = () => toggleSwitch('freeze');

el('btnProjector').onclick = () => { window.open('/projector', 'projector'); };

el('bright').oninput = () => {
  el('brightVal').textContent = el('bright').value + ' %';
};
el('bright').onchange = () => setBrightness(Number(el('bright').value));

el('staffAdder').onsubmit = async (e) => {
  e.preventDefault();
  const value = el('staffName').value.trim();
  if (!value) return;
  const added = await addPerson('staff', value);
  el('staffName').value = '';
  el('staffAdder').hidden = true;
  if (added && added.id != null) { sel.staff = added.id; peopleSignature = ''; renderPeople(); updateStartButton(); }
};
el('staffCancel').onclick = () => { el('staffAdder').hidden = true; el('staffName').value = ''; };

el('childAdder').onsubmit = async (e) => {
  e.preventDefault();
  const value = el('childAlias').value.trim();
  if (!value) return;
  const added = await addPerson('child', value);
  el('childAlias').value = '';
  el('childAdder').hidden = true;
  if (added && added.id != null) { sel.child = added.id; peopleSignature = ''; renderPeople(); updateStartButton(); }
};
el('childCancel').onclick = () => { el('childAdder').hidden = true; el('childAlias').value = ''; };

el('btnAddKid').onclick = () => { el('kidAdder').hidden = false; el('kidAlias').focus(); };
el('kidCancel').onclick = () => { el('kidAdder').hidden = true; el('kidAlias').value = ''; };
el('kidAdder').onsubmit = async (e) => {
  e.preventDefault();
  const value = el('kidAlias').value.trim();
  if (!value) return;
  await addPerson('child', value);
  el('kidAlias').value = '';
  el('kidAdder').hidden = true;
};

el('btnBackToList').onclick = () => {
  openReport = null;
  el('reportCard').hidden = true;
  el('sessionsCard').hidden = false;
  loadSessions();
};

el('btnSaveNote').onclick = saveReportNote;

el('kidsRetry').onclick = () => loadPeople({ probe: true, reload: true });
el('mediaRetry').onclick = () => loadSessions(true);

// ------------------------------------------------------------------ старт

buildModes();
buildSteps(null);
updateStartButton();
const rememberedStaff = recall('staff');
if (rememberedStaff) sel.staff = rememberedStaff === GUEST ? GUEST : rememberedStaff;
renderPeople();
live = liveImage(el('preview'), '/preview.mjpg', '/frame.jpg?kind=preview', 6);

// Сначала узнаём, что умеет программа на компьютере, потом начинаем работать.
detectCaps().then(() => {
  renderKids();
  refresh();
  setInterval(refresh, 1000);
  setInterval(tickTimer, 250);
});
