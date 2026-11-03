/* VibeCloud Mini App — vanilla JS, no build step. */
(() => {
'use strict';

const tg = window.Telegram && window.Telegram.WebApp;
const params = new URLSearchParams(location.search);
const DEMO = !(tg && tg.initData) || params.has('demo');
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const tgv = (v) => !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v));
// LITE (Telegram-first): player streams straight from SoundCloud's CDN, MP3s live in Telegram.
const LITE = () => !!(S.config && S.config.lite);

const fmt = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
};
const fmtLong = (sec) => { const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60); return h ? `${h} ч ${m} мин` : `${m} мин`; };
const plural = (n, a, b, c) => { const n10 = n % 10, n100 = n % 100; return n10 === 1 && n100 !== 11 ? a : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? b : c; };
const tracksWord = (n) => `${n} ${plural(n, 'трек', 'трека', 'треков')}`;
const haptic = (k = 'light') => {
  try {
    const h = tg && tg.HapticFeedback; if (!h) return;
    if (['success', 'error', 'warning'].includes(k)) h.notificationOccurred(k);
    else if (k === 'select') h.selectionChanged(); else h.impactOccurred(k);
  } catch (_) { /* old clients */ }
};
const LINK_RE = /(?:https?:\/\/)?(?:(?:www|m|on)\.)?soundcloud\.com\/[^\s<>"')\]]+/i;
const linkKind = (url) => {
  const m = (url || '').match(LINK_RE); if (!m) return null;
  const u = m[0].replace(/^https?:\/\//, '').replace(/[?#].*$/, '').replace(/\/+$/, '');
  const seg = u.split('/').slice(1);
  if (u.startsWith('on.')) return { kind: 'short', label: 'Короткая ссылка', url: m[0] };
  if (seg.length === 1) return { kind: 'user', label: 'Профиль — выберешь, что перенести', url: m[0] };
  if (seg[1] === 'likes') return { kind: 'likes', label: 'Лайки целиком', url: m[0] };
  if (seg[1] === 'sets' && seg.length === 2) return { kind: 'user_sets', label: 'Все плейлисты профиля', url: m[0] };
  if (seg[1] === 'sets') return { kind: 'playlist', label: 'Плейлист / альбом', url: m[0] };
  if (seg[1] === 'tracks' || seg[1] === 'popular-tracks') return { kind: 'user_tracks', label: 'Все треки артиста', url: m[0] };
  return { kind: 'track', label: 'Трек', url: m[0] };
};

/* ------------------------------------------------------------------ icons */
const ic = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
const I = {
  home: ic('<path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H5.5A1.5 1.5 0 0 1 4 19z"/><path d="M9.5 20.5v-5a2.5 2.5 0 0 1 5 0v5"/>'),
  lib: ic('<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M14 7.5v6.2"/><circle cx="11.6" cy="14" r="2.4"/><path d="M14 7.5l2.5 1"/>'),
  move: ic('<path d="M4 8h13l-3.5-3.5"/><path d="M20 16H7l3.5 3.5"/>'),
  gift: ic('<rect x="3.5" y="8" width="17" height="12.5" rx="2.5"/><path d="M12 8v12.5M3.5 12.5h17"/><path d="M12 8c-1.5-3.5-5.5-3.8-5.5-1.3C6.5 8 9 8 12 8zm0 0c1.5-3.5 5.5-3.8 5.5-1.3C17.5 8 15 8 12 8z"/>'),
  search: ic('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>'),
  plus: ic('<path d="M12 5v14M5 12h14"/>'),
  heart: ic('<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z"/>'),
  down: ic('<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>'),
  send: ic('<path d="M21 3 10.2 13.8"/><path d="M21 3 14.5 21l-4.3-7.2L3 9.5z"/>'),
  share: ic('<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>'),
  more: ic('<circle cx="5.5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="18.5" cy="12" r="1.2" fill="currentColor"/>'),
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.2-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect fill="currentColor" x="6.5" y="5" width="4" height="14" rx="1.3"/><rect fill="currentColor" x="13.5" y="5" width="4" height="14" rx="1.3"/></svg>',
  next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M5 6.3v11.4a1 1 0 0 0 1.55.83L15 12.83a1 1 0 0 0 0-1.66L6.55 5.47A1 1 0 0 0 5 6.3z"/><rect fill="currentColor" x="16.5" y="5" width="2.6" height="14" rx="1.2"/></svg>',
  prev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19 6.3v11.4a1 1 0 0 1-1.55.83L9 12.83a1 1 0 0 1 0-1.66l8.45-5.7A1 1 0 0 1 19 6.3z"/><rect fill="currentColor" x="4.9" y="5" width="2.6" height="14" rx="1.2"/></svg>',
  shuffle: ic('<path d="M3.5 7h3.2c2.2 0 3.4 1 4.6 3l1.4 2.4c1.2 2 2.4 3 4.6 3h3.2"/><path d="m18 12.8 2.5 2.6L18 18"/><path d="M3.5 17h3.2c1.4 0 2.4-.4 3.2-1.2M13.6 8.2c.8-.8 1.8-1.2 3.2-1.2h3.7"/><path d="M18 4.4 20.5 7 18 9.6"/>'),
  repeat: ic('<path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H19l-3-3"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H5l3 3"/>'),
  chev: ic('<path d="m6 9 6 6 6-6"/>'),
  back: ic('<path d="M15 5l-7 7 7 7"/>'),
  bell: ic('<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>'),
  lock: ic('<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>'),
  check: ic('<path d="m5 12.5 4.5 4.5L19 7.5"/>', 'stroke-width="2.6"'),
  trash: ic('<path d="M4.5 7h15M9.5 7V4.8h5V7M6.5 7l.9 12.2A1.5 1.5 0 0 0 8.9 20.5h6.2a1.5 1.5 0 0 0 1.5-1.3L17.5 7"/>'),
  link: ic('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  clip: ic('<rect x="6" y="4.5" width="12" height="16" rx="2.5"/><path d="M9.5 4.5V3.8A1.3 1.3 0 0 1 10.8 2.5h2.4a1.3 1.3 0 0 1 1.3 1.3v.7"/><path d="M9.5 11h5M9.5 14.5h5"/>'),
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 1.5c.5 5.8 4.7 10 10.5 10.5-5.8.5-10 4.7-10.5 10.5C11.5 16.7 7.3 12.5 1.5 12 7.3 11.5 11.5 7.3 12 1.5z"/></svg>',
  sync: ic('<path d="M20 11a8 8 0 0 0-14.3-4.6L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 13a8 8 0 0 0 14.3 4.6L20 15.5"/><path d="M20 20v-4.5h-4.5"/>'),
  x: ic('<path d="M6 6l12 12M18 6 6 18"/>'),
  cloud: ic('<path d="M7 18.5h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.5 9.5 4.5 4.5 0 0 0 7 18.5z"/>'),
  alert: ic('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.2v.3"/>'),
};

/* ------------------------------------------------------------------ api */
const API = {
  async req(method, path, body) {
    if (DEMO) return window.VCDemo.handle(method, path, body);
    const r = await fetch(path, {
      method,
      headers: { Authorization: 'tma ' + tg.initData, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Ошибка ${r.status}`);
    return data;
  },
  get: (p) => API.req('GET', p),
  post: (p, b) => API.req('POST', p, b || {}),
  del: (p) => API.req('DELETE', p),
};

/* ------------------------------------------------------------------ state */
const S = {
  user: null, stats: null, config: {},
  tracks: new Map(), order: [], playlists: [], mixtapes: [],
  transit: new Map(), // jobId -> {id,title,artwork,kind,status,total,ids:[]}
  view: 'home', filter: 'all', q: '', remote: [], remoteQ: '',
  closers: [],
};
const T = (id) => S.tracks.get(+id);

function upsertTrack(t, { prepend = false } = {}) {
  const old = S.tracks.get(t.id);
  S.tracks.set(t.id, old ? { ...old, ...t } : t);
  if (!old) prepend ? S.order.unshift(t.id) : S.order.push(t.id);
  return !old;
}

/* ------------------------------------------------------------------ render helpers */
function coverHTML(t, cls = 'cover') {
  const letter = esc((t && (t.title || '?')).trim().charAt(0).toUpperCase());
  const img = t && t.artwork ? `<img src="${esc(t.artwork)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : '';
  return `<div class="${cls}"><div class="ph">${letter}</div>${img}<span class="eq"><i></i><i></i><i></i></span></div>`;
}
const coverImgOnly = (src, cls = '') => src ? `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : `<div class="ph ${cls}"></div>`;

function stateHTML(t) {
  switch (t.status) {
    case 'downloading': return `<span class="row-state">${t.progress || 0}%</span>`;
    case 'queued': return '<span class="row-state muted">в очереди</span>';
    case 'unavailable': return `<span class="row-state muted" title="Недоступен для скачивания">${I.lock}</span>`;
    case 'error': return `<span class="row-state muted" style="color:#ff7a6b" title="Ошибка">${I.alert}</span>`;
    case 'ready': return `<span class="row-state" title="${LITE() ? 'Уже в Telegram — придёт в чат мгновенно' : 'Скачан — играет мгновенно'}"><i class="off-dot"></i></span>`;
    default: return '';
  }
}

function rowHTML(t, { idx = null, arrive = false, i = 0, remote = false } = {}) {
  const playing = P.current() && P.current().id === t.id;
  const lead = idx != null ? `<span class="idx">${String(idx).padStart(2, '0')}</span>` : '';
  const right = remote
    ? `<button class="round" data-act="add-remote" aria-label="Добавить">${t.in_library ? I.check : I.plus}</button>`
    : `<button class="more" data-act="more" aria-label="Действия">${I.more}</button>`;
  return `<div class="row${playing ? ' playing' : ''}${playing && !P.playing ? ' paused' : ''}${arrive ? ' arrive' : ''}" style="--i:${i};--p:${t.progress || 0}"
      data-tid="${t.id}" data-status="${t.status}" data-act="${remote ? 'play-remote' : 'play'}" role="button" tabindex="0">
    ${lead}${coverHTML(t)}
    <div class="row-main"><b>${esc(t.title)}</b><span>${esc(t.artist)} · ${fmt(t.duration)}</span></div>
    <span class="st">${stateHTML(t)}</span>${right}<i class="prog"></i>
  </div>`;
}

function patchTrack(t) {
  $$(`[data-tid="${t.id}"]`).forEach((el) => {
    if (el.classList.contains('row')) {
      el.dataset.status = t.status;
      el.style.setProperty('--p', t.progress || 0);
      const st = $('.st', el); if (st) st.innerHTML = stateHTML(t);
    }
    if (el.classList.contains('card')) {
      const pill = $('.pill-top', el);
      if (pill) { pill.className = 'pill-top' + (t.status === 'ready' ? ' ready' : ''); pill.textContent = pillText(t); }
    }
  });
}
const pillText = (t) => t.status === 'downloading' ? `${t.progress || 0}%` : t.status === 'ready' ? (LITE() ? 'в TG' : 'офлайн') : t.status === 'queued' ? 'в очереди' : fmt(t.duration);

function splitLetters(text) {
  let i = 0;
  return esc(text).split(/\s+/).filter(Boolean).map((w) =>
    `<span class="wd">${[...w.replace(/&amp;/g, '&')].map((ch) => `<span class="l" style="--i:${i++}">${esc(ch)}</span>`).join('')}</span>`).join(' ');
}

const measureCtx = document.createElement('canvas').getContext('2d');
const isRu = (t) => /[\u0400-\u04FF]/.test(String(t || ''));
function fitGiant(text, width, maxSize, maxLines = 3, family = 'Anton') {
  const words = String(text).toUpperCase().split(/\s+/).filter(Boolean);
  // Anton has no Cyrillic — Russian titles render in Fira Sans Extra Condensed, so measure with it
  const fira = isRu(text) || family !== 'Anton';
  measureCtx.font = fira ? '900 100px "Fira Sans Extra Condensed", Impact' : '400 100px Anton, Impact';
  const longest = Math.max(...words.map((w) => measureCtx.measureText(w).width), 1);
  let size = Math.min(maxSize, (width / longest) * 100 * 0.96);
  const total = measureCtx.measureText(words.join(' ')).width * (size / 100);
  const lines = Math.ceil(total / width);
  if (lines > maxLines) size *= Math.sqrt(maxLines / lines);
  return Math.max(40, Math.floor(size));
}
const shortTitle = (s, n = 26) => {
  s = String(s || '').replace(/\s*[\[(].*?[\])]\s*/g, ' ').replace(/\s+/g, ' ').trim();
  if (s.length <= n) return s;
  const cut = s.slice(0, n); return cut.slice(0, cut.lastIndexOf(' ') > 8 ? cut.lastIndexOf(' ') : n) + '…';
};

function countUp(el, to, decimals = 0, from = 0) {
  if (reduced) { el.textContent = to.toFixed(decimals); return; }
  const t0 = performance.now(), dur = from ? 600 : 1100;
  el._run = t0;
  const step = (t) => {
    if (el._run !== t0) return;
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(2, -10 * k);
    el.textContent = (k === 1 ? to : from + (to - from) * e).toFixed(decimals);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ------------------------------------------------------------------ toast */
let toastTimer;
function toast(text, kind = 'ok') {
  const el = $('#toast');
  el.className = kind === 'err' ? 'err' : '';
  el.innerHTML = `<i></i><span>${esc(text)}</span>`;
  requestAnimationFrame(() => el.classList.add('on'));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), 2600);
  if (kind === 'err') haptic('error');
}

/* ------------------------------------------------------------------ views */
const VIEWS = ['home', 'library', 'import', 'mixtapes'];
function go(view, { force = false } = {}) {
  if (S.view === view && !force) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  S.view = view;
  $$('.view').forEach((v) => v.classList.toggle('on', v.dataset.view === view));
  const idx = VIEWS.indexOf(view);
  $$('#nav button').forEach((b, i) => b.classList.toggle('on', i === idx));
  const btn = $$('#nav button')[idx];
  $('#nav .blob').style.transform = `translateX(${btn.offsetLeft - 6}px)`;
  render(view);
  window.scrollTo(0, 0);
  haptic('select');
}
function render(view = S.view) {
  ({ home: renderHome, library: renderLibrary, import: renderImport, mixtapes: renderMixtapes })[view]();
}
let renderQueued = null;
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = requestAnimationFrame(() => { renderQueued = null; render(); });
}

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Доброй ночи' : h < 12 ? 'Доброе утро' : h < 18 ? 'Добрый день' : 'Добрый вечер';
}

function transitHTML() {
  const jobs = [...S.transit.values()];
  if (!jobs.length) return '';
  return `<div class="section-head" style="margin:0"><h2>В пути</h2><span class="live">LIVE</span></div>` + jobs.map((j) => {
    const ts = j.ids.map(T).filter(Boolean);
    const total = Math.max(j.total || 0, ts.length);
    const ready = ts.filter((t) => t.status === 'ready').length;
    const lite = LITE();
    // LITE: nothing is downloaded in advance — a track has "arrived" as soon as it is in the library
    const dots = ts.slice(0, 60).map((t) => `<i class="${t.status === 'ready' || (lite && t.status !== 'unavailable' && t.status !== 'error') ? 'on' : t.status === 'downloading' ? 'half' : ''}"></i>`).join('');
    const sub = lite
      ? (j.status === 'running' ? (j.total ? `Переношу · ${ts.length} из ${total}` : 'Читаю SoundCloud…') : `✓ ${tracksWord(total)} · слушай сразу`)
      : j.status === 'running' ? (j.total ? `Переношу · офлайн ${ready} из ${total}` : 'Читаю SoundCloud…')
        : ready >= total ? `✓ ${tracksWord(total)} · всё офлайн` : `${tracksWord(total)} в медиатеке · офлайн ${ready}`;
    return `<div class="job" data-job="${esc(j.id)}">
      ${j.artwork ? `<img src="${esc(j.artwork)}" alt="">` : '<div class="ph"></div>'}
      <div class="job-main"><b>${esc(j.title || 'Перенос')}</b><span>${sub}</span><div class="dotbar">${dots}</div></div>
      <span class="job-count">${ts.length}<small style="font-size:14px;opacity:.6">/${total || '…'}</small></span>
    </div>`;
  }).join('');
}

function renderHome() {
  const u = S.user || {}, st = S.stats || {};
  const name = u.first_name || 'друг';
  const recent = S.order.slice(0, 12).map(T).filter(Boolean);
  const avatar = u.photo_url ? `<img src="${esc(u.photo_url)}" alt="">` : esc(name.charAt(0));
  const today = new Date().getDay();
  const week = (st.week || []).map((on, i) => `<span class="${on ? 'on' : ''}${i === 6 ? ' today' : ''}"></span>`).join('');
  $('[data-view="home"]').innerHTML = `
    <header class="topbar">
      <div class="avatar">${avatar}</div>
      <div class="hello"><small>${greeting()},</small><b>${esc(name)}</b></div>
      <button class="icon-btn" data-act="search" aria-label="Поиск">${I.search}</button>
      <button class="icon-btn" data-act="go-mixtapes" aria-label="Микстейпы">${I.gift}</button>
    </header>

    <section class="hero" aria-label="Перенос из SoundCloud">
      <img class="hero-img" src="img/hero.jpg" alt="">
      <div class="hero-meta"><span>SoundCloud → Telegram<br>Vol.01</span><span>MMXXVI</span></div>
      <h1 class="blurword" data-text="VIBE">VIBE</h1>
      <p class="hero-tag">Кинь ссылку.<br>Остальное —<br>наше.</p>
      <span class="star s1">${I.star}</span><span class="star s2">${I.star}</span>
      <form class="drop" data-form="quick">
        <label class="sr" for="q-url">Ссылка SoundCloud</label>
        <input id="q-url" name="url" inputmode="url" autocomplete="off" placeholder="soundcloud.com/…" enterkeyhint="go">
        <button class="btn btn-lime" type="submit">Перенести</button>
      </form>
      <div class="paste-row">
        <button class="chip-mini" data-act="paste" type="button">${I.clip}Вставить</button>
        <button class="chip-mini" data-act="go-import" type="button">${I.move}Все способы</button>
      </div>
    </section>

    <section class="transit" id="transit-home">${transitHTML()}</section>

    <section class="section" aria-label="Статистика">
      <div class="stats">
        <div class="tile big"><span class="lbl">Перенесено</span><b class="num" data-count="${st.tracks || 0}">0</b><span class="dotgrid"></span>
          <div class="week" aria-label="Дни прослушивания на неделе">${week}</div></div>
        <div class="tile cobalt"><span class="lbl">${musicStat(st).label}</span><b class="num" data-count="${musicStat(st).value}">0</b></div>
        <div class="tile"><span class="lbl">Дней подряд</span><b class="num" data-count="${st.streak || 0}">0</b><span class="dotgrid"></span></div>
      </div>
    </section>

    <section class="section">
      <div class="section-head"><h2>Недавно перенесено</h2><button class="link" data-act="go-library">Все</button></div>
      <div id="home-recent">${recentHTML(recent)}</div>
    </section>
    <div id="home-pl">${homePlaylistsHTML()}</div>`;
  $$('[data-count]', $('[data-view="home"]')).forEach((el) => countUp(el, +el.dataset.count, +(el.dataset.dec || 0)));
  recent.forEach((t) => delete t._new);
}

const musicStat = (st) => { const h = (st && st.hours) || 0; return h < 10 ? { label: 'Минут музыки', value: Math.round(h * 60) } : { label: 'Часов музыки', value: Math.round(h) }; };
function recentHTML(recent) {
  return recent.length ? `<div class="rail" data-queue="recent">${recent.map((t, i) => `
    <div class="card${t._new ? ' arrive' : ''}" style="--i:${t._new ? 0 : i}" data-tid="${t.id}" data-act="play" role="button" tabindex="0">
      ${t.artwork ? `<img src="${esc(t.artwork)}" alt="" loading="lazy">` : '<div class="ph"></div>'}
      <span class="pill-top${t.status === 'ready' ? ' ready' : ''}">${pillText(t)}</span>
      <div class="card-txt"><b>${esc(shortTitle(t.title, 30))}</b><span>${esc(t.artist)}</span></div>
    </div>`).join('')}</div>`
    : '<div class="empty"><b>Пока пусто</b>Вставь ссылку на трек или плейлист — он появится здесь через пару секунд.</div>';
}
function homePlaylistsHTML() {
  return S.playlists.length ? `<section class="section">
      <div class="section-head"><h2>Плейлисты</h2><button class="link" data-act="lib-playlists">Все</button></div>
      <div class="list">${S.playlists.slice(0, 4).map(plRowHTML).join('')}</div>
    </section>` : '';
}
/* light refresh while tracks stream in: no full re-render, numbers roll from the current value */
function refreshHome() {
  const root = $('[data-view="home"]'); if (!root || !$('#home-recent', root)) return renderHome();
  const recent = S.order.slice(0, 12).map(T).filter(Boolean);
  const rail = $('.rail', root), sx = rail ? rail.scrollLeft : 0;
  $('#home-recent', root).innerHTML = recentHTML(recent);
  const rail2 = $('.rail', root); if (rail2) rail2.scrollLeft = sx;
  recent.forEach((t) => delete t._new);
  $('#home-pl', root).innerHTML = homePlaylistsHTML();
  const st = S.stats || {};
  const ms = musicStat(st), vals = [st.tracks || 0, ms.value, st.streak || 0];
  const lbl = $('.tile.cobalt .lbl', root); if (lbl) lbl.textContent = ms.label;
  $$('[data-count]', root).forEach((el, i) => {
    const to = vals[i], from = parseFloat(el.textContent) || 0;
    if (to !== from) countUp(el, to, +(el.dataset.dec || 0), from);
  });
  highlightPlaying();
}

function plRowHTML(p) {
  return `<div class="row" data-pid="${esc(p.id)}" data-act="open-pl" role="button" tabindex="0">
    <div class="cover">${coverImgOnly(p.artwork)}</div>
    <div class="row-main"><b>${esc(p.title)}</b><span>${p.kind === 'mixtape' ? '🎁 ' : ''}${esc(p.artist || '')} · ${tracksWord(p.track_count)}</span></div>
    ${p.watch ? `<span class="row-state" title="Слежу за обновлениями">${I.bell}</span>` : ''}
    <button class="round" data-act="play-pl" aria-label="Слушать">${I.play}</button>
  </div>`;
}

function filteredTracks() {
  let list = S.order.map(T).filter(Boolean);
  if (S.filter === 'liked') list = list.filter((t) => t.liked);
  if (S.filter === 'offline') list = list.filter((t) => t.status === 'ready');
  const q = S.q.trim().toLowerCase();
  if (q) list = list.filter((t) => (t.title + ' ' + t.artist).toLowerCase().includes(q));
  return list;
}

function renderLibrary({ keepSearch = false } = {}) {
  const root = $('[data-view="library"]');
  const all = S.order.map(T).filter(Boolean);
  const counts = { all: all.length, liked: all.filter((t) => t.liked).length, offline: all.filter((t) => t.status === 'ready').length, playlists: S.playlists.length };
  const chip = (id, label) => `<button class="chip${S.filter === id ? ' on' : ''}" data-act="filter" data-f="${id}">${label}<span class="n">${counts[id]}</span></button>`;
  const body = S.filter === 'playlists'
    ? (S.playlists.length ? `<div class="grid2">${S.playlists.map((p, i) => `
        <div class="pl-card arrive" style="--i:${i}" data-pid="${esc(p.id)}" data-act="open-pl" role="button" tabindex="0">
          ${coverImgOnly(p.artwork)}${p.watch ? `<span class="bell">${I.bell}</span>` : ''}
          <div class="txt"><b>${esc(shortTitle(p.title, 34))}</b><span>${tracksWord(p.track_count)} · ${fmtLong(p.duration)}</span></div>
        </div>`).join('')}</div>` : '<div class="empty"><b>Нет плейлистов</b>Перенеси плейлист, альбом или лайки — они появятся тут.</div>')
    : libraryListHTML();
  if (keepSearch && $('#lib-body', root)) { $('#lib-body', root).innerHTML = body; $('.chips', root).innerHTML = chipsHTML(); return; }
  function chipsHTML() { return chip('all', 'Все') + chip('liked', 'Лайки') + chip('playlists', 'Плейлисты') + chip('offline', LITE() ? 'В TG' : 'Офлайн'); }
  root.innerHTML = `
    <div class="page-title"><h1>Медиа<br>тека</h1><span class="count">${String(all.length).padStart(3, '0')}</span></div>
    <label class="search"><span class="sr">Поиск</span>${I.search}<input id="lib-q" type="search" placeholder="Трек, артист или ссылка" value="${esc(S.q)}" autocomplete="off" enterkeyhint="search"></label>
    <div class="chips">${chipsHTML()}</div>
    <div id="lib-body" style="margin-top:18px">${body}</div>`;
}

function libraryListHTML() {
  const list = filteredTracks();
  let html = list.length ? `<div class="list" data-queue="library">${list.slice(0, 400).map((t, i) => rowHTML(t, { arrive: !!t._new, i: Math.min(i, 8) })).join('')}</div>`
    : S.q ? '' : '<div class="empty"><b>Тут будет твоя музыка</b>Перенеси трек или плейлист из SoundCloud.</div>';
  list.forEach((t) => delete t._new);
  if (S.q.trim().length >= 2) {
    html += `<div class="section"><div class="section-head"><h2>В SoundCloud</h2></div>
      <div class="list" data-queue="remote" id="remote">${S.remoteQ === S.q ? (S.remote.length ? S.remote.map((t) => rowHTML(t, { remote: true })).join('') : '<div class="empty">Ничего не нашёл</div>') : '<div class="skeleton" style="height:64px"></div><div class="skeleton" style="height:64px;margin-top:8px"></div>'}</div></div>`;
  }
  return html;
}

function renderImport() {
  $('[data-view="import"]').innerHTML = `
    <div class="punk">
      <h1 class="punk-word">Drop</h1>
      <div class="punk-cols">
        <p>Трек, плейлист, альбом, лайки или целый профиль. <span class="hl">Одна ссылка</span> — и всё переезжает.</p>
        <p>${LITE() ? 'Список — мгновенно, слушай <span class="hl">сразу</span>. MP3 прилетают в чат прямо из Telegram.' : 'Список — мгновенно, MP3 — в фоне. Потом слушай <span class="hl">офлайн</span> и делись с друзьями.'}</p>
      </div>
      <h1 class="punk-word alt">Move</h1>
    </div>
    <form class="field" data-form="import">
      <label class="sr" for="imp-url">Ссылки SoundCloud</label>
      <textarea id="imp-url" name="url" rows="2" placeholder="Вставь одну или несколько ссылок soundcloud.com" autocomplete="off"></textarea>
      <div class="field-foot">
        <button class="chip-mini" style="background:var(--glass-2);color:var(--text)" type="button" data-act="paste-imp">${I.clip}Вставить</button>
        <span class="detected" id="detected">Жду ссылку…</span>
        <button class="btn btn-lime" type="submit">Перенести</button>
      </div>
    </form>
    <div id="preview-slot"></div>
    <section class="transit" id="transit-import" style="${S.transit.size ? '' : 'display:none'}">${transitHTML()}</section>
    <section class="section">
      <div class="section-head"><h2>Самые быстрые способы</h2></div>
      <div class="ways">
        <div class="way"><span class="n">01</span><div><b>Поделиться из SoundCloud</b><span>В приложении SoundCloud: «Поделиться» → Telegram → <code>@${esc(S.config.bot || 'VibeCloudBot')}</code>. Трек придёт MP3, плейлист — целиком.</span></div></div>
        <div class="way"><span class="n">02</span><div><b>Профиль целиком</b><span>Ссылка на профиль — и выбираешь: все лайки, треки артиста или все плейлисты разом.</span></div></div>
        <div class="way"><span class="n">03</span><div><b>Отправить другу из любого чата</b><span>Набери <code>@${esc(S.config.bot || 'VibeCloudBot')} название</code> в любом чате — трек уйдёт аудиофайлом.</span></div></div>
        <div class="way"><span class="n">04</span><div><b>Следить за плейлистом</b><span>Включи 🔔 у плейлиста — новые треки сами появятся в медиатеке, а бот напишет.</span></div></div>
      </div>
    </section>`;
}

const THEME_LABEL = { cobalt: 'Кобальт', red: 'Бетон', smoke: 'Дым', lime: 'Неон' };
function mixtapeCardHTML(m, i = 0) {
  const size = fitGiant(m.title, Math.min(innerWidth, 520) - 76, 92, 3, 'Fira Sans Extra Condensed');
  return `<article class="mx t-${esc(m.theme)} arrive" style="--i:${i}" data-code="${esc(m.code)}" data-act="open-mx" role="button" tabindex="0">
    <div class="mx-meta"><span>Mixtape · ${tracksWord(m.track_count)}</span><span class="claims">${m.claims ? `♥ ${m.claims}` : 'new'}</span></div>
    <h2 class="mx-title" style="font-size:${size}px">${esc(m.title)}</h2>
    ${m.note ? `<p style="margin:10px 0 0;font-weight:700;font-size:14px;opacity:.85">«${esc(m.note)}»</p>` : ''}
    <div class="mx-foot">
      <div class="stack">${(m.covers || []).slice(0, 4).map((c) => `<img src="${esc(c)}" alt="">`).join('')}</div>
      <span class="sp"></span>
      <button class="btn ${m.theme === 'lime' ? 'btn-lime' : m.theme === 'red' ? 'btn-paper' : 'btn-ink'}" data-act="share-mx">${I.send}Отправить</button>
    </div>
  </article>`;
}

function renderMixtapes() {
  $('[data-view="mixtapes"]').innerHTML = `
    <div class="page-title"><h1>Микс<br>тейпы</h1><span class="count">${String(S.mixtapes.length).padStart(2, '0')}</span></div>
    <p class="page-sub">Собери подборку и отправь другу. Он откроет постер и заберёт все треки себе в один тап.</p>
    <button class="btn btn-lime btn-block" style="margin-top:20px;min-height:56px" data-act="new-mx">${I.plus}Собрать микстейп</button>
    <div style="margin-top:20px">${S.mixtapes.length ? S.mixtapes.map(mixtapeCardHTML).join('')
      : '<div class="empty"><b>Ещё ни одного</b>Выбери треки, придумай название и стиль постера — и делись.</div>'}</div>`;
}

/* ------------------------------------------------------------------ player */
const P = {
  queue: [], idx: -1, playing: false, shuffle: false, repeat: 'off', source: '', counted: false, wave: [],
  audio: null,
  current() { return this.idx >= 0 ? T(this.queue[this.idx]) : null; },
};

function initAudio() {
  P.audio = DEMO ? new window.VCDemo.FakeAudio() : $('#audio');
  const a = P.audio;
  a.addEventListener('timeupdate', onTime);
  a.addEventListener('playing', () => { P.playing = true; setLoading(false); syncPlayState(); });
  a.addEventListener('pause', () => { P.playing = false; syncPlayState(); });
  a.addEventListener('waiting', () => setLoading(true));
  a.addEventListener('ended', () => (P.repeat === 'one' ? (a.currentTime = 0, a.play()) : next(true)));
  a.addEventListener('error', () => {
    const t = P.current();
    if (t && a._direct) return directFailed(t);
    setLoading(false);
    if (t && a.src) toast('Не удалось включить трек', 'err');
  });
  if ('mediaSession' in navigator) {
    const ms = navigator.mediaSession;
    ms.setActionHandler('play', () => toggle(true));
    ms.setActionHandler('pause', () => toggle(false));
    ms.setActionHandler('nexttrack', () => next());
    ms.setActionHandler('previoustrack', () => prev());
    try { ms.setActionHandler('seekto', (d) => { a.currentTime = d.seekTime; }); } catch (_) { /* unsupported */ }
  }
}

/* ---------- sources: SoundCloud CDN first, our server only as a fallback ---------- */
const HLS_CDN = 'https://cdn.jsdelivr.net/npm/hls.js@1.7.4/dist/hls.light.min.js';
const direct = new Map();         // track id -> {url, type, expires}
let hls = null, srcToken = 0, hlsLib = null;
const useDirect = () => !DEMO && !!S.config.direct;
function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  hlsLib = hlsLib || new Promise((res, rej) => {
    const add = (src, fallback) => {
      const el = document.createElement('script'); el.src = src; el.async = true;
      el.onload = () => (window.Hls ? res(window.Hls) : rej(new Error('hls')));
      el.onerror = () => (fallback ? add(fallback) : rej(new Error('hls')));
      document.head.appendChild(el);
    };
    add(HLS_CDN, 'js/vendor/hls.light.min.js');   // public CDN first — not even the player code comes from us
  }).catch((e) => { hlsLib = null; throw e; });
  return hlsLib;
}
function nativeHls() {
  const apple = tg && ['ios', 'macos'].includes(tg.platform);
  const can = !!P.audio.canPlayType && P.audio.canPlayType('application/vnd.apple.mpegurl') !== '';
  return can && (apple || !(window.MediaSource || window.ManagedMediaSource));
}
async function directFor(t, fresh = false) {
  const c = direct.get(t.id);
  if (!fresh && c && c.expires * 1000 - Date.now() > 60000) return c;
  const d = await API.get(`/api/track/${t.id}/direct`);
  if (d && d.url) { direct.set(t.id, d); return d; }
  direct.set(t.id, { url: null, expires: Date.now() / 1000 + 600 });
  return null;
}
function detachHls() { if (hls) { try { hls.destroy(); } catch (_) { /* noop */ } hls = null; } }
function seekWhenReady(a, at) { if (at > 0) a.addEventListener('loadedmetadata', () => { try { a.currentTime = at; } catch (_) { /* noop */ } }, { once: true }); }
async function setSource(t, { at = 0, server = false, fresh = false } = {}) {
  const my = ++srcToken, a = P.audio;
  detachHls();
  a._direct = false;
  if (useDirect() && !server) {
    try {
      const d = await directFor(t, fresh);
      if (my !== srcToken) return;
      if (d && d.url) {
        if (d.type === 'progressive' || nativeHls()) {
          a._direct = true; a.src = d.url; seekWhenReady(a, at);
        } else {
          const Hls = await loadHls();
          if (my !== srcToken) return;
          if (!Hls.isSupported()) throw new Error('no MSE');
          hls = new Hls({ startPosition: at > 0 ? at : -1, maxBufferLength: 45, enableWorker: true });
          hls.on(Hls.Events.ERROR, (_, e) => { if (e.fatal) directFailed(t, my); });
          a._direct = true; hls.loadSource(d.url); hls.attachMedia(a);
        }
        a.play().catch(() => {});
        return;
      }
    } catch (_) { if (my !== srcToken) return; }
  }
  // server: local cache → Telegram (getFile) → SoundCloud, in that order
  a.src = t.stream; seekWhenReady(a, at);
  a.play().catch(() => {});
  if (t.status === 'cloud') { t.status = 'queued'; patchTrack(t); }
}
let failedToken = -1;
function directFailed(t, my = srcToken) {
  const a = P.audio, at = a.currentTime || 0;
  if (P.current() !== t || my !== srcToken || failedToken === my) return;   // hls.js + <audio> may both report it
  failedToken = my;
  // first: the signed url may simply have expired → resolve a fresh one; second time: our server
  if (!t._retried) { t._retried = true; return setSource(t, { at, fresh: true }); }
  t._retried = false;
  setSource(t, { at, server: true });
}
// iOS: an <audio> element "blessed" by a tap can later start playback after an await
const SILENCE = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
function unlockAudio() {
  const a = P.audio;
  if (DEMO || !a || a.src || a._unlocked) return;
  a._unlocked = true;
  const mine = () => a.getAttribute('src') === SILENCE;   // a real track may already have been set
  try { a.src = SILENCE; const p = a.play(); p && p.then(() => { if (mine()) { a.pause(); a.removeAttribute('src'); a.load(); } }).catch(() => { if (mine()) a.removeAttribute('src'); }); } catch (_) { /* noop */ }
}

function playQueue(ids, startId, source = '') {
  ids = ids.filter((id) => { const t = T(id); return t && t.status !== 'unavailable'; });
  if (!ids.length) return toast('Этот трек недоступен для прослушивания', 'err');
  P.queue = P.shuffle ? shuffleKeep(ids, startId) : ids;
  P.source = source;
  playIndex(Math.max(0, P.queue.indexOf(+startId)), 'right');
}
function shuffleKeep(ids, first) {
  const rest = ids.filter((x) => x !== +first);
  for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
  return first != null && ids.includes(+first) ? [+first, ...rest] : rest;
}

function playIndex(i, dir = 'right') {
  if (i < 0 || i >= P.queue.length) return;
  const prevT = P.current();
  P.idx = i; P.counted = false;
  const t = P.current();
  t._retried = false;
  setLoading(true);
  if (DEMO || !useDirect()) {
    P.audio.src = t.stream;
    P.audio.play().catch(() => {});
    if (t.status !== 'ready') { t.status = t.status === 'cloud' ? 'queued' : t.status; patchTrack(t); }
  } else setSource(t);
  updateMini();
  if ($('#player').classList.contains('on')) renderPlayer(prevT && prevT.id !== t.id ? dir : null);
  highlightPlaying();
  loadWave(t);
  tintFrom(t.artwork);
  if ('mediaSession' in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artist, album: 'VibeCloud',
      artwork: t.artwork ? [{ src: t.artwork, sizes: '500x500', type: 'image/jpeg' }] : [],
    });
  }
  // warm up the next track: just its CDN url (LITE) or the MP3 on the server
  const n = T(P.queue[i + 1]);
  if (n && useDirect()) directFor(n).catch(() => {});
  else if (n && n.status === 'cloud') API.post(`/api/track/${n.id}/prefetch`).catch(() => {});
}
function toggle(force) {
  if (!P.current()) return;
  const play = force ?? P.audio.paused;
  play ? P.audio.play().catch(() => {}) : P.audio.pause();
  haptic('medium');
}
function next(auto = false) {
  if (P.idx < P.queue.length - 1) return playIndex(P.idx + 1, 'right');
  if (P.repeat === 'all') return playIndex(0, 'right');
  if (auto) { P.audio.pause(); P.audio.currentTime = 0; }
}
function prev() {
  if (P.audio.currentTime > 3) { P.audio.currentTime = 0; return; }
  if (P.idx > 0) playIndex(P.idx - 1, 'left');
}
function setLoading(on) { $$('.play-main').forEach((b) => b.classList.toggle('loading', on && !P.audio.paused)); }
function syncPlayState() {
  $$('.play-main, #mini .play').forEach((b) => { b.innerHTML = P.playing ? I.pause : I.play; b.setAttribute('aria-label', P.playing ? 'Пауза' : 'Играть'); });
  highlightPlaying();
}
function highlightPlaying() {
  const t = P.current();
  $$('.row.playing').forEach((r) => r.classList.remove('playing', 'paused'));
  if (!t) return;
  $$(`.row[data-tid="${t.id}"]`).forEach((r) => { r.classList.add('playing'); r.classList.toggle('paused', !P.playing); });
}
function onTime() {
  const a = P.audio, d = a.duration || (P.current() && P.current().duration) || 0;
  const p = d ? a.currentTime / d : 0;
  $('#mini').style.setProperty('--p', (p * 100).toFixed(2));
  if ($('#player').classList.contains('on')) {
    const cur = $('#player .cur'), tot = $('#player .tot');
    if (cur) cur.textContent = fmt(a.currentTime);
    if (tot) tot.textContent = fmt(d);
    drawWave(p);
  }
  if (!P.counted && a.currentTime > 15) {
    P.counted = true;
    const t = P.current(); if (t) API.post(`/api/track/${t.id}/played`).catch(() => {});
  }
}

function updateMini() {
  const t = P.current(), el = $('#mini');
  if (!t) { el.classList.remove('on'); return; }
  el.innerHTML = `${coverHTML(t)}
    <div class="row-main" data-act="open-player" role="button" tabindex="0"><b>${esc(t.title)}</b><span>${esc(t.artist)}</span></div>
    <button class="ctrl" data-act="next" aria-label="Следующий">${I.next}</button>
    <button class="ctrl play" data-act="toggle" aria-label="${P.playing ? 'Пауза' : 'Играть'}">${P.playing ? I.pause : I.play}</button>
    <i class="line"></i>`;
  if (!el.classList.contains('on')) el.classList.add('on');
}

async function loadWave(t) {
  P.wave = seeded(t.id);
  try { const r = await API.get(`/api/track/${t.id}/waveform?bars=72`); if (r.bars && r.bars.length && P.current() === t) P.wave = r.bars; } catch (_) { /* placeholder */ }
  drawWave(P.audio.duration ? P.audio.currentTime / P.audio.duration : 0);
}
function seeded(id) {
  let x = id % 2147483647 || 7; const out = [];
  for (let i = 0; i < 72; i++) { x = (x * 48271) % 2147483647; out.push(0.25 + 0.75 * Math.abs(Math.sin(i / 5 + (x % 100) / 50)) * ((x % 1000) / 1000 * 0.5 + 0.5)); }
  return out;
}
function drawWave(p = 0) {
  const c = $('#player canvas'); if (!c) return;
  const dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (!w) return;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  const bars = P.wave.length ? P.wave : seeded(1), n = bars.length, step = w / n, gap = 5.2, r = 1.55;
  const rows = Math.floor(h / gap);
  for (let i = 0; i < n; i++) {
    const x = i * step + step / 2;
    const cnt = Math.max(1, Math.round(bars[i] * rows));
    const played = i / n <= p;
    const head = Math.abs(i / n - p) < 1 / n;
    g.fillStyle = head ? '#ffffff' : played ? '#d6f35b' : 'rgba(255,255,255,.26)';
    if (head) { g.shadowColor = '#d6f35b'; g.shadowBlur = 10; } else g.shadowBlur = 0;
    for (let k = 0; k < cnt; k++) {
      const y = h / 2 + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * gap;
      g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
    }
  }
}

function openPlayer() {
  if (!P.current()) return;
  const pl = $('#player');
  pl.classList.remove('closing');
  pl.classList.add('on');
  renderPlayer(null);
  pushCloser(closePlayer);
  if (tg && tg.disableVerticalSwipes) try { tg.disableVerticalSwipes(); } catch (_) { /* old */ }
  haptic('medium');
}
function closePlayer() {
  const pl = $('#player');
  if (!pl.classList.contains('on')) return;
  pl.classList.add('closing');
  setTimeout(() => pl.classList.remove('on', 'closing'), 380);
  if (tg && tg.enableVerticalSwipes) try { tg.enableVerticalSwipes(); } catch (_) { /* old */ }
}

function renderPlayer(dir) {
  const t = P.current(); if (!t) return;
  const pl = $('#player');
  const width = Math.min(innerWidth, 520) - 32;
  const title = shortTitle(t.title, 28);
  const size = fitGiant(title, width, 150, 3);
  const src = P.source ? esc(P.source) : 'Медиатека';
  if (!$('.pl-stage', pl) || !dir) {
    pl.innerHTML = `
      <div class="pl-bg"></div>
      <header class="pl-head">
        <button class="icon-btn" data-act="close-player" aria-label="Свернуть">${I.chev}</button>
        <div class="mid"><small>Сейчас играет</small><b class="src">${src}</b></div>
        <button class="icon-btn" data-act="more-current" aria-label="Действия">${I.more}</button>
      </header>
      <div class="pl-stage">
        <h1 class="pl-giant split" aria-hidden="true"></h1>
        <div class="pl-cover"></div>
      </div>
      <div class="pl-meta"><div class="t"><h2></h2><p></p></div>
        <button class="icon-btn like" data-act="like-current" aria-label="Лайк">${I.heart}</button></div>
      <div class="wave" data-act="seek"><canvas></canvas></div>
      <div class="times"><span class="cur">0:00</span><span class="tot">${fmt(t.duration)}</span></div>
      <div class="controls">
        <button class="c ${P.shuffle ? 'on' : 'dim'}" data-act="shuffle" aria-label="Перемешать">${I.shuffle}</button>
        <button class="c" data-act="prev" aria-label="Предыдущий">${I.prev}</button>
        <button class="c play play-main" data-act="toggle" aria-label="Играть">${P.playing ? I.pause : I.play}</button>
        <button class="c" data-act="next" aria-label="Следующий">${I.next}</button>
        <button class="c ${P.repeat !== 'off' ? 'on' : 'dim'}" data-act="repeat" aria-label="Повтор">${I.repeat}${P.repeat === 'one' ? '<b style="position:absolute;font:900 10px var(--ui);margin:0 0 0 0">1</b>' : ''}</button>
      </div>
      <div class="pl-actions">
        <button data-act="dl-current">${I.down}Скачать</button>
        <button data-act="send-current">${I.send}В чат</button>
        <button data-act="share-current">${I.share}Другу</button>
        <button data-act="mx-current">${I.gift}В микстейп</button>
      </div>`;
    bindWave();
    bindSwipe();
  }
  const giant = $('.pl-giant', pl), cover = $('.pl-cover', pl);
  const apply = () => {
    giant.style.setProperty('--gsize', size + 'px');
    giant.classList.toggle('ru', isRu(title));
    giant.innerHTML = splitLetters(title);
    cover.innerHTML = t.artwork ? `<img src="${esc(t.artwork)}" alt="">` : `<div class="ph" style="font-size:120px">${esc(t.title.charAt(0))}</div>`;
    $('.pl-bg', pl).style.backgroundImage = t.artwork ? `url("${t.artwork}")` : 'none';
    $('.pl-meta h2', pl).textContent = t.title;
    $('.pl-meta p', pl).textContent = t.artist;
    $('.src', pl).textContent = P.source || 'Медиатека';
    $('.tot', pl).textContent = fmt(t.duration);
    $('.like', pl).classList.toggle('on', !!t.liked);
    drawWave(0);
  };
  if (dir && !reduced) {
    cover.className = 'pl-cover swap-out-' + (dir === 'right' ? 'left' : 'right');
    setTimeout(() => { apply(); cover.className = 'pl-cover swap-in-' + dir; }, 300);
  } else apply();
}

function bindWave() {
  const w = $('#player .wave'); let drag = false;
  const seekAt = (e) => {
    const r = w.getBoundingClientRect(), k = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const d = P.audio.duration || (P.current() && P.current().duration) || 0;
    drawWave(k);
    return k * d;
  };
  w.addEventListener('pointerdown', (e) => { drag = true; w.setPointerCapture(e.pointerId); seekAt(e); haptic('select'); });
  w.addEventListener('pointermove', (e) => { if (drag) seekAt(e); });
  w.addEventListener('pointerup', (e) => { if (!drag) return; drag = false; P.audio.currentTime = seekAt(e); haptic('light'); });
}
function bindSwipe() {
  const stage = $('#player .pl-stage'); let x0 = null, y0 = null;
  stage.addEventListener('pointerdown', (e) => { x0 = e.clientX; y0 = e.clientY; });
  stage.addEventListener('pointerup', (e) => {
    if (x0 == null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0; x0 = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) { dx < 0 ? next() : prev(); haptic('medium'); }
    else if (dy > 90) closePlayer();
  });
}

function tintFrom(src) {
  if (!src) return;
  const img = new Image(); img.crossOrigin = 'anonymous';
  img.onload = () => {
    try {
      const c = document.createElement('canvas'); c.width = c.height = 24;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0, 24, 24);
      const d = g.getImageData(0, 0, 24, 24).data; const buckets = [];
      for (let i = 0; i < d.length; i += 4) {
        const r = d[i], gg = d[i + 1], b = d[i + 2], mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
        const sat = mx ? (mx - mn) / mx : 0; if (mx < 40) continue;
        buckets.push({ r, g: gg, b, w: sat * sat + 0.05 });
      }
      buckets.sort((a, b) => b.w - a.w);
      const pick = (k) => { const x = buckets[Math.min(buckets.length - 1, k)] || { r: 90, g: 43, b: 214 }; return `rgb(${x.r},${x.g},${x.b})`; };
      const root = document.documentElement.style;
      root.setProperty('--aura-1', pick(0)); root.setProperty('--aura-2', pick(Math.floor(buckets.length / 6))); root.setProperty('--aura-3', pick(Math.floor(buckets.length / 3)));
    } catch (_) { /* tainted canvas */ }
  };
  img.src = src;
}

/* ------------------------------------------------------------------ detail (playlist / mixtape) */
async function openPlaylist(pid) {
  const d = $('#detail');
  d.innerHTML = '<div style="padding:calc(var(--top) + 80px) 20px"><div class="skeleton" style="height:280px"></div></div>';
  d.classList.add('on'); pushCloser(closeDetail);
  let pl;
  try { pl = await API.get(`/api/playlist/${pid}`); } catch (e) { closeDetail(); return toast(e.message, 'err'); }
  pl.tracks.forEach((t) => upsertTrack(t));
  const size = fitGiant(shortTitle(pl.title, 34), Math.min(innerWidth, 520) - 40, 110, 3);
  d.innerHTML = `
    <section class="d-hero">
      <div class="bgimg" style="background-image:url('${esc(pl.artwork || '')}')"></div>
      <div class="pl-head"><button class="icon-btn" data-act="close-detail" aria-label="Назад">${I.back}</button><span style="flex:1"></span>
        ${pl.kind !== 'mixtape' ? `<button class="icon-btn" data-act="sync-pl" data-pid="${esc(pl.id)}" aria-label="Обновить">${I.sync}</button>` : ''}
        <button class="icon-btn" data-act="del-pl" data-pid="${esc(pl.id)}" aria-label="Удалить">${I.trash}</button></div>
      <div class="eyebrow" style="margin-top:auto;color:var(--lime)">${{ album: 'Альбом', likes: 'Лайки', user_tracks: 'Треки артиста', mixtape: 'Микстейп' }[pl.kind] || 'Плейлист'}</div>
      <h1 class="split${isRu(pl.title) ? ' ru' : ''}" style="--gsize:${size}px;margin-top:8px">${splitLetters(shortTitle(pl.title, 34))}</h1>
      <div class="sub">${esc(pl.artist || '')} · ${tracksWord(pl.track_count)} · ${fmtLong(pl.duration)}</div>
    </section>
    <div class="d-actions">
      <button class="btn btn-lime" data-act="play-pl" data-pid="${esc(pl.id)}">${I.play}Слушать</button>
      <button class="icon-btn" data-act="shuffle-pl" data-pid="${esc(pl.id)}" aria-label="Перемешать">${I.shuffle}</button>
      <button class="icon-btn" data-act="send-pl" data-pid="${esc(pl.id)}" aria-label="Скачать всё в чат">${I.down}</button>
      ${pl.kind !== 'mixtape' ? `<button class="icon-btn${pl.watch ? ' on' : ''}" data-act="watch-pl" data-pid="${esc(pl.id)}" aria-label="Следить за обновлениями">${I.bell}</button>` : ''}
      <button class="icon-btn" data-act="mx-from-pl" data-pid="${esc(pl.id)}" aria-label="Сделать микстейп">${I.gift}</button>
    </div>
    <div class="d-list"><div class="list" data-queue="pl:${esc(pl.id)}" data-source="${esc(pl.title)}">
      ${pl.tracks.map((t, i) => rowHTML(T(t.id), { idx: i + 1, arrive: true, i: Math.min(i, 10) })).join('')}</div></div>`;
  d.scrollTop = 0;
  highlightPlaying();
}
function closeDetail() {
  const d = $('#detail'); if (!d.classList.contains('on')) return;
  d.classList.remove('on'); d.innerHTML = '';
}

async function openMixtape(code) {
  const d = $('#detail');
  d.innerHTML = '<div style="padding:calc(var(--top) + 80px) 20px"><div class="skeleton" style="height:320px"></div></div>';
  d.classList.add('on'); pushCloser(closeDetail);
  let m;
  try { m = await API.get(`/api/mixtape/${code}`); } catch (e) { closeDetail(); return toast('Микстейп не найден', 'err'); }
  m.tracks.forEach((t) => { if (!S.tracks.has(t.id)) S.tracks.set(t.id, t); });
  const size = fitGiant(m.title, Math.min(innerWidth, 520) - 76, 120, 3, 'Fira Sans Extra Condensed');
  d.innerHTML = `
    <div style="padding:calc(var(--top) + 8px) var(--gutter) 0">
      <div class="pl-head"><button class="icon-btn" data-act="close-detail" aria-label="Назад">${I.back}</button><span style="flex:1"></span>
        ${m.mine ? `<button class="icon-btn" data-act="del-mx" data-code="${esc(m.code)}" aria-label="Удалить">${I.trash}</button>` : ''}</div>
      <article class="mx t-${esc(m.theme)}" style="min-height:420px;margin-top:10px">
        <div class="mx-meta"><span>Mixtape<br>by ${esc(m.owner || 'друг')}</span><span>${tracksWord(m.track_count)}<br>${fmtLong(m.duration)}</span></div>
        <h2 class="mx-title split" style="font-size:${size}px">${splitLetters(m.title)}</h2>
        ${m.note ? `<p style="margin:12px 0 0;font-weight:800;font-size:15px">«${esc(m.note)}»</p>` : ''}
        <div class="mx-foot"><div class="stack">${(m.covers || []).map((c) => `<img src="${esc(c)}" alt="">`).join('')}</div></div>
      </article>
    </div>
    <div class="d-actions" style="margin-top:14px">
      ${m.mine ? `<button class="btn btn-lime" data-act="share-mx" data-code="${esc(m.code)}">${I.send}Отправить другу</button>`
        : `<button class="btn btn-lime" data-act="claim-mx" data-code="${esc(m.code)}">${I.plus}Забрать себе</button>`}
      <button class="icon-btn" data-act="play-mx" aria-label="Слушать">${I.play}</button>
    </div>
    <div class="d-list"><div class="list" data-queue="mx:${esc(m.code)}" data-source="${esc(m.title)}">
      ${m.tracks.map((t, i) => rowHTML(T(t.id), { idx: i + 1, arrive: true, i: Math.min(i, 10) })).join('')}</div></div>`;
  d.scrollTop = 0;
}

/* ------------------------------------------------------------------ sheets */
function openSheet(html, onMount) {
  const s = $('#sheet');
  s.innerHTML = `<div class="scrim" data-act="close-sheet"></div><div class="panel" role="dialog" aria-modal="true"><div class="handle"></div>${html}</div>`;
  s.className = 'on';
  pushCloser(closeSheet);
  if (onMount) onMount($('.panel', s));
}
function closeSheet() {
  const s = $('#sheet'); if (!s.classList.contains('on')) return;
  s.classList.add('closing');
  setTimeout(() => { s.className = ''; s.innerHTML = ''; }, 260);
}

function trackSheet(t) {
  const inLib = S.order.includes(t.id);
  openSheet(`
    <div class="sheet-head">${coverHTML(t)}<div class="row-main"><b>${esc(t.title)}</b><span>${esc(t.artist)} · ${fmt(t.duration)}${t.genre ? ' · ' + esc(t.genre) : ''}</span></div></div>
    <button class="act" data-act="s-dl" data-tid="${t.id}">${I.down}Скачать MP3<span class="hint">на устройство</span></button>
    <button class="act" data-act="s-send" data-tid="${t.id}">${I.send}Отправить в чат<span class="hint">аудиофайлом</span></button>
    <button class="act" data-act="s-share" data-tid="${t.id}">${I.share}Поделиться с другом</button>
    <button class="act" data-act="s-mx" data-tid="${t.id}">${I.gift}Добавить в микстейп</button>
    <button class="act" data-act="s-like" data-tid="${t.id}">${I.heart}${t.liked ? 'Убрать из лайков' : 'В лайки'}</button>
    ${t.permalink ? `<button class="act" data-act="s-sc" data-tid="${t.id}">${I.link}Открыть в SoundCloud</button>` : ''}
    ${inLib ? `<button class="act danger" data-act="s-del" data-tid="${t.id}">${I.trash}Убрать из медиатеки</button>` : ''}`);
}

function mixtapeSheet(preselect = []) {
  const sel = new Set(preselect.map(Number));
  let theme = 'cobalt';
  const list = S.order.map(T).filter((t) => t && t.status !== 'unavailable').sort((a, b) => sel.has(b.id) - sel.has(a.id));
  openSheet(`
    <div class="sheet-head" style="border:0;padding-bottom:0"><h3>Новый<br>микстейп</h3></div>
    <label class="label" for="mx-title">Название</label>
    <input class="input" id="mx-title" maxlength="40" placeholder="Например: Для Лёши" autocomplete="off">
    <label class="label">Постер</label>
    <div class="theme-pick">${Object.entries(THEME_LABEL).map(([k, v]) => `<button type="button" class="t-${k}${k === theme ? ' on' : ''}" data-theme="${k}" aria-label="${v}">Aa</button>`).join('')}</div>
    <label class="label" for="mx-note">Записка другу <span style="color:var(--faint)">— по желанию</span></label>
    <input class="input" id="mx-note" maxlength="140" placeholder="Это под пробежку 🏃" autocomplete="off">
    <label class="label">Треки · <span id="mx-count">${sel.size}</span></label>
    <div id="mx-picks">${list.map((t) => `<div class="pick${sel.has(t.id) ? ' on' : ''}" data-pick="${t.id}" role="checkbox" aria-checked="${sel.has(t.id)}" tabindex="0">
      ${coverHTML(t)}<div class="row-main"><b>${esc(t.title)}</b><span>${esc(t.artist)}</span></div><span class="check">${I.check}</span></div>`).join('')}</div>
    <div class="sticky-foot"><button class="btn btn-lime btn-block" style="min-height:56px" id="mx-create">${I.send}Создать и отправить</button></div>`,
  (panel) => {
    panel.addEventListener('click', async (e) => {
      const th = e.target.closest('[data-theme]');
      if (th) { theme = th.dataset.theme; $$('[data-theme]', panel).forEach((b) => b.classList.toggle('on', b === th)); haptic('select'); return; }
      const pk = e.target.closest('[data-pick]');
      if (pk) {
        const id = +pk.dataset.pick; sel.has(id) ? sel.delete(id) : sel.add(id);
        pk.classList.toggle('on', sel.has(id)); pk.setAttribute('aria-checked', sel.has(id));
        $('#mx-count').textContent = sel.size; haptic('select'); return;
      }
      if (e.target.closest('#mx-create')) {
        const title = $('#mx-title').value.trim() || 'Микстейп';
        if (!sel.size) return toast('Выбери хотя бы один трек', 'err');
        const btn = $('#mx-create'); btn.disabled = true;
        try {
          const m = await API.post('/api/mixtapes', { title, theme, note: $('#mx-note').value.trim(), track_ids: [...sel] });
          S.mixtapes.unshift(m); closeSheet(); haptic('success');
          go('mixtapes', { force: true });
          setTimeout(() => shareMixtape(m.code), 450);
        } catch (err) { toast(err.message, 'err'); btn.disabled = false; }
      }
    });
  });
}

function profileSheet(p) {
  openSheet(`
    <div class="sheet-head">${coverHTML({ title: p.title, artwork: p.artwork })}<div class="row-main"><b>${esc(p.title)}</b><span>Профиль SoundCloud</span></div></div>
    <p style="margin:6px 0 12px;color:var(--mute)">Что перенести в VibeCloud?</p>
    <div class="preview-actions" style="padding:0">${p.options.filter((o) => o.count).map((o) => `
      <button class="opt" data-act="import-mode" data-mode="${o.mode}" data-url="${esc(p.url)}">${{ likes: I.heart, user_tracks: I.lib, user_sets: I.move }[o.mode]}${esc(o.label)}<span class="n">${o.count}</span></button>`).join('')
      || '<div class="empty">Профиль пустой</div>'}</div>`);
}

/* ------------------------------------------------------------------ actions */
async function startImport(raw, mode) {
  const found = linkKind(raw);
  if (!found) return toast('Вставь ссылку soundcloud.com', 'err');
  haptic('medium');
  try {
    if (!mode && found.kind === 'user') {
      const p = await API.post('/api/import/inspect', { url: raw });
      if (p.kind === 'user') return profileSheet(p);
    }
    await API.post('/api/import', { url: raw, mode });
    toast('Переношу — треки уже летят ✦');
  } catch (e) { toast(e.message, 'err'); }
}

async function readClipboard() {
  if (tg && tg.readTextFromClipboard && tgv('6.4')) {
    const txt = await new Promise((res) => { try { tg.readTextFromClipboard((t) => res(t || '')); } catch (_) { res(''); } setTimeout(() => res(''), 1500); });
    if (txt) return txt;
  }
  try { return await navigator.clipboard.readText(); } catch (_) { return ''; }
}

async function downloadTrack(t) {
  if (t.status === 'unavailable') return toast('Этот трек нельзя скачать', 'err');
  if (LITE()) return sendTrack(t, t.status === 'ready' ? 'MP3 уже в чате с ботом — сохраняй оттуда' : 'Готовлю MP3 — пришлю в чат с ботом');
  if (tg && tg.downloadFile && tgv('8.0')) {
    tg.downloadFile({ url: new URL(t.file, location.href).href, file_name: `${t.artist} - ${t.title}.mp3`.replace(/[\\/:*?"<>|]+/g, ' ') }, (ok) => ok && haptic('success'));
    return;
  }
  if (DEMO) return toast('В Telegram файл сохранится на устройство');
  await sendTrack(t);
}
async function sendTrack(t, msg = 'MP3 летит в чат с ботом') {
  try { await API.post(`/api/track/${t.id}/send`); toast(msg); haptic('success'); }
  catch (e) { toast(e.message, 'err'); }
}
async function shareTrack(t) {
  toast('Готовлю трек…');
  try {
    const r = await API.post(`/api/track/${t.id}/share`);
    if (r.prepared_id && tg && tg.shareMessage && tgv('8.0')) return tg.shareMessage(r.prepared_id, (ok) => ok && toast('Отправлено ✦'));
    if (tg && tg.switchInlineQuery && tgv('6.7')) return tg.switchInlineQuery(r.inline_query, ['users', 'groups', 'channels']);
    copy(r.link);
  } catch (e) { toast(e.message, 'err'); }
}
async function shareMixtape(code) {
  try {
    const r = await API.post(`/api/mixtape/${code}/share`);
    if (r.prepared_id && tg && tg.shareMessage && tgv('8.0')) return tg.shareMessage(r.prepared_id, (ok) => ok && toast('Микстейп отправлен 🎁'));
    const url = `https://t.me/share/url?url=${encodeURIComponent(r.link)}&text=${encodeURIComponent(r.text)}`;
    if (tg && tg.openTelegramLink && !DEMO) return tg.openTelegramLink(url);
    copy(r.link);
  } catch (e) { toast(e.message, 'err'); }
}
function copy(text) {
  if (!navigator.clipboard) return toast('Ссылка: ' + text);
  navigator.clipboard.writeText(text).then(() => toast('Ссылка скопирована'), () => toast('Ссылка: ' + text));
}
async function toggleLike(t) {
  t.liked = !t.liked; haptic(t.liked ? 'success' : 'light');
  $$('#player .like').forEach((b) => b.classList.toggle('on', t.liked));
  try { await API.post(`/api/track/${t.id}/like`, { liked: t.liked }); } catch (e) { t.liked = !t.liked; toast(e.message, 'err'); }
  if (S.view === 'library') renderLibrary({ keepSearch: true });
}
async function playPlaylist(pid, shuffle = false) {
  let pl;
  try { pl = await API.get(`/api/playlist/${pid}`); } catch (e) { return toast(e.message, 'err'); }
  pl.tracks.forEach((t) => upsertTrack(t));
  const ids = pl.tracks.map((t) => t.id);
  P.shuffle = shuffle || P.shuffle;
  playQueue(ids, shuffle ? ids[Math.floor(Math.random() * ids.length)] : ids[0], pl.title);
}

function queueFrom(el) {
  const list = el.closest('[data-queue]');
  const ids = list ? $$('[data-tid]', list).map((x) => +x.dataset.tid) : [+el.dataset.tid];
  const src = list && list.dataset.source ? list.dataset.source : list && list.dataset.queue === 'recent' ? 'Недавно перенесено' : 'Медиатека';
  return { ids, src };
}

const ACTIONS = {
  'go-library': () => go('library'), 'go-import': () => go('import'), 'go-mixtapes': () => go('mixtapes'),
  'lib-playlists': () => { S.filter = 'playlists'; go('library'); },
  search: () => { go('library'); setTimeout(() => $('#lib-q') && $('#lib-q').focus(), 80); },
  nav: (el) => go(el.dataset.view),
  filter: (el) => { S.filter = el.dataset.f; renderLibrary({ keepSearch: true }); haptic('select'); },
  paste: async () => { const t = await readClipboard(); if (!t) return toast('Буфер пуст — вставь ссылку вручную', 'err'); $('#q-url').value = t; startImport(t); },
  'paste-imp': async () => { const t = await readClipboard(); if (!t) return toast('Буфер пуст', 'err'); const ta = $('#imp-url'); ta.value = t; ta.dispatchEvent(new Event('input')); },
  play: (el) => {
    const t = T(el.dataset.tid); if (!t) return;
    if (P.current() && P.current().id === t.id) return openPlayer();
    const { ids, src } = queueFrom(el); playQueue(ids, t.id, src); haptic('light');
  },
  'play-remote': (el) => ACTIONS['add-remote'](el, true),
  'add-remote': async (el, andPlay = false) => {
    const t = S.remote.find((x) => x.id === +el.closest('[data-tid]').dataset.tid); if (!t) return;
    try {
      const r = await API.post(`/api/track/${t.id}/add`); t.in_library = true; upsertTrack({ ...r, _new: true }, { prepend: true });
      haptic('success'); toast(`«${t.title}» в медиатеке`);
      const b = $('.round', el.closest('.row')); if (b) b.innerHTML = I.check;
      if (andPlay) playQueue([t.id], t.id, 'Поиск');
    } catch (e) { toast(e.message, 'err'); }
  },
  more: (el) => { const t = T(el.closest('[data-tid]').dataset.tid); if (t) trackSheet(t); },
  'more-current': () => P.current() && trackSheet(P.current()),
  'open-player': openPlayer, 'close-player': closePlayer,
  toggle: () => toggle(), next: () => next(), prev: () => prev(),
  shuffle: (el) => { P.shuffle = !P.shuffle; el.classList.toggle('on', P.shuffle); el.classList.toggle('dim', !P.shuffle); haptic('select');
    if (P.shuffle && P.current()) { const cur = P.current().id; P.queue = shuffleKeep(P.queue, cur); P.idx = 0; } },
  repeat: () => { P.repeat = { off: 'all', all: 'one', one: 'off' }[P.repeat]; haptic('select'); renderPlayer(null); toast({ off: 'Повтор выключен', all: 'Повтор очереди', one: 'Повтор трека' }[P.repeat]); },
  'like-current': () => P.current() && toggleLike(P.current()),
  'dl-current': () => P.current() && downloadTrack(P.current()),
  'send-current': () => P.current() && sendTrack(P.current()),
  'share-current': () => P.current() && shareTrack(P.current()),
  'mx-current': () => P.current() && mixtapeSheet([P.current().id]),
  'open-pl': (el) => openPlaylist(el.closest('[data-pid]').dataset.pid),
  'play-pl': (el) => playPlaylist(el.closest('[data-pid]').dataset.pid),
  'shuffle-pl': (el) => playPlaylist(el.dataset.pid, true),
  'send-pl': async (el) => { try { const r = await API.post(`/api/playlist/${el.dataset.pid}/send`); toast(`Отправляю ${tracksWord(r.count)} в чат`); haptic('success'); } catch (e) { toast(e.message, 'err'); } },
  'watch-pl': async (el) => {
    const on = !el.classList.contains('on'); el.classList.toggle('on', on); haptic(on ? 'success' : 'light');
    try { await API.post(`/api/playlist/${el.dataset.pid}/watch`, { watch: on }); const p = S.playlists.find((x) => x.id === el.dataset.pid); if (p) p.watch = on;
      toast(on ? 'Слежу: новые треки появятся сами' : 'Больше не слежу'); } catch (e) { toast(e.message, 'err'); }
  },
  'sync-pl': async (el) => { toast('Проверяю SoundCloud…'); try { const r = await API.post(`/api/playlist/${el.dataset.pid}/sync`); toast(r.new ? `Новых треков: ${r.new}` : 'Новых треков нет'); if (r.new) openPlaylist(el.dataset.pid); } catch (e) { toast(e.message, 'err'); } },
  'del-pl': async (el) => {
    const go2 = async () => { try { await API.del(`/api/playlist/${el.dataset.pid}`); S.playlists = S.playlists.filter((p) => p.id !== el.dataset.pid); closeDetail(); render(); toast('Плейлист удалён, треки остались'); } catch (e) { toast(e.message, 'err'); } };
    if (tg && tg.showConfirm && tgv('6.2')) tg.showConfirm('Удалить плейлист? Треки останутся в медиатеке.', (ok) => ok && go2()); else go2();
  },
  'mx-from-pl': (el) => { const ids = $$('#detail [data-tid]').map((x) => +x.dataset.tid); mixtapeSheet(ids); },
  'new-mx': () => mixtapeSheet(),
  'open-mx': (el) => openMixtape(el.closest('[data-code]').dataset.code),
  'share-mx': (el) => shareMixtape(el.closest('[data-code]').dataset.code),
  'claim-mx': async (el) => {
    el.disabled = true;
    try { const r = await API.post(`/api/mixtape/${el.dataset.code}/claim`); haptic('success'); toast(`🎁 ${tracksWord(r.count)} теперь твои`); el.innerHTML = `${I.check}В медиатеке`; }
    catch (e) { toast(e.message, 'err'); el.disabled = false; }
  },
  'play-mx': () => { const ids = $$('#detail [data-tid]').map((x) => +x.dataset.tid); playQueue(ids, ids[0], $('#detail [data-source]').dataset.source); },
  'del-mx': async (el) => { try { await API.del(`/api/mixtape/${el.dataset.code}`); S.mixtapes = S.mixtapes.filter((m) => m.code !== el.dataset.code); closeDetail(); renderMixtapes(); } catch (e) { toast(e.message, 'err'); } },
  'import-mode': (el) => { closeSheet(); startImport(el.dataset.url, el.dataset.mode); },
  'close-sheet': () => popCloser(), 'close-detail': () => popCloser(),
  's-dl': (el) => { closeSheet(); downloadTrack(T(el.dataset.tid)); },
  's-send': (el) => { closeSheet(); sendTrack(T(el.dataset.tid)); },
  's-share': (el) => { closeSheet(); shareTrack(T(el.dataset.tid)); },
  's-mx': (el) => { closeSheet(); setTimeout(() => mixtapeSheet([+el.dataset.tid]), 280); },
  's-like': (el) => { closeSheet(); toggleLike(T(el.dataset.tid)); },
  's-sc': (el) => { const t = T(el.dataset.tid); closeSheet(); tg && tg.openLink ? tg.openLink(t.permalink) : open(t.permalink, '_blank'); },
  's-del': async (el) => {
    const id = +el.dataset.tid; closeSheet();
    try { await API.del(`/api/track/${id}`); S.order = S.order.filter((x) => x !== id); render(); toast('Убрал из медиатеки'); } catch (e) { toast(e.message, 'err'); }
  },
  seek: () => {},
};

/* closers stack: Telegram BackButton closes the top layer */
function pushCloser(fn) { S.closers = S.closers.filter((f) => f !== fn); S.closers.push(fn); syncBack(); }
function popCloser() { const fn = S.closers.pop(); if (fn) fn(); syncBack(); }
function syncBack() {
  S.closers = S.closers.filter((fn) => (fn === closePlayer ? $('#player').classList.contains('on') : fn === closeDetail ? $('#detail').classList.contains('on') : fn === closeSheet ? $('#sheet').classList.contains('on') : false) || fn === S.closers[S.closers.length - 1]);
  if (!tg || !tg.BackButton) return;
  S.closers.length ? tg.BackButton.show() : tg.BackButton.hide();
}

/* ------------------------------------------------------------------ live events */
function onEvent(name, data) {
  if (name === 'track_added') {
    const t = { ...data.track, _new: true };
    const isNew = upsertTrack(t, { prepend: true });
    if (!isNew) { const ex = T(t.id); ex._new = true; S.order = [t.id, ...S.order.filter((x) => x !== t.id)]; }
    if (data.job && S.transit.has(data.job)) { const j = S.transit.get(data.job); if (!j.ids.includes(t.id)) j.ids.push(t.id); }
    if (S.stats && isNew) { S.stats.tracks++; S.stats.hours = +(S.stats.hours + t.duration / 3600).toFixed(1); }
    scheduleTransitAndLists();
  } else if (name === 'track_status') {
    const t = T(data.id); if (!t) return;
    Object.assign(t, data.status ? { status: data.status } : {}, data.progress != null ? { progress: data.progress } : {}, data.in_telegram ? { in_telegram: true } : {});
    patchTrack(t); refreshTransit();
    if (P.current() && P.current().id === t.id && t.status === 'ready' && P.audio.paused && P.audio.error && !P.audio._direct) { P.audio.src = t.stream; P.audio.play().catch(() => {}); }
  } else if (name === 'job') {
    const j = S.transit.get(data.id) || { ids: [] };
    S.transit.set(data.id, { ...j, ...data, ids: j.ids });
    if (data.status === 'done') {
      haptic('success');
      toast(data.kind === 'track' ? `«${data.title}» в медиатеке` : `✓ «${data.title}» — ${tracksWord(data.total)}`);
      if (data.playlist) { S.playlists = [data.playlist, ...S.playlists.filter((p) => p.id !== data.playlist.id)]; if (S.stats) S.stats.playlists = S.playlists.length; }
      setTimeout(() => maybeRetire(data.id), 4000);
    }
    if (data.status === 'error') { toast(data.error || 'Не получилось', 'err'); S.transit.delete(data.id); }
    scheduleTransitAndLists();
  } else if (name === 'playlist') {
    S.playlists = [data, ...S.playlists.filter((p) => p.id !== data.id)]; scheduleTransitAndLists();
  } else if (name === 'need_choice') {
    profileSheet(data);
  } else if (name === 'import_error') {
    toast(data.error || 'Ошибка переноса', 'err');
  } else if (name === 'toast') toast(data.text);
}
function maybeRetire(jid) {
  const j = S.transit.get(jid); if (!j) return;
  const busy = j.ids.map(T).some((t) => t && (t.status === 'downloading' || t.status === 'queued'));
  if (busy) return setTimeout(() => maybeRetire(jid), 3000);
  S.transit.delete(jid); refreshTransit(true);
}
let transitTimer;
function refreshTransit(force = false) {
  clearTimeout(transitTimer);
  transitTimer = setTimeout(() => {
    const html = transitHTML();
    ['#transit-home', '#transit-import'].forEach((sel) => { const el = $(sel); if (el) { el.innerHTML = html; el.style.display = html ? '' : 'none'; } });
    const badge = $('#nav .badge'); const on = S.transit.size > 0;
    if (on && !badge) $$('#nav button')[2].insertAdjacentHTML('beforeend', '<span class="badge"></span>'); else if (!on && badge) badge.remove();
  }, force ? 0 : 120);
}
let listTimer;
function scheduleTransitAndLists() {
  refreshTransit();
  clearTimeout(listTimer);
  listTimer = setTimeout(() => {
    if (S.view === 'home') refreshHome();
    else if (S.view === 'library') renderLibrary({ keepSearch: true });
  }, 160);
}

function connectEvents() {
  if (DEMO) { window.VCDemo.subscribe(onEvent); return; }
  const url = `/api/events?auth=${encodeURIComponent(tg.initData)}`;
  let es, retry = 1000;
  const open = () => {
    es = new EventSource(url);
    ['track_added', 'track_status', 'job', 'playlist', 'need_choice', 'import_error', 'toast'].forEach((n) =>
      es.addEventListener(n, (e) => { try { onEvent(n, JSON.parse(e.data)); } catch (_) { /* bad frame */ } }));
    es.addEventListener('hello', () => { retry = 1000; });
    es.onerror = () => { es.close(); setTimeout(open, retry); retry = Math.min(retry * 2, 15000); };
  };
  open();
}

/* ------------------------------------------------------------------ boot */
function bindGlobal() {
  document.addEventListener('pointerdown', unlockAudio, { once: true, capture: true });
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]'); if (!el) return;
    const inner = e.target.closest('button[data-act]');
    const target = inner && el.contains(inner) ? inner : el;
    const fn = ACTIONS[target.dataset.act];
    if (fn) { e.preventDefault(); e.stopPropagation(); fn(target, e); }
  });
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[role="button"][data-act]')) { e.preventDefault(); e.target.click(); }
    if (e.key === 'Escape') popCloser();
  });
  document.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = e.target, v = (f.url && f.url.value) || '';
    startImport(v); if (f.url) f.url.value = '';
    const det = $('#detected'); if (det) { det.textContent = 'Жду ссылку…'; det.className = 'detected'; }
  });
  let searchTimer;
  document.addEventListener('input', (e) => {
    if (e.target.id === 'lib-q') {
      S.q = e.target.value; renderLibrary({ keepSearch: true });
      clearTimeout(searchTimer);
      const q = S.q.trim();
      if (linkKind(q)) return;
      if (q.length >= 2) searchTimer = setTimeout(async () => {
        try { const r = await API.get(`/api/search?q=${encodeURIComponent(q)}`); if (S.q.trim() !== q) return; S.remote = r.results; S.remoteQ = S.q; r.results.forEach((t) => { if (!S.tracks.has(t.id)) S.tracks.set(t.id, t); }); renderLibrary({ keepSearch: true }); } catch (_) { /* offline */ }
      }, 420);
    }
    if (e.target.id === 'imp-url') {
      const k = linkKind(e.target.value), det = $('#detected');
      const n = (e.target.value.match(new RegExp(LINK_RE.source, 'gi')) || []).length;
      det.textContent = k ? (n > 1 ? `${n} ссылки — перенесу все` : k.label) : e.target.value ? 'Это не ссылка SoundCloud' : 'Жду ссылку…';
      det.className = 'detected' + (k ? ' ok' : '');
    }
  });
  document.addEventListener('focusin', (e) => { if (e.target.id === 'imp-url') e.target.closest('.field').classList.add('focus'); });
  document.addEventListener('focusout', (e) => { if (e.target.id === 'imp-url') e.target.closest('.field').classList.remove('focus'); });
  $('#nav').addEventListener('click', (e) => { const b = e.target.closest('button[data-view]'); if (b) go(b.dataset.view); });
  addEventListener('resize', () => { const b = $$('#nav button')[VIEWS.indexOf(S.view)]; $('#nav .blob').style.transform = `translateX(${b.offsetLeft - 6}px)`; });
}

function buildChrome() {
  $('#nav').innerHTML = `<span class="blob"></span>
    <button data-view="home" class="on" aria-label="Главная">${I.home}</button>
    <button data-view="library" aria-label="Медиатека">${I.lib}</button>
    <button data-view="import" aria-label="Перенос">${I.move}</button>
    <button data-view="mixtapes" aria-label="Микстейпы">${I.gift}</button>`;
}

function intro() {
  const el = $('#intro');
  let seen = false;
  try { seen = !!sessionStorage.getItem('vc-intro'); sessionStorage.setItem('vc-intro', '1'); } catch (_) { /* sandboxed */ }
  if (!el) return;
  if (reduced || seen) { el.remove(); return; }
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 600); }, 1350);
}

function routeStart() {
  const sp = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || '';
  const open = params.get('open') || sp.replace('_', ':');
  if (!open) return;
  const [kind, id] = open.split(':');
  if (kind === 'tr' && id) {
    const t = T(id);
    if (t) { playQueue(S.order, t.id, 'Медиатека'); setTimeout(openPlayer, 200); }
    else API.post(`/api/track/${id}/add`).then((r) => { upsertTrack({ ...r, _new: true }, { prepend: true }); render(); playQueue([r.id], r.id, 'От друга'); setTimeout(openPlayer, 200); toast('Трек от друга сохранён ✦'); }).catch(() => {});
  } else if (kind === 'mx' && id) openMixtape(id);
  else if (kind === 'pl' && id) openPlaylist(id);
  else if (VIEWS.includes(kind)) go(kind);
}

async function boot() {
  if (tg) {
    try {
      tg.ready(); tg.expand();
      if (tgv('6.1')) { tg.setHeaderColor('#09090c'); tg.setBackgroundColor('#09090c'); }
      if (tgv('7.10')) tg.setBottomBarColor('#09090c');
      tg.BackButton && tg.BackButton.onClick(popCloser);
    } catch (_) { /* outside Telegram */ }
  }
  buildChrome();
  bindGlobal();
  initAudio();
  intro();
  const fontsReady = document.fonts ? Promise.race([Promise.all([
    document.fonts.load('400 100px Anton', 'VIBE'), document.fonts.load('900 100px "Fira Sans Extra Condensed"', 'ВАЙБ'),
    document.fonts.load('800 100px "Fira Sans Extra Condensed"', 'ВАЙБ'), document.fonts.load('900 40px Doto', '0123')]), sleep(1500)]).catch(() => {}) : null;
  try {
    const d = await API.get(`/api/bootstrap?tz=${-new Date().getTimezoneOffset()}`);
    S.user = { ...(d.user || {}), photo_url: (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url) || (d.user && d.user.photo_url) };
    S.stats = d.stats; S.config = d.config || {};
    d.tracks.forEach((t) => upsertTrack(t));
    S.playlists = d.playlists; S.mixtapes = d.mixtapes;
    (d.jobs || []).filter((j) => j.status === 'running').forEach((j) => S.transit.set(j.id, { ...j, ids: [] }));
  } catch (e) {
    toast(e.message || 'Нет связи с сервером', 'err');
  }
  await fontsReady;
  go(params.get('view') || 'home', { force: true });
  connectEvents();
  routeStart();
}

window.VC = { S, P, go, openPlayer, openPlaylist, openMixtape, mixtapeSheet, trackSheet, startImport, onEvent };
document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', boot) : boot();
})();
