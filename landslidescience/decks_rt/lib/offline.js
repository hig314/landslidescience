/*
 * lib/offline.js — loaded by lib/deck.js in a published deck only. Registers the deck's
 * service worker (sw.js, written by tools/publish.py next to index.html), which keeps every
 * file of the deck in this browser so the talk runs with no connection, and shows a small
 * mark while that happens: "Saving for offline 42%", then "Ready offline" for a few seconds.
 * A failure stays on screen and is retried every 15 s (six times), then stays until a reload.
 * O shows the mark again. Needs https or localhost; without either the deck works online only.
 */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;
  var mark = document.createElement("div"), hide = 0;
  mark.style.cssText = "position:fixed;right:10px;bottom:8px;z-index:2000;padding:3px 9px;border-radius:4px;" +
    "background:rgba(0,0,0,.6);color:#ddd;font:12px/1.4 -apple-system,Arial,sans-serif;pointer-events:none;transition:opacity .4s;opacity:0";
  document.body.appendChild(mark);
  var last = "Not saved for offline yet", tries = 0;
  function show(text, stay) {
    last = text; mark.textContent = text; mark.style.opacity = 1;
    clearTimeout(hide);
    if (!stay) hide = setTimeout(function () { mark.style.opacity = 0; }, 4000);
  }
  navigator.serviceWorker.addEventListener("message", function (ev) {
    var d = ev.data || {};
    if (d.type !== "deck:offline") return;
    if (d.error) {                 // a file did not arrive (the server restarting, the network dropping): try again, and say so until it works
      if (tries++ < 6) { show("Not all saved for offline yet (" + d.error + "). Trying again…", true); setTimeout(fill, 15000); }
      else show("Not saved for offline: " + d.error + ". Reload the page to try again.", true);
    }
    else if (d.done < d.total) show("Saving for offline " + Math.floor(100 * d.done / d.total) + "%", true);
    else show(d.fresh ? "Ready offline. A newer version was saved: reload to see it" : "Ready offline", d.fresh);
  });
  function fill() {
    var sw = navigator.serviceWorker.controller;
    if (sw) sw.postMessage({ type: "deck:fill" });
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
