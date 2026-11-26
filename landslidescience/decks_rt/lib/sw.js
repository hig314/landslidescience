/*
 * lib/sw.js — the service worker of a deck on the web. It is served next to the deck's
 * index.html with VERSION and FILES filled in (by the site for /decks/<name>/, or by
 * tools/publish.py for a static copy); its scope is that deck's folder.
 *
 * It keeps every file of the deck in a cache named for this version. Requests:
 *   the page and deck.json   network first (3 s), so a changed deck shows at once when
 *                            there is a connection; the saved copy otherwise
 *   every other deck file    the saved copy first; byte ranges (movies) are cut from it. A copy saved
 *                            by an earlier version is used only if the file has not changed since
 *                            (or there is no connection): a changed figure must not show its old self.
 *                            A file not saved yet comes from the network and is saved as it passes.
 *   anything else            untouched: maps in frames belong to other pages, and saves
 *                            from the editor are not GETs
 * The rest is filled in the background (deck:fill from the page, when the slides near the one
 * showing have their pictures; progress is posted back as deck:offline): one request at a time,
 * in the order the page gives (the talk's order from the slide showing; movies last). The page
 * always goes first: while it waits for a file the fill starts nothing new. A large file (a
 * movie) is fetched in pieces of PIECE bytes, each kept as it arrives, so the page never waits
 * behind more than one piece, and a fill that is interrupted (the page closed, the worker
 * stopped by the browser, the connection lost) goes on from the pieces it has. No file is
 * fetched twice: the fill and the page share a request for the same file.
 * The page repeats deck:fill every 20 s until the fill is done: that keeps this worker
 * alive, starts the fill again if the browser stopped it, and is answered with the progress.
 * A file may carry a query (the site: "v=…&w=…", the version and the display copy's width). It
 * is saved under the address with that query, so a picture asked for at another width is not
 * answered with the saved one, except with no connection.
 * A new version fills its own cache, taking unchanged files from the old one without
 * fetching them, and only then drops the old one: a deck that was ready offline stays
 * ready while a change comes in. notes.json is never saved: it is private.
 */
const VERSION = "__VERSION__";
const FILES = __FILES__;        // [[path, bytes, tag, query?], …]; path relative to the deck's folder, or from the site's root ("/…")
const SCOPE = self.registration.scope;
const ORIGIN = self.location.origin;
const PREFIX = "deck:" + SCOPE + ":";
const CACHE = PREFIX + VERSION;
const MANIFEST = SCOPE + "__manifest";
const FRESH = [SCOPE + "index.html", SCOPE + "deck.json"];
const PIECE = 2 << 20;           // a file larger than 1.5 pieces is fetched in pieces of this size
const PARTS = SCOPE + "__part/";
const abs = (p) => (p[0] === "/" ? ORIGIN + p : SCOPE + p);
const full = (f) => abs(f[0]) + (f[3] ? "?" + f[3] : "");        // the address a file is fetched from and saved under
const BY = new Map(FILES.map((f) => [abs(f[0]), f]));            // by address without query
let filling = null, order = null;
let waiting = 0;                 // requests of the page that are on the network now
let current = null;              // the fill's download of a whole file: {url, done}
let last = null;                 // the progress last told
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => ev.waitUntil(self.clients.claim()));

function tell(msg) {
  msg.type = "deck:offline";
  last = msg;
  return self.clients.matchAll().then((cs) => cs.forEach((c) => c.postMessage(msg)));
}
function ranked() {              // FILES in the order to fetch: as the page says, then the rest, movies last
  const at = new Map((order || []).map((p, i) => [p, i]));
  const rank = (f) => (/^media\//.test(f[0]) ? 2e6 : 0) + (at.has(f[0]) ? at.get(f[0]) : /^(assets|media)\//.test(f[0]) ? 1e6 : -1);
  return FILES.map((f, i) => [rank(f), i, f]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
}
async function quiet() { while (waiting > 0) await sleep(250); }
async function download(f, cache, progress) {   // one file of the fill, giving way to the page
  const url = full(f), size = f[1];
  if (size > 1.5 * PIECE && /^(assets|media)\//.test(f[0])) {
    // in pieces, by byte range; each piece is kept under __part/<tag>/<path>/<n> until the file is whole
    const n = Math.ceil(size / PIECE), key = (i) => PARTS + encodeURIComponent(f[2] || "") + "/" + f[0] + "/" + i, parts = [];
    let whole = null;
    for (let i = 0; i < n && !whole; i++) {
      let r = await cache.match(key(i));
      if (!r) {
        await quiet();
        r = await fetch(url, { cache: "no-store", headers: { Range: "bytes=" + i * PIECE + "-" + (Math.min(size, (i + 1) * PIECE) - 1) } });
        if (r.status === 200 && i === 0) { whole = r; break; }       // a server that does not cut files: all of it at once
        if (r.status !== 206) throw new Error(f[0] + ": " + r.status);
        // (a cache does not take a 206 as it is: its bytes go in as a plain answer)
        await cache.put(key(i), new Response(await r.blob(), { headers: { "Content-Type": r.headers.get("Content-Type") || "application/octet-stream" } }));
        r = await cache.match(key(i));
      }
      parts.push(await r.blob());
      progress(Math.min(size, (i + 1) * PIECE));
    }
    if (whole) await cache.put(url, whole);
    else {
      const type = (await cache.match(key(0))).headers.get("Content-Type") || "application/octet-stream";
      const blob = new Blob(parts, { type: type });
      if (blob.size !== size) {                                      // the file changed under us: start it over next time
        for (let i = 0; i < n; i++) await cache.delete(key(i));
        throw new Error(f[0] + ": changed while it was being saved");
      }
      await cache.put(url, new Response(blob, { headers: { "Content-Type": type, "Content-Length": String(size) } }));
    }
    for (let i = 0; i < n; i++) await cache.delete(key(i));
    return;
  }
  await quiet();
  let release;
  current = { url: url, done: new Promise((r) => { release = r; }) };
  try {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(f[0] + ": " + r.status);
    await cache.put(url, r);                             // resolves when the whole body is in
  } finally {
    current = null; release();
  }
}
async function fill() {
  const had = (await caches.keys()).filter((k) => k.startsWith(PREFIX) && k !== CACHE);
  const cache = await caches.open(CACHE);
  const olds = [];                              // [cache, {path: tag}] of earlier versions
  for (const k of had) {
    const c = await caches.open(k), m = await c.match(MANIFEST);
    if (m) olds.push([c, await m.json()]);
  }
  const total = FILES.reduce((a, f) => a + f[1], 0) || 1;
  let done = 0, added = 0;
  for (const f of ranked()) {
    const [path, bytes, tag] = f, url = full(f);
    if (!(await cache.match(url))) {
      let r = null;
      for (const [c, m] of olds) {
        if (tag && m[path] === tag && FRESH.indexOf(url) < 0) { r = await c.match(url); if (r) break; }
      }
      if (r) await cache.put(url, r);
      else {
        const at = done;
        tell({ done: Math.min(done, total - 1), total: total });
        await download(f, cache, (got) => tell({ done: Math.min(at + got, total - 1), total: total }));
        added++;
      }
    }
    done += bytes;
  }
  await cache.put(MANIFEST, new Response(JSON.stringify(Object.fromEntries(FILES.map((f) => [f[0], f[2]])))));
  await Promise.all(had.map((k) => caches.delete(k)));
  for (const r of await cache.keys()) if (r.url.startsWith(PARTS)) await cache.delete(r);     // pieces of files that have changed since
  tell({ done: total, total: total, fresh: had.length > 0 && added > 0 });
}
self.addEventListener("message", (ev) => {
  if (!ev.data || ev.data.type !== "deck:fill") return;
  if (Array.isArray(ev.data.order)) order = ev.data.order;
  if (!filling) filling = fill().catch((e) => tell({ error: e.message })).then(() => { filling = null; });
  else if (last && ev.source) ev.source.postMessage(last);       // a page that has just opened: where the fill is
  // Not the whole fill: the browser stops a worker whose event lasts minutes. The page's next deck:fill carries it on.
  ev.waitUntil(Promise.race([filling, sleep(25000)]));
});

async function saved(url, any) {               // this version's copy, else an older version's of the same file (any: of any age, at any width)
  const hit = await (await caches.open(CACHE)).match(url);
  if (hit) return hit;
  const f = BY.get(url.split("?")[0]);
  for (const k of await caches.keys()) {
    if (!k.startsWith(PREFIX) || k === CACHE) continue;
    const c = await caches.open(k);
    if (!any) {
      const m = await c.match(MANIFEST);
      if (!f || !f[2] || !m || (await m.json())[f[0]] !== f[2]) continue;
    }
    const old = await c.match(url, { ignoreSearch: !!any });
    if (old) return old;
  }
  return any ? (await caches.open(CACHE)).match(url, { ignoreSearch: true }) : null;
}
async function ranged(req, resp) {             // a movie seeks by asking for part of the file
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get("range") || "");
  if (!m || !resp) return resp;
  const blob = await resp.blob(), size = blob.size;
  const start = m[1] === "" ? Math.max(0, size - +m[2]) : +m[1];
  const end = m[1] !== "" && m[2] !== "" ? Math.min(+m[2], size - 1) : size - 1;
  if (start > end) return new Response(null, { status: 416, headers: { "Content-Range": "bytes */" + size } });
  return new Response(blob.slice(start, end + 1), { status: 206, headers: {
    "Content-Type": resp.headers.get("Content-Type") || "application/octet-stream",
    "Content-Range": "bytes " + start + "-" + end + "/" + size, "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes" } });
}
async function fromNetwork(ev, req, url) {     // a file the page needs that is not saved: fetch it, keep it, and hold the fill back meanwhile
  if (current && current.url === url) {        // the fill is fetching this very file: wait for that
    await current.done;
    const hit = await saved(url);
    if (hit) return ranged(req, hit);
  }
  waiting++;
  let held = true;
  const free = () => { if (held) { held = false; waiting--; } };
  try {
    const r = await fetch(req);
    const f = BY.get(url.split("?")[0]);
    if (r.status === 200 && f && full(f) === url && !req.headers.has("range")) {
      const keep = caches.open(CACHE).then((c) => c.put(url, r.clone())).catch(() => {}).then(free);   // settles when the body is in
      ev.waitUntil(keep);
    } else setTimeout(free, r.status === 206 ? 3000 : 0);    // part of a movie: its body streams on, give it a moment
    return r;
  } catch (e) {
    free();
    const old = await saved(url, true);
    return old ? ranged(req, old) : Response.error();
  }
}
self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;
  let url = req.url.split("#")[0], path = url.split("?")[0];
  if (path === SCOPE) path = SCOPE + "index.html";
  if (!BY.has(path)) return;
  if (!BY.get(path)[3]) url = path;            // a file saved under its plain address, whatever query a page adds (a figure's ?embed=1)
  if (FRESH.indexOf(path) >= 0) {
    ev.respondWith((async () => {
      try {
        const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 3000);
        const r = await fetch(req.mode === "navigate" ? req.url : req, { cache: "no-cache", signal: ctl.signal });
        clearTimeout(t);
        if (r.ok) return r;
        if (r.status === 404 || r.status === 403) return r;      // taken down, or no longer this visitor's: not the saved copy
      } catch (e) { /* no connection: the saved copy */ }
      return (await saved(path, true)) || fetch(req);
    })());
  } else {
    ev.respondWith(saved(url).then((hit) => hit ? ranged(req, hit) : fromNetwork(ev, req, url)));
  }
});
