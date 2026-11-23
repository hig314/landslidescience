/*
 * lib/deck-embed.js — the figure's half of the deck ↔ live figure protocol. No dependencies.
 * A page that is to be shown on a slide includes this file and calls DeckEmbed.init once:
 *
 *   DeckEmbed.init({
 *     getState: function () { return "map=9/60.1/-149.4&base=…"; },   // the whole view, as one string
 *     setState: function (state, opts) { … },      // show that view; opts.animate: travel there or cut
 *     steps: 7, step: function (n, replay) { … }   // optional: the figure's own numbered stages
 *   });
 *   DeckEmbed.ready();        // once, when the figure can be shown and will obey setState
 *   DeckEmbed.changed();      // whenever the view has changed (calls are coalesced)
 *
 * DeckEmbed.embedded is true when the page is in a frame; use it to hide page chrome and to
 * leave alone anything a slide should not touch (saved views, the address bar).
 *
 * Messages (window.postMessage, every one an object with a "type"):
 *   deck → figure   deck:state {state, animate}    show this view
 *                   deck:step  {step, replay}      show stage n (1-based); replay = arrived going forwards
 *                   deck:get   {}                  reply with deck:state (the editor's "capture this view")
 *   figure → deck   deck:ready {steps}             loaded; the deck then sends the slide's current step
 *                   deck:state {state}             the view now (after a change, or in answer to deck:get)
 *                   deck:key   {key}               a clicker key pressed while the figure had focus
 *
 * The state is a string the figure defines, by convention its URL hash without the "#", so a
 * view captured on a slide is also a link that opens the page there. The deck stores it as is.
 * Only views anyone may see belong in it: decks are published.
 *
 * Messages are accepted from the parent window only. Outgoing ones carry no secrets (a view
 * of a public page), so they are posted without naming the parent's origin.
 */
(function () {
  "use strict";
  var KEYS = { ArrowRight: 1, ArrowLeft: 1, PageDown: 1, PageUp: 1 };
  var api = null, pending = 0, isReady = false;

  function post(type, body) {
    if (!DeckEmbed.embedded) return;
    var m = body || {};
    m.type = type;
    window.parent.postMessage(m, "*");
  }
  function typing(t) {
    return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
  }

  var DeckEmbed = {
    embedded: window.parent !== window,
    init: function (a) {
      api = a || {};
      if (!DeckEmbed.embedded) return;
      window.addEventListener("message", function (ev) {
        var d = ev.data;
        if (ev.source !== window.parent || !d || typeof d.type !== "string") return;
        if (d.type === "deck:state" && api.setState && typeof d.state === "string") api.setState(d.state, { animate: d.animate !== false });
        else if (d.type === "deck:step" && api.step) api.step(+d.step || 1, !!d.replay);
        else if (d.type === "deck:get" && api.getState) post("deck:state", { state: api.getState(), asked: true });
      });
      // Clicker keys belong to the deck. Capture phase, so the figure's own handlers (a map
      // panning on the arrow keys) do not also act on them.
      window.addEventListener("keydown", function (ev) {
        if (!KEYS[ev.key] || ev.metaKey || ev.ctrlKey || ev.altKey || typing(ev.target)) return;
        ev.preventDefault(); ev.stopPropagation();
        post("deck:key", { key: ev.key });
      }, true);
      if (isReady) DeckEmbed.ready();
    },
    ready: function () {
      isReady = true;
      if (api) post("deck:ready", { steps: api.steps || 0 });
    },
    changed: function () {
      if (!DeckEmbed.embedded || !api || !api.getState || pending) return;
      pending = setTimeout(function () { pending = 0; post("deck:state", { state: api.getState() }); }, 150);
    }
  };
  window.DeckEmbed = DeckEmbed;
})();
