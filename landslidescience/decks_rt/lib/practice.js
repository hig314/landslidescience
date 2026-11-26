/*
 * lib/practice.js — Practice: keeping what a deck's live maps fetch, so the talk can show
 * the same views with a poor connection or none. Loaded by lib/deck.js for the owner of a
 * deck on the site that has a map figure (RT.maps()). The keeping itself is done by
 * lib/mapsw.js, a service worker over the map pages; its header says what is kept and how
 * the saved files are answered. This file is the owner's side of it:
 *
 *   Practice on (☰ ▸ Practice for offline maps…)
 *       registers the worker for each map page's folder, tells it which figure the files
 *       belong to (the slide showing, else the nearest map being loaded ahead) and repeats
 *       that every 3 s (the worker keeps nothing once the page falls silent), and loads the
 *       maps again so that their first views are fetched under the worker's eye.
 *       A mark at upper left counts what is kept for the slide and the talk against the
 *       limits (SLIDE_MAX, DECK_MAX): at a limit nothing more is kept, and the mark says so.
 *   Finish
 *       the review: for each slide, what this run added, to keep or discard, and what was
 *       kept before, to discard. This is the guard against a run that wandered: nothing
 *       stays without having been looked at here, and a run left unreviewed (the window
 *       closed) is offered again the next time the deck opens.
 * Practice survives a reload (sessionStorage). When nothing is saved for any deck the
 * worker is unregistered, so the map pages are as they were.
 * window.DECK_PRACTICE: { on(), start(), finish(), review() } for lib/sitemenu.js.
 */
(function () {
  "use strict";
  var deck = window.DECK, site = deck && deck.site, RT = window.DECK_RT || {};
  if (!site || !site.owner || !RT.maps || !("serviceWorker" in navigator) || !window.caches) return;
  var BASE = (document.currentScript && document.currentScript.src || "").replace(/[^\/]*$/, "");
  var MB = 1 << 20, SLIDE_MAX = 200 * MB, DECK_MAX = 1024 * MB;
  var PRE = "maps|", MINE = PRE + site.name + "|", META = location.origin + "/__maps/n";
  var STORE = "kn-practice:" + site.name;
  var run = null, beat = 0, panel = null;
  try { run = sessionStorage.getItem(STORE); } catch (e) { /* no storage: practice ends with the page */ }

  var css = document.createElement("style");
  css.textContent =
    ".kn-pr-mark{position:fixed;left:10px;top:8px;z-index:1600;padding:4px 6px 4px 10px;border-radius:5px;background:rgba(0,0,0,.72);color:#eee;font:13px/1.5 -apple-system,'Helvetica Neue',Arial,sans-serif;max-width:70%}" +
    ".kn-pr-mark.full{background:rgba(150,70,0,.92)}body.kn-editing .kn-pr-mark{top:38px}" +
    ".kn-pr-mark button,.kn-pr button{font:inherit;margin-left:8px;padding:1px 10px;border-radius:4px;border:1px solid #777;background:#3a3a3c;color:#eee;cursor:pointer}" +
    ".kn-pr{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:1700;width:min(680px,92vw);max-height:84vh;overflow:auto;padding:18px 22px;border-radius:9px;background:#2a2a2d;color:#ddd;font:14px/1.5 -apple-system,'Helvetica Neue',Arial,sans-serif;box-shadow:0 8px 40px rgba(0,0,0,.6)}" +
    ".kn-pr h2{margin:0 0 4px;font-size:17px;color:#fff}.kn-pr p{margin:0 0 12px;color:#aaa;font-size:13px}" +
    ".kn-pr .row{padding:9px 0;border-top:1px solid #444}.kn-pr .row b{color:#fff}.kn-pr .line{display:flex;align-items:center;gap:4px;margin-top:3px}.kn-pr .line span{flex:1}" +
    ".kn-pr .new{color:#9fd49f}.kn-pr .note{color:#e0b070;font-size:13px}.kn-pr .foot{border-top:1px solid #444;padding-top:12px;text-align:right}" +
    ".kn-pr button.go{background:#2f6fd0;border-color:#2f6fd0}";
  document.head.appendChild(css);
  function mk(tag, cls, parent, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function mb(b) { return b >= 1024 * MB ? +(b / MB / 1024).toFixed(1) + " GB" : b < MB / 10 ? (b ? "under 0.1" : "0") + " MB" : (b < 10 * MB ? (b / MB).toFixed(1) : Math.round(b / MB)) + " MB"; }
  function files(n) { return n + (n === 1 ? " file" : " files"); }

  // ---- the figures and the worker ----
  function figs() { return RT.maps(); }
  // The figure files belong to now: the slide showing, else the nearest map loading ahead.
  // Each folder's worker sees only its own page's maps, so it is told the nearest figure of
  // that page (scope); without scope, the nearest of any page (for the mark).
  function target(scope) {
    var at = window.Reveal && Reveal.isReady() ? Reveal.getIndices().h : 0, d, j;
    var f = figs().filter(function (x) { return !scope || x.scope === scope; });
    for (d = 0; d <= 3; d++) {
      for (j = 0; j < f.length; j++) if (f[j].i === at + d) return f[j];
      for (j = 0; d && j < f.length; j++) if (f[j].i === at - d) return f[j];
    }
    return null;
  }
  function scopes() { var s = {}; figs().forEach(function (f) { s[f.scope] = 1; }); return Object.keys(s); }
  function active(reg) {
    return new Promise(function (res) {
      if (reg.active) return res(reg.active);
      var w = reg.installing || reg.waiting;
      w.addEventListener("statechange", function () { if (w.state === "activated") res(w); });
    });
  }
  function workers(make) {         // the map worker of each folder, registering it if asked
    return Promise.all(scopes().map(function (sc) {
      var p = make ? navigator.serviceWorker.register(BASE + "mapsw.js", { scope: sc })
                   : navigator.serviceWorker.getRegistration(sc).then(function (r) { return r && new URL(r.scope).pathname === sc ? r : null; });
      return p.then(function (r) { return r && active(r); }).then(function (w) { return w && { w: w, scope: sc }; });
    })).then(function (ws) { return ws.filter(Boolean); });
  }
  function tell(ws, on) {          // say what to keep (on false: nothing); resolves when each has answered
    return Promise.all(ws.map(function (x) {
      var w = x.w, f = on ? target(x.scope) : null;
      var msg = { type: "maps:set", deck: site.name, fig: f ? f.key : null, label: f ? "slide " + (f.i + 1) : "",
                  run: run, slideMax: SLIDE_MAX, deckMax: DECK_MAX };
      return new Promise(function (res) {
        var ch = new MessageChannel();
        ch.port1.onmessage = res;
        setTimeout(res, 2500);
        w.postMessage(msg, [ch.port2]);
      });
    }));
  }
  function push() { return workers(false).then(function (ws) { return tell(ws, !!run); }); }

  // ---- what is saved (read straight from the caches) ----
  function saved(prefix) {
    return caches.keys().then(function (ks) {
      return Promise.all(ks.filter(function (k) { return k.indexOf(prefix) === 0; }).map(function (k) {
        // one record per folder's worker (lib/mapsw.js): added up here
        return caches.open(k).then(function (c) {
          return c.keys(META, { ignoreSearch: true }).then(function (qs) {
            return Promise.all(qs.map(function (q) { return c.match(q).then(function (m) { return m.json(); }).then(function (rec) { rec.url = q.url; return rec; }); }));
          });
        }).catch(function () { return []; }).then(function (recs) {
          var p = k.split("|"), o = { name: k, fig: p[2], run: p[3], n: 0, bytes: 0, kept: recs.length > 0, missed: {}, full: "", label: "", recs: recs };
          recs.forEach(function (rec) {
            o.n += rec.n || 0; o.bytes += rec.bytes || 0; o.kept = o.kept && !!rec.kept; o.full = o.full || rec.full || ""; o.label = o.label || rec.label || "";
            Object.keys(rec.missed || {}).forEach(function (h) { o.missed[h] = (o.missed[h] || 0) + rec.missed[h]; });
          });
          return o;
        });
      }));
    });
  }
  function tidy() {                // nothing saved for any deck and no practice: the map pages get their plain selves back
    if (run) return Promise.resolve();
    return caches.keys().then(function (ks) {
      if (ks.some(function (k) { return k.indexOf(PRE) === 0; })) return;
      return Promise.all(scopes().map(function (sc) {
        return navigator.serviceWorker.getRegistration(sc).then(function (r) {
          if (r && new URL(r.scope).pathname === sc && /\/mapsw\.js$/.test((r.active || r.installing || r.waiting || {}).scriptURL || "")) return r.unregister();
        });
      }));
    });
  }

  // ---- the mark ----
  var mark = null, text = null;
  function draw() {
    if (!run) { if (mark) mark.remove(); mark = null; return; }
    if (!mark) {
      mark = mk("div", "kn-pr-mark", document.body);
      text = mk("span", "", mark);
      mk("button", "", mark, "Finish").onclick = finish;
    }
    saved(MINE).then(function (list) {
      if (!mark) return;
      var f = target(), all = 0, mine = 0, n = 0, full = "";
      list.forEach(function (c) {
        all += c.bytes;
        if (f && c.fig === f.key) { mine += c.bytes; if (c.run === run) { n = c.n; full = c.full; } }
      });
      var t = "Practice · ";
      if (!f) t += "no map near this slide";
      else if (full === "slide") t += "slide " + (f.i + 1) + " has reached " + mb(SLIDE_MAX) + ": nothing more is being kept for it";
      else if (full === "deck") t += "the talk has reached " + mb(DECK_MAX) + ": nothing more is being kept";
      else t += "slide " + (f.i + 1) + ": " + files(n) + " kept this run, " + mb(mine) + " of " + mb(SLIDE_MAX);
      text.textContent = t + " · talk " + mb(all) + " of " + mb(DECK_MAX);
      mark.classList.toggle("full", !!full);
    });
  }

  // ---- what the worker never saw ----
  // A script, style sheet or picture the browser still had in memory is handed to the page
  // without a request, so the worker cannot keep it. While Practice is on, each map's page is
  // looked over for such files, and any that is not saved is fetched from inside that page
  // (a request the worker does see and keep).
  var swept = {};
  function sweep() {
    var maps = {};
    figs().forEach(function (f) { maps[f.i] = 1; });
    Array.prototype.forEach.call(document.querySelectorAll("iframe.kn-live[src]"), function (fr) {
      var sec = fr.closest("section"), W, urls = [];
      if (!sec || !maps[+sec.getAttribute("data-i")]) return;
      try {
        W = fr.contentWindow;
        Array.prototype.forEach.call(W.document.querySelectorAll("script[src], link[rel~=stylesheet][href], img[src]"), function (n) { urls.push(n.src || n.href); });
        W.performance.getEntriesByType("resource").forEach(function (e) { if (/^(script|link|css|img)$/.test(e.initiatorType)) urls.push(e.name); });
      } catch (e) { return; }
      urls.filter(function (u) { return /^https?:/.test(u) && !swept[u]; }).slice(0, 30).forEach(function (u) {
        swept[u] = 1;
        var key = u.split("#")[0];
        caches.keys().then(function (ks) {
          return Promise.all(ks.filter(function (k) { return k.indexOf(PRE) === 0; }).map(function (k) { return caches.match(key, { cacheName: k }); }));
        }).then(function (hits) {
          if (hits.some(Boolean) || !run) return;
          var own = new URL(u).origin === location.origin;
          return W.fetch(u, own ? {} : { mode: "no-cors", credentials: "omit" });
        }).catch(function () {});
      });
    });
  }

  // ---- on and off ----
  function reloadMaps() {          // the maps already loaded fetched their first view unseen: load them again
    var keys = {};
    figs().forEach(function (f) { keys[f.i] = 1; });
    Array.prototype.forEach.call(document.querySelectorAll("iframe.kn-live[src]"), function (fr) {
      var sec = fr.closest("section");
      if (!sec || !keys[+sec.getAttribute("data-i")]) return;
      try { fr.contentWindow.location.reload(); } catch (e) { fr.src = fr.getAttribute("src"); }
    });
  }
  function go(fresh) {
    try { sessionStorage.setItem(STORE, run); } catch (e) { /* see above */ }
    clearInterval(beat);
    beat = setInterval(function () { if (run) { push(); draw(); sweep(); } }, 3000);
    draw();
    return workers(true).then(function (ws) { return tell(ws, true); }).then(function () { if (fresh) reloadMaps(); });
  }
  function start() {
    if (run) return Promise.resolve();
    run = String(Date.now());
    return go(true).catch(function (e) { run = null; clearInterval(beat); draw(); throw new Error("Practice could not start: " + e.message); });
  }
  function finish() {
    if (!run) return review();
    clearInterval(beat);
    swept = {};
    var ws;
    return workers(false).then(function (w) { ws = w; run = null; return tell(ws, false); }).then(function () {
      try { sessionStorage.removeItem(STORE); } catch (e) { /* see above */ }
      draw();
      return review();
    });
  }
  if (window.Reveal) Reveal.on("slidechanged", function () { if (run) { push(); draw(); } });

  // ---- the review ----
  function review() {
    if (panel) panel.remove();
    panel = mk("div", "kn-pr", document.body);
    mk("h2", "", panel, "Saved map files");
    var intro = mk("p", "", panel, "");
    var body = mk("div", "", panel), foot = mk("div", "foot", panel);
    function close() { if (panel) panel.remove(); panel = null; tidy(); notice(); }
    function fill() {
      saved(MINE).then(function (list) {
        // a run that kept nothing is not worth a line
        var empty = list.filter(function (c) { return !c.kept && !c.n && !Object.keys(c.missed).length; });
        list = list.filter(function (c) { return empty.indexOf(c) < 0; });
        return Promise.all(empty.map(function (c) { return caches.delete(c.name); })).then(function () { return list; });
      }).then(function (list) {
        body.textContent = ""; foot.textContent = "";
        var by = {}, order = [], where = {}, fresh = [];
        figs().forEach(function (f) { where[f.key] = f.i + 1; });
        list.forEach(function (c) { if (!by[c.fig]) { by[c.fig] = []; order.push(c.fig); } by[c.fig].push(c); if (!c.kept) fresh.push(c); });
        order.sort(function (a, b) { return (where[a] || 1e9) - (where[b] || 1e9); });
        intro.textContent = !list.length ? "Nothing is saved for this talk's maps in this browser. Switch Practice on (☰) and go through the talk as you will give it."
          : "What the maps on your slides fetched while Practice was on, kept in this browser. During the talk these files are used first, " +
            "so the views you practised appear without a connection. Up to " + mb(SLIDE_MAX) + " a slide and " + mb(DECK_MAX) + " a talk.";
        order.forEach(function (fig) {
          var row = mk("div", "row", body), kept = by[fig].filter(function (c) { return c.kept; }), neu = by[fig].filter(function (c) { return !c.kept; });
          var sum = function (cs, k) { return cs.reduce(function (a, c) { return a + c[k]; }, 0); };
          mk("b", "", row, where[fig] ? "Slide " + where[fig] : "A map no longer in this talk" + (by[fig][0].label ? " (was " + by[fig][0].label + ")" : ""));
          function line(cls, label, cs, keepable) {
            var l = mk("div", "line " + cls, row);
            mk("span", "", l, label + files(sum(cs, "n")) + ", " + mb(sum(cs, "bytes")));
            if (keepable) mk("button", "go", l, "Keep").onclick = function () { keep(cs).then(fill); };
            mk("button", "", l, "Discard").onclick = function () { drop(cs).then(fill); };
          }
          if (kept.length) line("", "Kept: ", kept, false);
          if (neu.length) line("new", "This run added: ", neu, true);
          var missed = {}, full = "";
          by[fig].forEach(function (c) { full = full || c.full; Object.keys(c.missed).forEach(function (h) { missed[h] = (missed[h] || 0) + c.missed[h]; }); });
          if (full) mk("div", "note", row, full === "slide" ? "This slide reached its limit during a run: what it fetched after that was not kept."
            : "The talk reached its limit during a run: what this slide fetched after that was not kept.");
          Object.keys(missed).forEach(function (h) {
            mk("div", "note", row, missed[h] + " from " + h + " could not be kept (that host does not allow it, or the file was too large): those need a connection.");
          });
        });
        if (fresh.length > 1 || (fresh.length && order.length > 1)) mk("button", "go", foot, "Keep all new").onclick = function () { keep(fresh).then(fill); };
        mk("button", "", foot, fresh.length ? "Decide later" : "Close").onclick = close;
      });
    }
    function keep(cs) {
      return Promise.all(cs.map(function (c) {
        return caches.open(c.name).then(function (cache) {
          return Promise.all(c.recs.map(function (rec) {
            var url = rec.url; delete rec.url; rec.kept = true;
            return cache.put(url, new Response(JSON.stringify(rec), { headers: { "Content-Type": "application/json" } }));
          }));
        });
      }));
    }
    function drop(cs) { return Promise.all(cs.map(function (c) { return caches.delete(c.name); })); }
    fill();
    return Promise.resolve();
  }
  document.addEventListener("keydown", function (ev) {
    if (panel && ev.key === "Escape") { panel.remove(); panel = null; tidy(); notice(); ev.stopImmediatePropagation(); }
  }, true);

  // ---- on load ----
  var note = null;
  function notice() {              // a run nobody reviewed (the window was closed during Practice)
    if (note) note.remove();
    note = null;
    if (run) return;
    saved(MINE).then(function (list) {
      if (run || panel || !list.some(function (c) { return !c.kept && c.n; })) return;
      note = mk("div", "kn-pr-mark", document.body);
      mk("span", "", note, "A practice run's map files have not been reviewed");
      mk("button", "", note, "Review").onclick = function () { note.remove(); note = null; review(); };
    });
  }
  if (run) go(false).catch(function () {});
  else {
    notice();
    scopes().forEach(function (sc) {         // a newer worker, if the site has one
      navigator.serviceWorker.getRegistration(sc).then(function (r) { if (r && new URL(r.scope).pathname === sc) r.update().catch(function () {}); });
    });
  }
  window.DECK_PRACTICE = { on: function () { return !!run; }, start: start, finish: finish, review: review,
                           limits: "up to " + mb(SLIDE_MAX) + " a slide and " + mb(DECK_MAX) + " a talk" };
})();
