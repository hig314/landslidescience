/*
 * lib/offline.js — loaded by lib/deck.js in a published deck only. Registers the deck's
 * service worker (sw.js, written by tools/publish.py next to index.html), which keeps every
 * file of the deck in this browser so the talk runs with no connection, and shows a small
 * mark while that happens: "Saving for offline 42%", then "Ready offline" for a few seconds
 * (with the slides whose live maps still need a connection).
 * A failure stays on screen and the saving is tried again every 20 s, going on from what it has.
 * O shows the mark again. Needs https or localhost; without either the deck works online only.
 */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;
  var mark = document.createElement("div"), hide = 0;
  mark.style.cssText = "position:fixed;right:10px;bottom:8px;z-index:2000;padding:3px 9px;border-radius:4px;" +
    "background:rgba(0,0,0,.6);color:#ddd;font:12px/1.4 -apple-system,Arial,sans-serif;pointer-events:none;transition:opacity .4s;opacity:0";
  document.body.appendChild(mark);
  var last = "Not saved for offline yet";
  function show(text, stay) {
    last = text; mark.textContent = text; mark.style.opacity = 1;
    clearTimeout(hide);
    if (!stay) hide = setTimeout(function () { mark.style.opacity = 0; }, 4000);
  }
  navigator.serviceWorker.addEventListener("message", function (ev) {
    var d = ev.data || {};
    if (d.type !== "deck:offline") return;
    if (d.error) show("Not all saved for offline yet (" + d.error + "). Trying again…", true);   // the server restarting, the connection dropping: the next deck:fill goes on from what is saved
    else if (d.done < d.total) show("Saving for offline " + Math.floor(100 * d.done / d.total) + "%", true);
    else if ((finished = true)) scan().then(function () { show((d.fresh ? "Ready offline. A newer version was saved: reload to see it" : "Ready offline") + needs(), d.fresh); });
  });
  // The fill starts when the slides near the one showing have their pictures (deck.js calls
  // RT.whenIdle), and is told the order the talk will want the files in: from the slide
  // showing to the end, then the slides before it.
  var RT = window.DECK_RT || {}, ready = false, asked = false, finished = false;
  setInterval(function () { if (asked && ready && !finished) fill(); }, 20000);     // see lib/sw.js: keeps the fill going, and tries again after a failure
  function order() {
    var d = window.DECK || { slides: [] }, at = window.Reveal && Reveal.isReady() ? Reveal.getIndices().h : 0, out = [], seen = {};
    d.slides.slice(at).concat(d.slides.slice(0, at)).forEach(function (s) {
      (s.elements || []).forEach(function (e) {
        [e.src, e.fallback].forEach(function (p) { if (typeof p === "string" && /^(assets|media)\//.test(p) && !seen[p]) { seen[p] = 1; out.push(p); } });
      });
    });
    return out;
  }
  function fill() {
    asked = true;
    var sw = navigator.serviceWorker.controller;
    if (sw && ready) sw.postMessage({ type: "deck:fill", order: order() });
  }
  RT.whenIdle = function () { ready = true; if (asked) fill(); };
  if (RT.idle) ready = true;
  // What no saved copy covers: figures that are other pages. A practice run (lib/practice.js)
  // may have kept what a map fetched; kept[] holds the figures that have such files.
  var kept = {};
  function scan() {
    if (!window.caches || !RT.maps) return Promise.resolve();
    var pre = "maps|" + ((window.DECK || {}).site || {}).name + "|";
    return caches.keys().then(function (ks) {
      kept = {};
      return Promise.all(ks.filter(function (k) { return k.indexOf(pre) === 0; }).map(function (k) {
        return caches.open(k).then(function (c) { return c.matchAll(location.origin + "/__maps/n", { ignoreSearch: true }); }).then(function (ms) {
          return Promise.all(ms.map(function (m) { return m.json(); }));
        }).then(function (recs) {
          if (recs.some(function (rec) { return rec.kept && rec.n; })) kept[k.split("|")[2]] = 1;
        });
      }));
    }).catch(function () {});
  }
  scan();
  function needs() {
    var maps = RT.maps ? RT.maps() : [], key = {}, bare = [], some = [];
    maps.forEach(function (f) { key[f.i] = f.key; });
    ((window.DECK || {}).slides || []).forEach(function (s, i) {
      if (!(s.elements || []).some(function (e) { return e.type === "live" && !/^live\//.test(e.src || ""); })) return;
      (kept[key[i]] ? some : bare).push(i + 1);
    });
    function on(at) { return (at.length > 1 ? "s on slides " : " on slide ") + at.join(", "); }
    return (some.length ? ". The live map" + on(some) + (some.length > 1 ? " show" : " shows") + " the views a practice run saved" : "") +
           (bare.length ? ". The live map" + on(bare) + (bare.length > 1 ? " need" : " needs") + " a connection" : "");
  }
  navigator.serviceWorker.register("sw.js").then(function () { return navigator.serviceWorker.ready; }).then(function () {
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist();   // ask the browser not to evict it
    if (navigator.serviceWorker.controller) fill();
    navigator.serviceWorker.addEventListener("controllerchange", fill);
  }).catch(function (e) { show("Offline copy unavailable: " + e.message); });
  document.addEventListener("keydown", function (ev) {
    if ((ev.key === "o" || ev.key === "O") && !ev.metaKey && !ev.ctrlKey && !ev.altKey) show(last);
  });
})();
