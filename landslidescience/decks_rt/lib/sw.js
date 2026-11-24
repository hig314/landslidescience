/*
 * lib/sw.js — the service worker of a deck on the web. It is served next to the deck's
 * index.html with VERSION and FILES filled in (by the site for /decks/<name>/, or by
 * tools/publish.py for a static copy); its scope is that deck's folder.
 *
 * It keeps every file of the deck in a cache named for this version, filled in the
 * background (deck:fill from the page; progress is posted back as deck:offline). Requests:
 *   the page and deck.json   network first (3 s), so a changed deck shows at once when
 *                            there is a connection; the saved copy otherwise
 *   every other deck file    the saved copy first; byte ranges (movies) are cut from it. A copy saved
 *                            by an earlier version is used only if the file has not changed since
 *                            (or there is no connection): a changed figure must not show its old self
 *   anything else            untouched: maps in frames belong to other pages, and saves
 *                            from the editor are not GETs
 * A new version fills its own cache, taking unchanged files from the old one without
 * fetching them, and only then drops the old one: a deck that was ready offline stays
 * ready while a change comes in. notes.json is never saved: it is private.
 */
const VERSION = "__VERSION__";
const FILES = __FILES__;        // [[path, bytes, tag], …]; path relative to the deck's folder, or from the site's root ("/…")
const SCOPE = self.registration.scope;
const ORIGIN = self.location.origin;
const PREFIX = "deck:" + SCOPE + ":";
const CACHE = PREFIX + VERSION;
const MANIFEST = SCOPE + "__manifest";
const FRESH = [SCOPE + "index.html", SCOPE + "deck.json"];
const abs = (p) => (p[0] === "/" ? ORIGIN + p : SCOPE + p);
const KNOWN = new Set(FILES.map((f) => abs(f[0])));
const TAGS = new Map(FILES.map((f) => [abs(f[0]), [f[0], f[2]]]));
let filling = null;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => ev.waitUntil(self.clients.claim()));

function tell(msg) {
  msg.type = "deck:offline";
  return self.clients.matchAll().then((cs) => cs.forEach((c) => c.postMessage(msg)));
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
  for (const [path, bytes, tag] of FILES) {
    const url = abs(path);
    if (!(await cache.match(url))) {
      let r = null;
      for (const [c, m] of olds) {
        if (tag && m[path] === tag && FRESH.indexOf(url) < 0) { r = await c.match(url); if (r) break; }
      }
      if (!r) {
        r = await fetch(url, { cache: "no-cache" });
        if (!r.ok) throw new Error(path + ": " + r.status);
        added++;
        tell({ done: Math.min(done, total - 1), total: total });
      }
      await cache.put(url, r);
    }
    done += bytes;
  }
  await cache.put(MANIFEST, new Response(JSON.stringify(Object.fromEntries(FILES.map((f) => [f[0], f[2]])))));
  await Promise.all(had.map((k) => caches.delete(k)));
  tell({ done: total, total: total, fresh: had.length > 0 && added > 0 });
}
self.addEventListener("message", (ev) => {
  if (!ev.data || ev.data.type !== "deck:fill") return;
  if (!filling) filling = fill().catch((e) => tell({ error: e.message })).then(() => { filling = null; });
  ev.waitUntil(filling);
});

async function saved(url, any) {               // this version's copy, else an older version's of the same file (any: of any age)
  const hit = await (await caches.open(CACHE)).match(url);
  if (hit) return hit;
  const [path, tag] = TAGS.get(url) || [];
  for (const k of await caches.keys()) {
    if (!k.startsWith(PREFIX) || k === CACHE) continue;
    const c = await caches.open(k);
    if (!any) {
      const m = await c.match(MANIFEST);
      if (!tag || !m || (await m.json())[path] !== tag) continue;
    }
    const old = await c.match(url);
    if (old) return old;
  }
  return null;
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
self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;
  let url = req.url.split(/[?#]/)[0];
  if (url === SCOPE) url = SCOPE + "index.html";
  if (!KNOWN.has(url)) return;
  if (FRESH.indexOf(url) >= 0) {
    ev.respondWith((async () => {
      try {
        const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 3000);
        const r = await fetch(req.mode === "navigate" ? req.url : req, { cache: "no-cache", signal: ctl.signal });
        clearTimeout(t);
        if (r.ok) return r;
        if (r.status === 404 || r.status === 403) return r;      // taken down, or no longer this visitor's: not the saved copy
      } catch (e) { /* no connection: the saved copy */ }
      return (await saved(url, true)) || fetch(req);
    })());
  } else {
    ev.respondWith(saved(url).then((hit) => hit ? ranged(req, hit) :
      fetch(req).catch(() => saved(url, true).then((old) => old ? ranged(req, old) : Response.error()))));
  }
});
