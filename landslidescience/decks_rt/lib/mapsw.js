/*
 * lib/mapsw.js — the service worker that keeps what a deck's live maps fetch, so the talk can
 * show the same views with a poor connection or none. Registered by lib/practice.js (the
 * deck page, the owner only, when Practice is first switched on) once per folder a map
 * figure lives in (/inventory/, /lidar/compare/), and served by the site with
 * "Service-Worker-Allowed: /" so it may take those scopes. It is unregistered again when
 * nothing is saved.
 *
 * It only touches pages that are a deck's figure: a frame whose address has embed=1, and the
 * workers such a page starts. Every other page under its scope goes to the network exactly
 * as if this file did not exist.
 *
 * Answering (always, practising or not):
 *   another host's file (tiles, libraries)   the saved copy first, else the network
 *   this site's file (the page, its data)    the network first; the saved copy after 3 s
 *                                            or with no connection. A fresh answer replaces
 *                                            the saved one.
 * Keeping (only while the deck page says Practice is on, repeated every few seconds;
 * 15 s without word and nothing more is kept):
 *   each answer that is not saved yet goes into the cache of the figure the deck names
 *   (the slide showing, or the nearest one being loaded ahead) for this run:
 *       maps|<deck>|<figure>|<run>
 *   with a small record beside it (META: files, bytes, what could not be kept; one record
 *   per folder's worker, since a map loading ahead may be writing the same cache). The deck
 *   page reads those caches itself for the count on screen and for the review, where each
 *   run is kept (the record gets kept: true) or discarded (the cache is deleted).
 *   A figure's caches may hold slideMax bytes and a deck's deckMax (sent by the page): at
 *   the limit nothing more is kept for it, and the record says so (full).
 * A request for part of a file (PMTiles, a COG) is kept under its address plus the range,
 * and answered again as a 206. The compare page's per-pane "pk" is left out of the key.
 * Not kept: answers the browser will not show a worker (a host without CORS headers, after
 * asking again with CORS has failed: counted
 * in the record, by host), anything over BIG, anything but a 200 or 206.
 */
const PRE = "maps|";
const META = self.location.origin + "/__maps/n";                 // every record of a cache (matched with ignoreSearch)
const MINE = META + "?s=" + encodeURIComponent(new URL(self.registration.scope).pathname);   // this worker's: each folder's worker counts its own
const BIG = 32 << 20;
let state = null;                // what the deck page last said: {deck, fig, label, run, slideMax, deckMax, at}
let tally = null;                // a promise of the run being written: {name, cache, rec, figBase, deckBase}
const kind = new Map();          // client id → is it a deck's figure
const busy = new Set();          // keys being written now
const sealed = new Set();        // hosts that refuse a request with CORS
let names = null, namesAt = 0;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => ev.waitUntil(self.clients.claim()));

const embed = (url) => /[?&]embed=1(&|#|$)/.test(url);
async function judge(id) {       // is this client a deck's figure, or a worker of one
  if (!id) return false;
  const c = await self.clients.get(id);
  let yes;
  if (c && c.type === "window") yes = c.frameType === "nested" && embed(c.url);
  // a worker (Chrome does not find a page's own workers by id): a figure's, if a figure is open
  else yes = (await self.clients.matchAll({ type: "window" })).some((w) => w.frameType === "nested" && embed(w.url));
  kind.set(id, yes);
  return yes;
}
function keyOf(req) {
  const u = new URL(req.url);
  u.hash = "";
  if (u.searchParams.has("pk")) u.searchParams.delete("pk");
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get("range") || "");
  if (m) u.searchParams.set("__range", m[1] + "-" + m[2]);
  return u.href;
}
async function all() {           // the names of every cache of saved map files, any deck's
  if (!names || Date.now() - namesAt > 3000) { names = (await caches.keys()).filter((k) => k.startsWith(PRE)); namesAt = Date.now(); }
  return names;
}
async function lookup(key, loose) {
  for (const name of await all()) {
    const r = await caches.match(key, { cacheName: name, ignoreSearch: !!loose });
    if (r) return { name: name, resp: r };
  }
  return null;
}
function out(resp) {             // a saved answer as it first came
  const st = +resp.headers.get("X-Deck-Status") || 200;
  if (st === 200) return resp;
  return new Response(resp.body, { status: st, headers: resp.headers });
}
function plain(r, blob) {        // an answer in the form a cache takes: a 200, without the headers that no longer describe it
  const h = new Headers(r.headers);
  ["Content-Encoding", "Content-Length", "Vary"].forEach((k) => h.delete(k));
  h.set("X-Deck-Status", String(r.status));
  return new Response(blob, { status: 200, headers: h });
}

// ---- keeping ----
const recording = () => !!(state && state.fig && Date.now() - state.at < 15000);
async function sums(deck, fig, but) {      // bytes already saved for this figure and for this deck, leaving out one cache
  let f = 0, d = 0;
  for (const name of (await caches.keys())) {
    if (!name.startsWith(PRE + deck + "|") || name === but) continue;
    let b = 0;
    for (const m of await (await caches.open(name)).matchAll(META, { ignoreSearch: true })) b += (await m.json()).bytes || 0;
    d += b;
    if (name.startsWith(PRE + deck + "|" + fig + "|")) f += b;
  }
  return [f, d];
}
function tallyFor(st) {
  const name = PRE + st.deck + "|" + st.fig + "|" + st.run;
  if (tally && tally.name === name) return tally.made;
  const made = (async () => {
    const cache = await caches.open(name);
    names = null;
    const old = await cache.match(MINE), was = old ? await old.json() : {};
    const base = await sums(st.deck, st.fig, name);
    // (the other folder's worker may be writing this cache too: its bytes are in its own record, read again at each change of slide)
    for (const q of await cache.keys(META, { ignoreSearch: true })) if (q.url !== MINE) { const b = (await (await cache.match(q)).json()).bytes || 0; base[0] += b; base[1] += b; }
    return { name: name, cache: cache, figBase: base[0], deckBase: base[1], dirty: true, timer: 0,
      rec: { deck: st.deck, fig: st.fig, label: st.label, run: st.run, n: was.n || 0, bytes: was.bytes || 0,
             missed: was.missed || {}, full: "", kept: false, at: Date.now() } };
  })();
  tally = { name: name, made: made };
  return made;
}
async function flush(t) {
  clearTimeout(t.timer); t.timer = 0;
  if (!t.dirty) return;
  t.dirty = false;
  t.rec.at = Date.now();
  await t.cache.put(MINE, new Response(JSON.stringify(t.rec), { headers: { "Content-Type": "application/json" } }));
}
function soon(t) {
  t.dirty = true;
  if (!t.timer) t.timer = setTimeout(() => flush(t).catch(() => {}), 700);
}
async function miss(st, req) {
  const t = await tallyFor(st), host = new URL(req.url).host;
  t.rec.missed[host] = (t.rec.missed[host] || 0) + 1;
  soon(t);
}
async function store(st, key, r) {
  if (busy.has(key)) return;
  busy.add(key);
  try {
    const t = await tallyFor(st);
    if (t.rec.full) return;
    const blob = await r.blob();
    if (blob.size > BIG) { t.rec.missed[new URL(key).host] = (t.rec.missed[new URL(key).host] || 0) + 1; soon(t); return; }
    if (t.figBase + t.rec.bytes + blob.size > st.slideMax) { t.rec.full = "slide"; soon(t); return; }
    if (t.deckBase + t.rec.bytes + blob.size > st.deckMax) { t.rec.full = "deck"; soon(t); return; }
    await t.cache.put(key, plain(r, blob));
    t.rec.n++; t.rec.bytes += blob.size;
    soon(t);
  } finally { busy.delete(key); }
}
function keep(ev, req, key, r) {
  if (!recording()) return;
  const st = state;
  if (r.type === "opaque") { ev.waitUntil(miss(st, req).catch(() => {})); return; }
  if (r.status !== 200 && r.status !== 206) return;
  if (+(r.headers.get("Content-Length") || 0) > BIG) { ev.waitUntil(miss(st, req).catch(() => {})); return; }
  ev.waitUntil(store(st, key, r.clone()).catch(() => {}));
}
async function refresh(ev, hit, key, r) {  // this site's file came fresh: the saved copy follows it
  if (r.status !== 200 && r.status !== 206) return;
  const copy = r.clone();
  ev.waitUntil((async () => {
    const blob = await copy.blob();
    await (await caches.open(hit.name)).put(key, plain(copy, blob));
  })().catch(() => {}));
}

// ---- answering ----
async function answer(ev, req) {
  const key = keyOf(req), own = new URL(req.url).origin === self.location.origin;
  const hit = await lookup(key);
  if (!own) {
    if (hit) return out(hit.resp);
    // A script or picture asked for without CORS comes back sealed and cannot be kept. While
    // practising it is asked for with CORS instead (no cookies), which a public host allows.
    if (req.mode === "no-cors" && recording() && !sealed.has(new URL(req.url).host)) {
      try {
        const open = await fetch(req.url, { mode: "cors", credentials: "omit" });
        keep(ev, req, key, open);
        return open;
      } catch (e) { sealed.add(new URL(req.url).host); }
    }
    const r = await fetch(req);
    keep(ev, req, key, r);
    return r;
  }
  const ctl = new AbortController(), t = hit ? setTimeout(() => ctl.abort(), 3000) : 0;
  try {
    const r = await fetch(req, { signal: ctl.signal });
    clearTimeout(t);
    if (hit) refresh(ev, hit, key, r); else keep(ev, req, key, r);
    return r;
  } catch (e) {
    clearTimeout(t);
    if (hit) return out(hit.resp);
    // the page itself, asked for with another view in its address: any saved copy of the page will do
    const any = req.mode === "navigate" ? await lookup(key, true) : null;
    return any ? out(any.resp) : Response.error();
  }
}
self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {
    if (req.destination !== "iframe" || !embed(req.url)) return;
    if (ev.resultingClientId) kind.set(ev.resultingClientId, true);
    ev.respondWith(answer(ev, req));
    return;
  }
  const k = kind.get(ev.clientId);
  if (k === false) return;
  ev.respondWith(k ? answer(ev, req) : judge(ev.clientId).then((yes) => (yes ? answer(ev, req) : fetch(req))));
});

// The deck page: {type: "maps:set", deck, fig, label, run, slideMax, deckMax}; fig absent = keep nothing.
// Answered on the message's port when what was counted so far is written down.
self.addEventListener("message", (ev) => {
  const d = ev.data || {};
  if (d.type !== "maps:set") return;
  ev.waitUntil((async () => {
    const was = tally;
    state = d.fig ? { deck: d.deck, fig: d.fig, label: d.label, run: d.run, slideMax: d.slideMax, deckMax: d.deckMax, at: Date.now() } : null;
    if (was) {
      const t = await was.made.catch(() => null);
      if (t) await flush(t).catch(() => {});
      if (!state || was.name !== PRE + state.deck + "|" + state.fig + "|" + state.run) tally = null;   // the sums are taken again next time
    }
    if (ev.ports && ev.ports[0]) ev.ports[0].postMessage({ ok: true });
  })());
});
