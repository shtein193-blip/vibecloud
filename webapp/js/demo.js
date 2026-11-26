/* VibeCloud — offline demo backend.
   Used automatically when the Mini App is opened outside Telegram (no initData) or with ?demo.
   Every track, artist and playlist here is fictional. */
(() => {
'use strict';
const BASE = (window.VC_ASSET_BASE || 'img/demo/');
const C = (n) => (/[/:]/.test(n) ? n : (window.VC_ASSETS && window.VC_ASSETS[n]) || `${BASE}${n}.jpg`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Math.floor(Date.now() / 1000);
let seq = 9000;

const RAW = [
  // id, title, artist, cover, sec, genre, status, liked
  [101, 'Motion Blur', 'Vanta Club', 'blur', 214, 'Electronic', 'ready', 1],
  [102, 'Бетон и дождь', 'Гранит', 'tower', 238, 'Post-Punk', 'ready', 1],
  [103, 'Smoke Signals', 'Ash & Echo', 'smoke', 251, 'Ambient', 'ready', 0],
  [104, 'Night Drive 404', 'Highway Ghosts', 'trails', 199, 'Synthwave', 'ready', 1],
  [105, "Runner's High", 'Pulse Theory', 'runner', 186, 'House', 'ready', 0],
  [106, 'Северное сияние', 'Полярный', 'aurora', 263, 'Indie', 'ready', 1],
  [107, 'Кобальт', 'Мира Север', 'cobalt', 177, 'Dream Pop', 'ready', 0],
  [108, 'Hollow Tower', 'Brutal Garden', 'tower-cobalt', 242, 'Post-Punk', 'cloud', 0],
  [109, 'Lime Exhaust', 'Ash & Echo', 'smoke-lime', 205, 'Techno', 'ready', 0],
  [110, 'Afterglow', 'Lumen Haze', 'aurora-pink', 221, 'Indie', 'cloud', 1],
  [111, 'Red Shift', 'Highway Ghosts', 'trails-red', 192, 'Synthwave', 'ready', 0],
  [112, 'Пульс', 'Pulse Theory', 'runner-red', 174, 'House', 'cloud', 0],
  [113, 'Пепел', 'Мира Север', 'ash', 230, 'Dream Pop', 'ready', 0],
  [114, 'Concrete Bloom', 'Гранит', 'tower-crop', 248, 'Post-Punk', 'cloud', 0],
  [115, 'Blue Hour', 'Lumen Haze', 'smoke-blue', 266, 'Ambient', 'ready', 1],
  [116, 'Overpass', 'Highway Ghosts', 'trails-violet', 201, 'Synthwave', 'unavailable', 0],
  [117, 'Static Heart', 'Dial Tone', 'aurora-violet', 189, 'Electronic', 'cloud', 0],
  [118, 'Velocity', 'Pulse Theory', 'runner-teal', 183, 'Drum & Bass', 'ready', 0],
];
// tracks that are "on SoundCloud" but not imported yet (search results & demo imports)
const POOL_RAW = [
  [201, 'Glass Pills', 'Vanta Club', 'cobalt', 208, 'Electronic'],
  [202, 'Ночной трамвай', 'Гранит', 'tower', 233, 'Post-Punk'],
  [203, 'Low Light', 'Lumen Haze', 'aurora', 247, 'Indie'],
  [204, 'Tunnel Vision', 'Highway Ghosts', 'trails', 195, 'Synthwave'],
  [205, 'Дым над городом', 'Ash & Echo', 'smoke', 259, 'Ambient'],
  [206, 'Second Wind', 'Pulse Theory', 'runner', 181, 'House'],
  [207, 'Paper Stars', 'Мира Север', 'blur', 202, 'Dream Pop'],
  [208, 'Brutal Garden', 'Brutal Garden', 'tower-crop', 244, 'Post-Punk'],
  [209, 'Aurora Loop', 'Полярный', 'aurora-pink', 276, 'Indie'],
  [210, 'Green Wave', 'Ash & Echo', 'smoke-lime', 214, 'Techno'],
  [211, 'Cold Engine', 'Dial Tone', 'trails-red', 187, 'Electronic'],
  [212, 'Сигнал', 'Мира Север', 'ash', 199, 'Dream Pop'],
  [213, 'Heat Map', 'Pulse Theory', 'runner-red', 176, 'Drum & Bass'],
  [214, 'Blue Smoke', 'Lumen Haze', 'smoke-blue', 252, 'Ambient'],
  [215, 'Violet Lanes', 'Highway Ghosts', 'trails-violet', 223, 'Synthwave'],
  [216, 'Feedback', 'Dial Tone', 'aurora-violet', 206, 'Electronic'],
];
const mk = ([id, title, artist, cover, sec, genre, status = 'cloud', liked = 0], i = 0) => ({
  id, title, artist, artwork: C(cover), duration: sec, genre,
  permalink: `https://soundcloud.com/${artist.toLowerCase().replace(/[^a-z0-9]+/g, '-')}/${String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'track-' + id}`,
  status, in_telegram: status === 'ready', liked: !!liked, play_count: (id * 7) % 23,
  added_at: now() - i * 3600 * 7, stream: `demo://stream/${id}`, file: `demo://file/${id}`,
});

const DB = {
  tracks: new Map(),
  library: [], // ordered ids
  playlists: [],
  mixtapes: [],
};
RAW.forEach((r, i) => { const t = mk(r, i); DB.tracks.set(t.id, t); DB.library.push(t.id); });
POOL_RAW.forEach((r) => { const t = mk(r); DB.tracks.set(t.id, t); });
const pool = POOL_RAW.map((r) => r[0]);

const pl = (id, kind, title, artist, cover, ids, watch = false) => ({ id, kind, title, artist, artwork: C(cover), permalink: 'https://soundcloud.com/', watch, ids,
  get track_count() { return this.ids.length; }, get duration() { return this.ids.reduce((s, x) => s + (DB.tracks.get(x) || { duration: 0 }).duration, 0); },
  created_at: now(), synced_at: now() });
DB.playlists.push(
  pl('p1', 'playlist', 'Night Drive', 'Highway Ghosts', 'trails', [104, 111, 116, 101, 117, 118], true),
  pl('p2', 'album', 'Бетон и дождь', 'Гранит', 'tower', [102, 114, 108, 113]),
  pl('p3', 'likes', 'Лайки alex.wav', 'alex.wav', 'blur', [101, 102, 104, 106, 110, 115]),
  pl('p4', 'playlist', 'Run Club', 'Pulse Theory', 'runner', [105, 112, 118, 109]),
);
const mx = (code, title, theme, note, ids, claims, owner = 'Алекс', mine = true) => ({ code, title, theme, note, ids, claims, owner, owner_id: mine ? 1 : 2, mine, created_at: now() });
DB.mixtapes.push(
  mx('NEON7Q', 'Для Лёши: ночная трасса', 'cobalt', 'Включай на МКАДе после полуночи', [104, 111, 101, 107, 115], 3),
  mx('BETON2', 'Бетон', 'red', '', [102, 114, 108, 113, 103], 1),
);
const foreign = mx('GIFT01', 'Лето в наушниках', 'lime', 'Это под пробежку 🏃', [206, 213, 105, 201, 209], 12, 'Маша', false);

const plOut = (p) => ({ id: p.id, kind: p.kind, title: p.title, artist: p.artist, artwork: p.artwork, permalink: p.permalink, watch: p.watch,
  track_count: p.track_count, duration: p.duration, created_at: p.created_at, synced_at: p.synced_at });
const mxOut = (m, full = false) => {
  const ts = m.ids.map((x) => DB.tracks.get(x)).filter(Boolean);
  const o = { code: m.code, title: m.title, theme: m.theme, note: m.note, owner: m.owner, owner_id: m.owner_id, track_count: ts.length,
    duration: ts.reduce((s, t) => s + t.duration, 0), claims: m.claims, created_at: m.created_at, covers: ts.map((t) => t.artwork).slice(0, 4) };
  if (full) { o.tracks = ts.map(tOut); o.mine = m.mine; o.link = `https://t.me/VibeCloudBot/app?startapp=mx_${m.code}`; }
  return o;
};
const tOut = (t) => ({ ...t, in_library: DB.library.includes(t.id) });
/* ---- v2 demo data: leaderboard, duos (all people here are fictional) ---- */
const PEOPLE = [
  { id: 2, name: 'Маша', status: '👑 Легенда', min: 1840, genres: ['House', 'Indie'] },
  { id: 3, name: 'Тимур', status: '🎧 Диджей', min: 1512, genres: ['Techno'] },
  { id: 1, name: 'Алекс', status: '🔥 Меломан', min: 1290, genres: ['Post-Punk', 'Synthwave'] },
  { id: 4, name: 'Соня', status: null, min: 990, genres: ['Dream Pop'] },
  { id: 5, name: 'Даня', status: '🌙 Ночной слушатель', min: 760, genres: ['Ambient'] },
  { id: 6, name: 'Вика', status: null, min: 410, genres: ['Electronic'] },
  { id: 7, name: 'Лёша', status: null, min: 205, genres: ['Synthwave'] },
];
let meHidden = false, listenedSec = 1290 * 60;
const person = (id) => PEOPLE.find((x) => x.id === id);
const pOut = (x, k = 1) => ({ id: x.id, name: x.name, photo: null, status: x.status, minutes: Math.round(x.min * k) });
DB.duos = [
  { id: 'd1', title: 'Алекс + Маша: дорога на юг', a: 1, b: 2, state: 'active', ids: [105, 109, 118, 101], by: { 105: 2, 109: 1, 118: 2, 101: 1 } },
  { id: 'd2', title: 'Ночные смены', a: 5, b: 1, state: 'pending', ids: [], by: {} },
];
const duoOut = (d, full = false) => {
  const partner = person(d.a === 1 ? d.b : d.a), ts = d.ids.map((x) => DB.tracks.get(x)).filter(Boolean);
  const o = { id: d.id, title: d.title, state: d.state, incoming: d.state === 'pending' && d.b === 1, partner: pOut(partner), me: pOut(person(1)),
    track_count: ts.length, duration: ts.reduce((s2, t) => s2 + t.duration, 0), artwork: ts[0] ? ts[0].artwork : null };
  if (full) { o.match = 42; o.tracks = ts.map((t) => ({ ...tOut(t), added_by: d.by[t.id] || 1, added_by_name: (d.by[t.id] || 1) === 1 ? 'ты' : partner.name })); }
  return o;
};
const LIST_JOBS = {};
function demoParse(text) {
  return String(text || '').split(/\r?\n/).map((l) => l.replace(/^\s*\d+[.)]\s*/, '').trim()).filter(Boolean)
    .map((l) => { const m = l.split(/\s+[—–-]\s+/); return m.length > 1 ? { artist: m[0], title: m.slice(1).join(' - ') } : { artist: '', title: l }; })
    .filter((x) => x.title && !/^\d+:\d\d$/.test(x.title));
}
function runList(text, title) {
  const items = demoParse(text).slice(0, 40), jid = 'L' + (++seq), name = title || 'Яндекс Музыка';
  const ids = [], missing = [];
  LIST_JOBS[jid] = { id: jid, kind: 'list', title: name, total: items.length, added: 0, status: 'running', playlist_id: null, missing };
  (async () => {
    emit('job', { id: jid, status: 'running', kind: 'list', title: name, artwork: null, total: items.length, added: 0, found: 0 });
    for (let i = 0; i < items.length; i++) {
      await sleep(260);
      if (i % 4 === 3) missing.push(items[i]);
      else { const [id] = takeFromPool(1); const t = DB.tracks.get(id); t.title = items[i].title; t.artist = items[i].artist || t.artist; ids.push(id); DB.library.unshift(id); emit('track_added', { job: jid, playlist: null, track: tOut(t) }); }
      emit('job', { id: jid, status: 'running', kind: 'list', title: name, artwork: ids[0] ? DB.tracks.get(ids[0]).artwork : null, total: items.length, added: i + 1, found: ids.length });
    }
    const p2 = pl('p' + (++seq), 'imported', name, 'Перенос по списку', ids[0] ? DB.tracks.get(ids[0]).artwork : C('aurora'), ids.slice());
    DB.playlists.unshift(p2); Object.assign(LIST_JOBS[jid], { status: 'done', added: items.length, playlist_id: p2.id });
    emit('job', { id: jid, status: 'done', kind: 'list', title: name, artwork: p2.artwork, total: items.length, added: items.length, found: ids.length, missing: missing.length, playlist: plOut(p2) });
  })();
  return { ok: true, count: items.length, total: items.length, sample: items.slice(0, 5) };
}
function ewaDemo(text) {
  const t = String(text || '').toLowerCase();
  if (/яндекс/.test(t)) return { text: 'Из Яндекс Музыки переносим честно: скопируй список своих треков (на music.yandex.ru — «Мне нравится», прокрути до конца, выдели и скопируй) или загрузи файл — я найду те же песни на SoundCloud.', tracks: [], actions: [{ open: 'yandex' }] };
  if (/парн|дуэт|вдво/.test(t)) return { text: 'Парный плейлист — общий плейлист на двоих: открой Топ, нажми на человека и пригласи. После принятия вы оба добавляете треки, а ✦ подкинет общие вкусы.', tracks: [], actions: [{ open: 'leaders' }] };
  const res = [...DB.tracks.values()].filter((x) => !DB.library.includes(x.id)).slice(0, 3);
  return { text: t.includes('вечер') ? 'Вечером зайдёт что-то тёплое и неспешное — вот пара треков с мягким синтом и воздухом 🌙' : 'Держи подборку под это настроение — жми ▶, чтобы послушать, или +, чтобы забрать в медиатеку ✨', tracks: res.map(tOut), actions: [] };
}

function stats() {
  const ts = DB.library.map((x) => DB.tracks.get(x));
  return { listened_min: Math.round(listenedSec / 60), rank: meHidden ? null : 3,
    tracks: ts.length, hours: +(ts.reduce((s, t) => s + t.duration, 0) / 3600).toFixed(1), streak: 12,
    week: [1, 1, 0, 1, 1, 1, 1], playlists: DB.playlists.length, listens: 148, top_genres: ['Post-Punk', 'Synthwave', 'Ambient'] };
}

/* ------------------------------------------------------------ events */
const subs = [];
const emit = (name, data) => subs.forEach((fn) => { try { fn(name, data); } catch (e) { console.error(e); } });

function download(id, delay = 0) {
  const t = DB.tracks.get(id); if (!t || t.status === 'ready' || t.status === 'unavailable' || t._dl) return;
  t._dl = true;
  setTimeout(async () => {
    t.status = 'downloading'; t.progress = 0; emit('track_status', { id, status: 'downloading', progress: 0 });
    for (let p = 8; p <= 100; p += 8 + Math.floor(Math.random() * 14)) { await sleep(140 + Math.random() * 120); t.progress = Math.min(p, 99); emit('track_status', { id, status: 'downloading', progress: t.progress }); }
    await sleep(180); t.status = 'ready'; t.progress = 100; t.in_telegram = true; t._dl = false;
    emit('track_status', { id, status: 'ready', progress: 100, in_telegram: true });
  }, delay);
}

function takeFromPool(n) {
  const ids = [];
  while (ids.length < n && pool.length) ids.push(pool.shift());
  if (ids.length < n) { // pool exhausted: mint copies so the demo never runs dry
    for (let i = ids.length; i < n; i++) {
      const src = DB.tracks.get(POOL_RAW[i % POOL_RAW.length][0]); const id = ++seq;
      DB.tracks.set(id, { ...src, id, title: src.title + ' (Live)', status: 'cloud', stream: `demo://stream/${id}`, file: `demo://file/${id}` }); ids.push(id);
    }
  }
  return ids;
}

const KIND = { track: 'Трек', playlist: 'Плейлист', likes: 'Лайки', user_tracks: 'Треки артиста', user_sets: 'Плейлисты профиля' };
function parse(url) {
  const m = String(url || '').match(/soundcloud\.com\/([^\s?#]+)/i); if (!m) return null;
  const seg = m[1].replace(/\/+$/, '').split('/');
  const user = seg[0] || 'artist';
  if (/^on\./i.test(url.replace(/^https?:\/\//, ''))) return { kind: 'playlist', user: 'friend', slug: 'shared' };
  if (seg.length === 1) return { kind: 'user', user };
  if (seg[1] === 'likes') return { kind: 'likes', user };
  if (seg[1] === 'sets' && seg.length === 2) return { kind: 'user_sets', user };
  if (seg[1] === 'sets') return { kind: 'playlist', user, slug: seg[2] };
  if (seg[1] === 'tracks' || seg[1] === 'popular-tracks') return { kind: 'user_tracks', user };
  return { kind: 'track', user, slug: seg[1] };
}
const titleCase = (s) => String(s || '').replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()).trim();

async function runImport(url, mode) {
  const r = parse(url); if (!r) throw new Error('Это не ссылка SoundCloud');
  let kind = r.kind;
  if (kind === 'user') kind = mode || 'likes';
  const jid = 'j' + (++seq);
  const n = kind === 'track' ? 1 : kind === 'user_sets' ? 9 : kind === 'likes' ? 12 : 8;
  const ids = takeFromPool(n);
  const first = DB.tracks.get(ids[0]);
  const title = kind === 'track' ? (r.slug ? titleCase(r.slug) : first.title)
    : kind === 'likes' ? `Лайки ${r.user}` : kind === 'user_tracks' ? `Треки ${r.user}` : kind === 'user_sets' ? `Плейлисты ${r.user}`
    : r.slug ? titleCase(r.slug) : 'Smoke Signals';
  if (kind === 'track' && r.slug) first.title = title;
  const art = first.artwork;
  (async () => {
    emit('job', { id: jid, status: 'running', kind, title, artwork: art, total: 0, added: 0 });
    await sleep(650);
    emit('job', { id: jid, status: 'running', kind, title, artwork: art, total: ids.length, added: 0 });
    for (const id of ids) {
      await sleep(kind === 'track' ? 250 : 190);
      const t = DB.tracks.get(id); t.added_at = now();
      DB.library = [id, ...DB.library.filter((x) => x !== id)];
      emit('track_added', { job: jid, playlist: null, track: tOut(t) });
      // LITE: nothing is pre-downloaded — tracks play straight from the CDN, MP3s are made on demand
    }
    let playlist = null;
    if (kind !== 'track') {
      const p = pl('p' + (++seq), kind === 'user_sets' ? 'playlist' : kind, title, r.user, art, ids.slice());
      DB.playlists.unshift(p); playlist = plOut(p);
    }
    await sleep(300);
    emit('job', { id: jid, status: 'done', kind, title, artwork: art, total: ids.length, added: ids.length, new: ids.length, playlist });
  })();
  return { job_id: jid, queued: true };
}

/* ------------------------------------------------------------ api */
const bars = (id, n = 72) => {
  let x = (id * 2654435761) % 2147483647 || 3; const out = [];
  for (let i = 0; i < n; i++) {
    x = (x * 48271) % 2147483647;
    const env = Math.min(1, i / 6) * Math.min(1, (n - i) / 5);
    const groove = 0.55 + 0.45 * Math.sin(i / 3.2 + id) * Math.cos(i / 11);
    out.push(+Math.max(0.12, env * (0.35 + 0.65 * Math.abs(groove)) * (0.75 + (x % 1000) / 4000)).toFixed(3));
  }
  return out;
};

async function handle(method, path, body = {}) {
  await sleep(90 + Math.random() * 120);
  const u = new URL(path, 'https://demo.local');
  const p = u.pathname.replace(/^\/api\//, '').split('/');
  const fail = (msg) => { throw new Error(msg); };

  if (p[0] === 'bootstrap') {
    return { user: { id: 1, first_name: 'Алекс', username: 'alex', status: person(1).status, admin: true }, stats: stats(),
      tracks: DB.library.map((x) => tOut(DB.tracks.get(x))), playlists: DB.playlists.map(plOut), mixtapes: DB.mixtapes.map((m) => mxOut(m)), jobs: [],
      duos: DB.duos.map((d) => duoOut(d)), events_token: 'demo',
      config: { bot: 'VibeCloudBot', app_link: 'https://t.me/VibeCloudBot/app', storage: true, lite: true, direct: true, ai: true, list_max: 1500 } };
  }
  if (p[0] === 'listen') { listenedSec += +body.seconds || 0; return { ok: true }; }
  if (p[0] === 'import' && p[1] === 'list' && p[2] === 'preview') { const it = demoParse(body.text); return { count: it.length, sample: it.slice(0, 5) }; }
  if (p[0] === 'import' && p[1] === 'list') { if (!demoParse(body.text).length) fail('Не нашёл треков'); return runList(body.text, body.title); }
  if (p[0] === 'job') { const j = LIST_JOBS[p[1]]; if (!j) fail('Не найдено'); return j; }
  if (p[0] === 'ewa') { await sleep(700); return ewaDemo(body.text); }
  if (p[0] === 'leaders') {
    const period = u.searchParams.get('period') || 'week', k = { week: 1, month: 3.6, all: 11 }[period];
    const users = PEOPLE.filter((x) => !(x.id === 1 && meHidden)).map((x) => pOut(x, k)).sort((a, b) => b.minutes - a.minutes);
    return { period, users, me: { ...pOut(person(1), k), rank: meHidden ? null : users.findIndex((x) => x.id === 1) + 1, hidden: meHidden }, admin: true };
  }
  if (p[0] === 'me' && p[1] === 'hidden') { meHidden = !!body.hidden; return { ok: true, hidden: meHidden }; }
  if (p[0] === 'admin') { const x = person(+body.user_id); if (!x) fail('Не найден'); x.status = body.status || null; return { ok: true, status: x.status }; }
  if (p[0] === 'user') {
    const x = person(+p[1]); if (!x) fail('Не найден');
    const duo = DB.duos.find((d) => [d.a, d.b].includes(x.id) && [d.a, d.b].includes(1) && x.id !== 1);
    return { ...pOut(x), week_minutes: x.min, total_minutes: x.min * 11, rank: PEOPLE.slice().sort((a, b) => b.min - a.min).indexOf(x) + 1,
      tracks: 120 + x.id * 7, top_genres: x.genres, match: x.id === 1 ? null : 20 + ((x.id * 17) % 60), duo: duo ? duoOut(duo) : null, me: x.id === 1 };
  }
  if (p[0] === 'duos') {
    if (method === 'POST') {
      const x = person(+body.user_id); if (!x) fail('Не найден');
      if (DB.duos.some((d) => d.state === 'pending' && d.b === x.id)) fail('Приглашение уже отправлено — ждём ответа');
      const d = { id: 'd' + (++seq), title: body.title || `Алекс + ${x.name}`, a: 1, b: x.id, state: 'pending', ids: [], by: {} };
      DB.duos.unshift(d); return duoOut(d);
    }
    return { duos: DB.duos.map((d) => duoOut(d)) };
  }
  if (p[0] === 'duo') {
    const d = DB.duos.find((x) => x.id === p[1]); if (!d) fail('Парный плейлист не найден');
    if (method === 'DELETE' && p[2] === 'track') { d.ids = d.ids.filter((x) => x !== +p[3]); return { ok: true }; }
    if (method === 'DELETE') { DB.duos = DB.duos.filter((x) => x !== d); return { ok: true }; }
    if (p[2] === 'accept') { d.state = 'active'; d.ids = [102, 106, 115]; d.ids.forEach((x) => { d.by[x] = 1; }); return duoOut(d); }
    if (p[2] === 'decline') { DB.duos = DB.duos.filter((x) => x !== d); return { id: d.id, deleted: true }; }
    if (p[2] === 'add') { const before = d.ids.length; (body.track_ids || []).forEach((x) => { if (!d.ids.includes(+x)) { d.ids.push(+x); d.by[+x] = 1; } }); return { ok: true, added: d.ids.length - before }; }
    if (p[2] === 'fill') { const before = d.ids.length; [104, 111, 107].forEach((x) => { if (!d.ids.includes(x)) { d.ids.push(x); d.by[x] = 2; } }); return { ok: true, added: d.ids.length - before }; }
    return duoOut(d, true);
  }
  if (p[0] === 'import' && p[1] === 'inspect') {
    const r = parse(body.url); if (!r) fail('Это не ссылка SoundCloud');
    if (r.kind === 'user') return { kind: 'user', label: 'Профиль', url: body.url, title: r.user, artist: r.user, artwork: C('aurora'), count: 0,
      options: [{ mode: 'likes', label: 'Лайки', count: 12 }, { mode: 'user_tracks', label: 'Треки', count: 8 }, { mode: 'user_sets', label: 'Плейлисты', count: 3 }] };
    return { kind: r.kind, label: KIND[r.kind], url: body.url, title: titleCase(r.slug || r.user), count: r.kind === 'track' ? 1 : 8 };
  }
  if (p[0] === 'import') return runImport(body.url, body.mode);
  if (p[0] === 'search') {
    const q = (u.searchParams.get('q') || '').toLowerCase();
    const all = [...DB.tracks.values()];
    let res = all.filter((t) => (t.title + ' ' + t.artist + ' ' + t.genre).toLowerCase().includes(q));
    if (res.length < 4) res = res.concat(pool.map((x) => DB.tracks.get(x)).filter((t) => !res.includes(t))).slice(0, 6);
    return { results: res.slice(0, 12).map(tOut) };
  }
  if (p[0] === 'track') {
    const id = +p[1], t = DB.tracks.get(id); if (!t) fail('Трек не найден');
    switch (p[2]) {
      case 'waveform': return { bars: bars(id, +(u.searchParams.get('bars') || 72)) };
      case 'like': t.liked = !!body.liked; return { ok: true, liked: t.liked };
      case 'played': t.play_count++; return { ok: true };
      case 'prefetch': download(id, 200); return { ok: true };
      case 'add':
        if (!DB.library.includes(id)) { DB.library.unshift(id); t.added_at = now(); }
        return tOut(t);
      case 'send': if (t.status === 'unavailable') fail('Этот трек нельзя скачать'); download(id, 0); return { ok: true, queued: t.status !== 'ready' };
      case 'share': return { prepared_id: null, inline_query: `tr_${id}`, link: `https://t.me/VibeCloudBot/app?startapp=tr_${id}` };
      default:
        if (method === 'DELETE') { DB.library = DB.library.filter((x) => x !== id); return { ok: true }; }
    }
  }
  if (p[0] === 'playlist') {
    const x = DB.playlists.find((q) => q.id === p[1]); if (!x) fail('Плейлист не найден');
    if (method === 'DELETE') { DB.playlists = DB.playlists.filter((q) => q !== x); return { ok: true }; }
    if (p[2] === 'watch') { x.watch = !!body.watch; return { ok: true, watch: x.watch }; }
    if (p[2] === 'send') { x.ids.slice(0, 4).forEach((id, i) => download(id, i * 400)); return { ok: true, count: x.ids.filter((id) => DB.tracks.get(id).status !== 'unavailable').length }; }
    if (p[2] === 'sync') {
      const add = takeFromPool(1); x.ids.push(...add); add.forEach((id) => { if (!DB.library.includes(id)) DB.library.unshift(id); emit('track_added', { job: null, playlist: x.id, track: tOut(DB.tracks.get(id)) }); });
      return { ok: true, new: add.length };
    }
    return { ...plOut(x), tracks: x.ids.map((id) => tOut(DB.tracks.get(id))) };
  }
  if (p[0] === 'mixtapes' && method === 'POST') {
    const code = Math.random().toString(36).slice(2, 8).toUpperCase();
    const m = mx(code, body.title || 'Микстейп', body.theme || 'cobalt', body.note || '', (body.track_ids || []).map(Number), 0);
    DB.mixtapes.unshift(m); return { ...mxOut(m), link: `https://t.me/VibeCloudBot/app?startapp=mx_${code}` };
  }
  if (p[0] === 'mixtape') {
    const m = p[1] === foreign.code ? foreign : DB.mixtapes.find((q) => q.code === p[1]); if (!m) fail('Микстейп не найден');
    if (method === 'DELETE') { DB.mixtapes = DB.mixtapes.filter((q) => q !== m); return { ok: true }; }
    if (p[2] === 'share') return { prepared_id: null, link: `https://t.me/VibeCloudBot/app?startapp=mx_${m.code}`, text: `🎁 Микстейп «${m.title}»` };
    if (p[2] === 'claim') {
      m.claims++;
      const fresh = m.ids.filter((id) => !DB.library.includes(id));
      fresh.forEach((id, i) => setTimeout(() => { DB.library.unshift(id); const t = DB.tracks.get(id); emit('track_added', { job: null, playlist: null, track: tOut(t) }); }, 150 + i * 160));
      const p2 = pl('p' + (++seq), 'mixtape', m.title, `от ${m.owner}`, DB.tracks.get(m.ids[0]).artwork, m.ids.slice());
      DB.playlists.unshift(p2); setTimeout(() => emit('playlist', plOut(p2)), 200);
      return { ok: true, playlist_id: p2.id, count: m.ids.length };
    }
    return mxOut(m, true);
  }
  fail('Демо: метод не поддерживается');
}

/* ------------------------------------------------------------ fake audio */
class FakeAudio extends EventTarget {
  constructor() { super(); this._src = ''; this._t = 0; this._dur = NaN; this.paused = true; this.error = null; this._timer = null; this._id = 0; }
  get src() { return this._src; }
  set src(v) {
    this.pause(true); this._src = v || ''; this._t = 0;
    const id = +(String(v).match(/(\d+)$/) || [])[1]; this._id = id;
    const t = DB.tracks.get(id); this._dur = t ? t.duration : NaN; this.error = null;
  }
  get currentTime() { return this._t; }
  set currentTime(v) { this._t = Math.max(0, Math.min(v, this._dur || 0)); this._fire('timeupdate'); }
  get duration() { return this._dur; }
  _fire(n) { this.dispatchEvent(new Event(n)); }
  play() {
    if (!this._src) return Promise.reject(new Error('no src'));
    if (!this.paused) return Promise.resolve();
    this.paused = false; this._fire('play'); this._fire('waiting');
    const t = DB.tracks.get(this._id), cold = t && t.status !== 'ready';
    clearTimeout(this._start);
    this._start = setTimeout(() => {
      if (this.paused) return;
      this._fire('playing');
      clearInterval(this._timer);
      this._timer = setInterval(() => {
        this._t += 0.25; this._fire('timeupdate');
        if (this._dur && this._t >= this._dur) { clearInterval(this._timer); this.paused = true; this._fire('ended'); }
      }, 250);
    }, cold ? 700 : 260);
    return Promise.resolve();
  }
  pause(silent = false) {
    clearInterval(this._timer); clearTimeout(this._start);
    if (this.paused) return; this.paused = true; if (!silent) this._fire('pause');
  }
  load() {}
}

let toured = false;
function subscribe(fn) {
  subs.push(fn);
  if (window.VC_TOUR && !toured) { toured = true; setTimeout(() => runImport('https://soundcloud.com/ash-and-echo/sets/smoke-signals'), window.VC_TOUR_DELAY || 2600); }
}

// the single-file showcase drives the demo from outside the phone frame
addEventListener('message', (e) => {
  const d = e.data || {};
  if (d.vc === 'import' && d.url) runImport(d.url, d.mode).catch(() => {});
  if (d.vc === 'go' && window.VC) window.VC.go(d.view);
  if (d.vc === 'profile' && window.VC) window.VC.startImport(d.url);
});

window.VCDemo = { handle, subscribe, FakeAudio, emit, runImport, DB };
})();
