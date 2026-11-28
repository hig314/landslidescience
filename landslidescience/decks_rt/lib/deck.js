/*
 * lib/deck.js — renders decks/<name>/deck.json on reveal.js used as a plain stage (deck.width × deck.height: 1920 × 1080, or 1920 × 1440 for a 4:3 screen).
 *
 * deck.json:
 *   { title, width, height, slides: [ slide, … ] }      (published: true in a copy made by tools/publish.py;
 *                                                         site: {name, state, owner, edit} when served by the site, see below)
 *   slide: { kn, bg, notes, dissolve, panzoom, flat: [stage images], use: "elements" | "flat",
 *            check: [what the importer could not reproduce], owner, builds: [ row, … ], elements: [ element, … ] }
 *   element: frame x, y, w, h in slide pixels; optional rot (degrees), op (0–100), id (what a build row names),
 *            group (elements of a slide with the same group are one thing to the editor and to a build row),
 *            build (the importer's form: the click at which it appears; 1 or absent = with the slide)
 *   builds:  the slide's build order, as in Keynote: rows that run in order.
 *            row: { el: element id | grp: group, do: "in" | "out" | "step", para, start, delay, fx, dur }
 *              in / out   the element (or its paragraph number para, from 0) appears / goes away. What has an
 *                         "in" row as its first row is absent until then.
 *              step       the live figure moves on one stage
 *              start      "click" (default) | "with" (when the row before starts) | "after" (when it ends);
 *                         rows before the first "click" row run when the slide arrives
 *              delay      seconds before the row starts
 *              fx, dur    "dissolve" over dur seconds (default 1); absent = at once. On a step row dur is how
 *                         long the figure plays that stage: it is sent to the figure with the step (a figure
 *                         that animates in its own time draws at that pace; absent = its own pace), and a
 *                         following "after" row waits that long. Stage 1 has no row: the live element's dur.
 *            Absent: the order is read from the elements' build / paraBuilds and the figure's stages (builds
 *            first, one click each), which is what the importer writes. The editor writes builds.
 *     text   { text, font, weight, italic, size, color, align, valign, lineHeight, paraGap, indent, tracking, fill,
 *              markers, markerIndent, paraBuilds, runs: [{t, …overrides}] }
 *                                             lineHeight, paraGap (extra space between paragraphs), indent in pixels; tracking in em;
 *                                             runs, when present, carry the text (mixed styles).
 *                                             Lists: markers has one string per paragraph ("•", "1.", "" for none), drawn
 *                                             at the paragraph's left with its text markerIndent pixels further in.
 *                                             paraBuilds: the click at which each paragraph appears (as build).
 *     image  { src, fit: [ix, iy, iw, ih], flip: "h" | "v" }
 *                                             fit = the whole image's rectangle relative to the
 *                                             frame, which crops it; absent = fills the frame
 *     video  { src, autoplay }
 *            text also: fill (a background colour), fillOp (its opacity, percent), glow (a colour: a halo
 *                       round the letters, to set text over a picture), glowSize (px, default 8), glowStrength (1-6)
 *     shape  { shape: rect | oval | path, fill, fillOp, stroke, strokeW, strokeOp, dash: dash | dot,
 *              corner, radius, pts, closed, arrow: end | start | both }
 *                                             drawn as SVG (drawShape). fill / stroke: a colour, absent = none;
 *                                             fillOp / strokeOp: opacity in percent (absent = 100); strokeW in px
 *                                             (default 4). A box's or oval's line lies inside its frame. corner: a
 *                                             box's corner radius in px; radius (from the importer): in percent of
 *                                             the frame, 50 = an oval. A path's pts are [fx, fy] fractions of the
 *                                             frame (so it scales with it); its line and arrowheads may reach past it.
 *     live   { src, steps | states, fallback }
 *                                             another page in an iframe, stepped by the clicker. states: one view
 *                                             string per click, sent to the page (which includes lib/deck-embed.js);
 *                                             steps: the page's own numbered stages; fallback: an image shown until
 *                                             the page reports ready
 *
 * use "flat" shows Keynote's own renders (one image per build stage) instead of the elements.
 * K on a slide lays the flat render over the elements (off → on → half) to compare the two.
 * E opens the editor (lib/editor.js): move, resize and retype elements in place; saved to
 * deck.json through tools/serve.py.
 *
 * On the site (landslidescience.org/decks/<name>/) the server adds "site" to the deck.json it sends:
 * state "draft" | "published"; owner = this visitor is the deck's owner; edit = it may be edited here
 * (the owner's drafts only: a published deck is constant, and is revised through a copy). The owner's
 * notes come separately (notes.json) and are merged in before anything is drawn, so that a save
 * from the editor carries them. Saves send window.DECK_CSRF as X-CSRFToken.
 *
 * dissolve: seconds; on leaving the slide forwards, a copy of it stays on top of the next
 *           slide and fades out (a true cross-dissolve). Everything else is a cut.
 * panzoom:  {from: [scale, cx, cy], to: […], dur, on: "show" | "click"}; cx, cy are the
 *           fractions of the frame to centre on.
 * live:     the protocol is in lib/deck-embed.js. Each click posts deck:state (or deck:step); clicker
 *           keys pressed inside the page come back as deck:key.
 */
(function () {
  "use strict";
  var W = 1920, H = 1080, deck = null;
  var BASE = (document.currentScript && document.currentScript.src || "").replace(/[^\/]*$/, "");   // lib/
  var RT = window.DECK_RT = { editing: false, blobs: {} };      // what lib/editor.js works through
  var slidesEl = document.querySelector(".reveal .slides");

  function el(tag, cls, parent) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (parent) parent.appendChild(n);
    return n;
  }
  function family(f) { return '"' + f + '", "Helvetica Neue", Helvetica, Arial, sans-serif'; }

  // ---- loading ----
  // Pictures are fetched in the order the talk needs them, not all at once: the slide showing
  // first, then the next, the one before, and so on to AHEAD slides on; one slide at a time, so
  // that on a weak connection the slide on screen never waits behind pictures for later. Only
  // when those are in is the offline copy filled (RT.whenIdle, lib/offline.js), and it gives way
  // again whenever the page asks for something (lib/sw.js). A picture that fails is asked for
  // again, at longer intervals, for as long as it is on a slide. Movies: see near().
  // On the site a file is asked for as deck.json's site.files says ("v=…&w=…": its version, so
  // the browser may keep it for good, and the width of the display copy to send).
  var AHEAD = [0, 1, -1, 2, 3, -2], busy = null, idle = false, mark = null, markTimer = 0;
  function url(p) {
    if (RT.blobs[p]) return RT.blobs[p];   // a picture added offline, not on the server yet (see "this device's copy")
    var q = deck && deck.site && deck.site.files && deck.site.files[p];
    return q ? p + "?" + q : p;
  }
  function fetchInto(n, done) {          // start one picture; done() when it has arrived, or failed for the first time
    var p = n.getAttribute("data-kn-src"), tries = 0, told = false;
    if (p == null) { if (done) done(); return; }
    n.removeAttribute("data-kn-src");
    n.knPath = p; n.knPending = true;
    function tell() { n.knPending = false; if (!told) { told = true; shown(); if (done) done(); } }
    n.addEventListener("load", function () { n.knFailed = false; tell(); });
    n.addEventListener("error", function () {
      n.knFailed = true; tell();
      var u = n.getAttribute("src");
      setTimeout(function () {             // again, unless it was given another address or left the slide
        if (n.isConnected && n.knFailed && n.getAttribute("src") === u) { n.removeAttribute("src"); n.src = u; }
      }, Math.min(30000, 2000 * Math.pow(2, tries++)));
    });
    n.src = url(p);
  }
  function fetchSlide(sec, done) {       // every picture of a slide that has not been asked for yet
    var ns = sec ? Array.prototype.slice.call(sec.querySelectorAll("[data-kn-src]")) : [], left = ns.length;
    if (!left) { if (done) done(); return 0; }
    ns.forEach(function (n) { fetchInto(n, function () { if (!--left && done) done(); }); });
    return ns.length;
  }
  function pump() {                      // the next slide near the one showing that still has pictures to fetch
    if (busy || !window.Reveal || !Reveal.isReady()) return;
    var i = Reveal.getIndices().h, secs = slidesEl.querySelectorAll(":scope > section"), sec = null;
    for (var k = 0; k < AHEAD.length && !sec; k++) {
      var c = secs[i + AHEAD[k]];
      if (c && c.querySelector("[data-kn-src]")) sec = c;
    }
    if (!sec) {
      if (!idle) { idle = true; RT.idle = true; if (RT.whenIdle) RT.whenIdle(); }
      return;
    }
    busy = sec;
    fetchSlide(sec, function () { busy = null; pump(); });
  }
  function near() {                      // on arriving at a slide
    var cur = Reveal.getCurrentSlide(), i = Reveal.getIndices().h;
    Array.prototype.forEach.call(cur.querySelectorAll("img"), function (n) { n.setAttribute("fetchpriority", "high"); });
    fetchSlide(cur);                       // at once, whatever else is on its way
    // a movie starts to arrive when its slide is the next one; before that not a byte of it is asked for
    Array.prototype.forEach.call(slidesEl.querySelectorAll(":scope > section"), function (sec, j) {
      if (Math.abs(j - i) <= 1) Array.prototype.forEach.call(sec.querySelectorAll("video"), function (v) { if (v.preload !== "auto") v.preload = "auto"; });
    });
    shown();
    pump();
  }
  function shown() {                     // "Loading…" while the slide on screen waits for a picture (after a moment: most arrive at once)
    if (!window.Reveal || !Reveal.isReady()) return;
    var cur = Reveal.getCurrentSlide();
    var waiting = !!cur && Array.prototype.some.call(cur.querySelectorAll("img"), function (n) { return n.knPending || n.knFailed; });
    if (!waiting) { clearTimeout(markTimer); markTimer = 0; if (mark) mark.style.opacity = 0; return; }
    if (markTimer || (mark && mark.style.opacity === "1")) return;
    markTimer = setTimeout(function () {
      markTimer = 0;
      if (!mark) { mark = el("div", "kn-loading", document.body); mark.textContent = "Loading…"; void mark.offsetWidth; }
      mark.style.opacity = 1;
    }, 700);
  }
  RT.url = url;
  RT.files = function (files) {          // after a save: a picture that is now shown larger is asked for again, larger
    if (!deck.site || !files) return;
    deck.site.files = files;
    Array.prototype.forEach.call(slidesEl.querySelectorAll("img"), function (n) {
      if (n.knPath != null && n.getAttribute("src") && n.getAttribute("src") !== url(n.knPath)) n.src = url(n.knPath);
    });
  };
  RT.fetchAll = function (root) { Array.prototype.forEach.call(root.querySelectorAll("[data-kn-src]"), function (n) { fetchInto(n); }); };

  // ---- building a slide from its elements ----
  function frame(node, e) {
    node.style.left = e.x + "px"; node.style.top = e.y + "px";
    node.style.width = e.w + "px"; node.style.height = e.h + "px";
    node.style.transform = e.rot ? "rotate(" + (-e.rot) + "deg)" : "";      // rot: degrees, anticlockwise (Keynote's sense)
    if (e.op != null && e.op !== 100) node.style.opacity = e.op / 100;
    return node;
  }
  function styleText(node, s) {
    if (s.font) node.style.fontFamily = family(s.font);
    if (s.weight) node.style.fontWeight = s.weight;
    if (s.italic != null) node.style.fontStyle = s.italic ? "italic" : "normal";
    if (s.size) node.style.fontSize = s.size + "px";
    if (s.color) node.style.color = s.color;
  }
  var NS = "http://www.w3.org/2000/svg", arrows = 0;
  function svgEl(tag, parent, attrs) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] != null) n.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(n);
    return n;
  }
  function rgba(c, op) {                  // a colour at an opacity in percent (absent: as it is)
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(c || "");
    if (op == null || !m) return c;
    return "rgba(" + parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," + parseInt(m[3], 16) + "," + op / 100 + ")";
  }
  function glow(e) {                       // a soft halo round the letters: the same blurred shadow, stacked to make it stronger
    var one = "0 0 " + (e.glowSize == null ? 8 : e.glowSize) + "px " + e.glow, n = Math.max(1, Math.min(6, e.glowStrength || 2)), out = [];
    while (out.length < n) out.push(one);
    return out.join(", ");
  }
  function drawShape(n, e) {               // (again after any change of frame or style: the editor calls RT.drawShape)
    n.textContent = "";
    var w = e.w, h = e.h, line = e.stroke ? (e.strokeW == null ? 4 : +e.strokeW) : 0, kind = e.shape || (e.radius >= 50 ? "oval" : "rect");
    var svg = svgEl("svg", n, { width: w, height: h, "class": "kn-svg" }), g;
    var st = { fill: e.fill || "none", "fill-opacity": e.fillOp != null ? e.fillOp / 100 : null,
               stroke: e.stroke || "none", "stroke-width": line || null, "stroke-opacity": e.strokeOp != null ? e.strokeOp / 100 : null,
               "stroke-linejoin": "round", "stroke-linecap": e.dash === "dot" || kind === "path" ? "round" : "butt" };
    if (e.dash === "dash") st["stroke-dasharray"] = 3 * line + " " + 2 * line;
    else if (e.dash === "dot") st["stroke-dasharray"] = "0.01 " + 2 * line;
    if (kind === "path") {
      var p = (e.pts || []).map(function (q) { return (q[0] * w).toFixed(1) + "," + (q[1] * h).toFixed(1); });
      st.d = p.length ? "M" + p.join("L") + (e.closed ? "Z" : "") : "";
      g = svgEl("path", svg, st);
      if (e.arrow && e.stroke && !e.closed) {   // arrowheads in the line's colour, sized by its width
        var id = "kn-ar" + (++arrows), m = svgEl("marker", svgEl("defs", svg), { id: id, viewBox: "0 0 10 10", refX: 4, refY: 5,
          markerWidth: 4, markerHeight: 4, markerUnits: "strokeWidth", orient: "auto-start-reverse" });
        svgEl("path", m, { d: "M0,0 L10,5 L0,10 z", fill: e.stroke, "fill-opacity": st["stroke-opacity"] });
        if (e.arrow === "end" || e.arrow === "both") g.setAttribute("marker-end", "url(#" + id + ")");
        if (e.arrow === "start" || e.arrow === "both") g.setAttribute("marker-start", "url(#" + id + ")");
      }
    } else if (kind === "oval") {
      st.cx = w / 2; st.cy = h / 2; st.rx = Math.max(0, (w - line) / 2); st.ry = Math.max(0, (h - line) / 2);
      g = svgEl("ellipse", svg, st);
    } else {
      st.x = line / 2; st.y = line / 2; st.width = Math.max(0, w - line); st.height = Math.max(0, h - line);
      if (e.corner) { st.rx = st.ry = Math.min(e.corner, st.width / 2, st.height / 2); }
      else if (e.radius) { st.rx = e.radius / 100 * w; st.ry = e.radius / 100 * h; }
      g = svgEl("rect", svg, st);
    }
    return n;
  }
  function build(e, stack) {
    var n;
    if (e.type === "shape") {
      n = drawShape(el("div", "kn-el kn-shape", stack), e);
    } else if (e.type === "text") {
      n = el("div", "kn-el kn-text", stack);
      if (e.fill) n.style.background = rgba(e.fill, e.fillOp);
      if (e.radius) n.style.borderRadius = e.radius + "%";
      if (e.glow) n.style.textShadow = glow(e);
      if (e.type === "text") {
        styleText(n, e);
        n.style.textAlign = e.align || "left";
        n.style.justifyContent = { middle: "center", bottom: "flex-end" }[e.valign] || "flex-start";
        var body = el("div", "kn-body", n), p = null;
        if (e.indent) n.style.paddingLeft = e.indent + "px";
        if (e.tracking) n.style.letterSpacing = e.tracking + "em";
        if (e.lineHeight) {             // Keynote keeps the first line where it was; CSS centres lines in their pitch
          body.style.lineHeight = e.lineHeight + "px";
          if ((e.valign || "top") === "top") body.style.marginTop = (1.193 * e.size - e.lineHeight) / 2 + "px";
          else if (e.valign === "bottom") body.style.marginBottom = (1.193 * e.size - e.lineHeight) + "px";   // measured on one box (gc_outline 4–5)
        }
        var np = 0;
        (e.runs || [{ t: e.text }]).forEach(function (r, ri) {    // one block per paragraph
          r.t.split("\n").forEach(function (t, k) {
            if (k || !p) {
              p = el("div", "kn-p", body);
              if (e.paraGap && np) p.style.marginTop = e.paraGap + "px";
              var mk = e.markers && e.markers[np];
              if (mk) { p.style.paddingLeft = (e.markerIndent || 0) + "px"; el("span", "kn-marker", p).textContent = mk; }
              np++;
            }
            if (t) { var sp = el("span", "", p); sp.textContent = t; styleText(sp, r); sp.setAttribute("data-r", ri); }
          });
        });
        Array.prototype.forEach.call(body.children, function (q) { if (!q.firstChild) q.appendChild(document.createElement("br")); });
      }
    } else if (e.type === "image") {
      n = el("div", "kn-el kn-image", stack);
      var im = el("img", "", n);
      im.setAttribute("data-kn-src", e.src); im.alt = "";
      if (e.flip) im.style.transform = e.flip === "h" ? "scaleX(-1)" : "scaleY(-1)";
      if (e.fit) { im.style.left = e.fit[0] + "px"; im.style.top = e.fit[1] + "px"; im.style.width = e.fit[2] + "px"; im.style.height = e.fit[3] + "px"; }
    } else if (e.type === "video") {
      n = el("video", "kn-el", stack);
      n.setAttribute("data-src", url(e.src)); n.playsInline = true;
      n.preload = "none";               // nothing is fetched until its slide is next (near(), below)
      if (e.autoplay) n.setAttribute("data-autoplay", "");
    } else if (e.type === "live") {
      n = el("iframe", "kn-el kn-live", stack);
      n.setAttribute("data-src", e.src); n.setAttribute("data-preload", ""); n.allow = "fullscreen";
      n.addEventListener("load", function () { if (RT.liveLoaded) RT.liveLoaded(n); });
      if (e.fallback) {                 // shown until the page reports ready (no network, slow tiles)
        var fb = frame(el("img", "kn-el kn-fallback", stack), e);
        fb.setAttribute("data-kn-src", e.fallback); fb.alt = "";
      }
    } else return null;
    n.knEl = e;
    return frame(n, e);
  }
  // ---- the build order ----
  // plan(s): the slide's rows with their elements found: [{e, para, do, start, delay, fx, dur}]
  function stages(e) { return e ? (e.states ? e.states.length : (e.steps || 1)) : 1; }
  function plan(s) {
    if (!s || s.use === "flat" || !s.elements) return [];
    var rows = [], live = s.elements.filter(function (e) { return e.type === "live"; })[0], n = 1, max = stages(live);
    if (s.builds) {
      var by = {}, gr = {};
      s.elements.forEach(function (e) { if (e.id && !by[e.id]) by[e.id] = e; if (e.group) (gr[e.group] = gr[e.group] || []).push(e); });
      s.builds.forEach(function (b) {
        var es = b.grp ? gr[b.grp] : by[b.el] && [by[b.el]], e = es && es[0];
        if (!e || (b.do === "step" && (b.grp || e !== live || n >= max))) return;
        if (b.do === "step") n++;
        rows.push({ e: e, es: es, grp: b.grp, para: b.grp ? undefined : b.para, do: b.do, start: b.start || "click", delay: +b.delay || 0, fx: b.fx || "", dur: +b.dur || 0 });
      });
    } else {                              // the importer's form: a click number on elements and paragraphs
      var ev = [];
      s.elements.forEach(function (e, k) {
        var b = e.build > 1 ? e.build : 1;
        if (b > 1) ev.push({ at: b, k: k, e: e, para: -1 });
        (e.paraBuilds || []).forEach(function (pb, np) { if (pb > b) ev.push({ at: pb, k: k, e: e, para: np }); });
      });
      ev.sort(function (a, b) { return a.at - b.at || a.k - b.k || a.para - b.para; });
      ev.forEach(function (x, j) {
        rows.push({ e: x.e, es: [x.e], para: x.para < 0 ? undefined : x.para, do: "in", start: j && ev[j - 1].at === x.at ? "with" : "click", delay: 0, fx: "", dur: 0 });
      });
    }
    for (; n < max; n++) rows.push({ e: live, es: [live], do: "step", start: "click", delay: 0, fx: "", dur: 0 });   // a stage with no row of its own
    return rows;
  }
  function groups(rows) {                 // [rows that run on arrival, rows of click 1, rows of click 2, …]
    var g = [[]];
    rows.forEach(function (r) { if (r.start === "click") g.push([r]); else g[g.length - 1].push(r); });
    return g;
  }
  function nodesFor(sec, r) {             // a row's nodes: its element's (or its paragraph's), or every member's of its group
    var out = [];
    Array.prototype.forEach.call(sec.querySelector(".kn-stack").children, function (x) {
      if (r.es.indexOf(x.knEl) < 0) return;
      if (r.para != null) x = x.querySelectorAll(".kn-body > .kn-p")[r.para];
      if (x) out.push(x);
    });
    return out;
  }
  function stateAt(sec, s, c) {           // what shows once click c has played out (0: on arrival; -1: before anything)
    var rows = plan(s), g = groups(rows), nodes = new Map(), stage = 1;
    rows.forEach(function (r) {           // what a row will bring in is absent before it
      if (r.do !== "step") nodesFor(sec, r).forEach(function (n) { if (!nodes.has(n)) nodes.set(n, r.do !== "in"); });
    });
    for (var k = 0; k <= c && k < g.length; k++) g[k].forEach(function (r) {
      if (r.do === "step") { stage++; return; }
      nodesFor(sec, r).forEach(function (n) { nodes.set(n, r.do === "in"); });
    });
    return { nodes: nodes, stage: stage, groups: g };
  }
  function clicksOf(sec) { return sec.querySelectorAll(".kn-step.visible:not(.kn-pz)").length; }
  function showNode(n, vis, dur, ghost) {
    n.style.transition = dur ? "opacity " + dur + "s linear" + (vis ? "" : ", visibility 0s linear " + dur + "s") : "none";
    if (dur) void n.offsetWidth;
    n.classList.toggle("kn-off", !vis && !ghost);
    n.classList.toggle("kn-ghost", !vis && !!ghost);
  }
  function spans(sec, s) {                // one step of reveal's per click; a pan/zoom click comes after them
    Array.prototype.forEach.call(sec.querySelectorAll(".kn-step:not(.kn-pz)"), function (n) { n.remove(); });
    var n = groups(plan(s)).length - 1, pz = sec.querySelector(".kn-pz");
    for (var i = 0; i < n; i++) el("span", "fragment kn-step", sec);
    if (pz) { pz.removeAttribute("data-fragment-index"); sec.appendChild(pz); }
  }

  // (re)build a slide's stack from its data
  function fill(sec, s) { fillStack(sec.querySelector(".kn-stack"), sec, s); spans(sec, s); }
  function fillStack(stack, sec, s) {
    stack.innerHTML = "";
    if (s.use === "flat" || !s.elements) {
      (s.flat || []).forEach(function (src, j) {
        var im = el("img", "kn-flat" + (j ? " fragment" : ""), stack);
        im.setAttribute("data-kn-src", src); im.alt = "";
      });
      (s.elements || []).forEach(function (e) { if (e.type === "video") build(e, stack); });
    } else {
      stack.style.background = s.bg || "#000";
      s.elements.forEach(function (e) { build(e, stack); });
    }
  }
  function section(s, i) {
    var sec = el("section", "", slidesEl);
    sec.setAttribute("data-i", i);
    if (s.kn != null) sec.setAttribute("data-kn", s.kn);
    el("div", "kn-stack", sec);
    fill(sec, s);
    if (s.panzoom && s.panzoom.on === "click") el("span", "fragment kn-step kn-pz", sec);
    notes(sec, s);
    return sec;
  }
  function notes(sec, s) {
    if (s.notes) { var a = el("aside", "notes", sec); s.notes.split(/\n+/).forEach(function (p) { el("p", "", a).textContent = p; }); }
  }
  // A published copy (tools/publish.py) carries no presenter notes: they are a separate file
  // that the site gives only to the deck's owner. It also keeps itself for use offline.
  function published() {
    fetch("notes.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (n) {
      if (!n) return;
      deck.slides.forEach(function (s, i) {
        if (n[i] == null) return;
        s.notes = n[i];
        notes(slidesEl.querySelector('section[data-i="' + i + '"]'), s);
      });
      RT.notes = true;
    }).catch(function () {});
    el("script", "", document.body).src = BASE + "offline.js";
  }
  // The live figures that are pages of this site (maps), whose files lib/mapsw.js can keep:
  // [{i: slide index, e, key, scope}]. key names the figure in the saved caches: the element's
  // id if it has one, else its page and its place among the figures of that page.
  RT.maps = function () {
    var out = [], nth = {};
    deck.slides.forEach(function (s, i) {
      (s.elements || []).forEach(function (e) {
        if (e.type !== "live" || !e.src || /^live\//.test(e.src)) return;
        var u;
        try { u = new URL(e.src, location.href); } catch (err) { return; }
        var scope = u.pathname.replace(/[^\/]*$/, "");
        if (u.origin !== location.origin || scope === "/" || /^\/decks\//.test(scope)) return;
        var page = scope.replace(/^\/|\/$/g, "").replace(/[^A-Za-z0-9]+/g, "-");
        nth[page] = (nth[page] || 0) + 1;
        out.push({ i: i, e: e, scope: scope, key: e.id ? "id." + String(e.id).replace(/[^A-Za-z0-9_-]/g, "") : page + "." + nth[page] });
      });
    });
    return out;
  };
  function say(text) {                 // a line at the bottom of the window for a few seconds
    var n = el("div", "kn-say", document.body);
    n.textContent = text;
    setTimeout(function () { n.remove(); }, 6000);
  }

  function start() {
    W = deck.width || W; H = deck.height || H;
    document.documentElement.style.setProperty("--kn-w", W + "px");      // deck.css draws the stage at this size
    document.documentElement.style.setProperty("--kn-h", H + "px");
    document.title = deck.title || document.title;
    deck.slides.forEach(section);
    if (deck.published) published();
    else if (deck.site) {
      el("script", "", document.body).src = BASE + "offline.js";
      if (deck.site.owner) el("script", "", document.body).src = BASE + "sitemenu.js";      // ☰: edit, publish, copy
      if (deck.site.owner && RT.maps().length) el("script", "", document.body).src = BASE + "practice.js";   // keeping what the maps fetch
    }

    Reveal.initialize({
      width: W, height: H, margin: 0, minScale: 0.05, maxScale: 8,
      controls: false, progress: false, slideNumber: false, hash: true, hashOneBasedIndex: true, center: false,
      transition: "none", backgroundTransition: "none", viewDistance: 3,
      keyboard: { 75: null }                    // K compares with Keynote's render here; reveal would also step back
    }).then(function () {
      var overlay = el("div", "", slidesEl);
      overlay.id = "kn-dissolve";
      var timer = null;

      function index(sec) { return +sec.getAttribute("data-i"); }
      function data(sec) { return deck.slides[index(sec)]; }

      // ---- pan / zoom ----
      function view(v) { // [scale, cx, cy] → transform that centres (cx, cy) at that scale, edges kept covered
        var s = Math.max(1, +v[0] || 1), cx = v[1] == null ? 0.5 : +v[1], cy = v[2] == null ? 0.5 : +v[2];
        var tx = Math.min(0, Math.max(W * (1 - s), W * (0.5 - cx * s)));
        var ty = Math.min(0, Math.max(H * (1 - s), H * (0.5 - cy * s)));
        return "translate(" + tx + "px," + ty + "px) scale(" + s + ")";
      }
      function panzoom(sec, toEnd, animate) {
        var p = data(sec).panzoom, st = sec.querySelector(".kn-stack");
        if (!p || !st) return;
        st.style.transition = animate ? "transform " + (p.dur || 3) + "s ease-in-out" : "none";
        st.style.transform = view(toEnd ? (p.to || [1]) : (p.from || [1]));
      }

      // ---- live pages (protocol: lib/deck-embed.js) ----
      function liveData(sec) { return (data(sec).elements || []).filter(function (e) { return e.type === "live"; })[0]; }
      function liveStep(sec, replay, n) {  // n: the stage to show; absent = the one the slide's clicks have reached
        var fr = sec.querySelector("iframe.kn-live"), e = liveData(sec);
        if (!fr || !fr.contentWindow || !e) return;
        var w = fr.contentWindow;
        if (n == null) n = RT.editing && RT.stepFor ? RT.stepFor(e) : stateAt(sec, data(sec), clicksOf(sec)).stage;   // the editor chooses the step shown
        n = Math.max(1, Math.min(stages(e), n));
        var dur = +e.dur || 0, k = 1;      // how long this stage plays: its step row's dur, or the element's for stage 1
        if (n > 1) plan(data(sec)).forEach(function (r) { if (r.do === "step" && ++k === n) dur = r.dur; });
        if (e.states) w.postMessage({ type: "deck:state", state: e.states[n - 1], animate: !!fr.knShown }, "*");
        else {
          w.postMessage({ type: "deck:step", step: n, replay: !!replay, dur: dur }, "*");
          w.postMessage({ type: "relief-step", step: n, replay: !!replay, dur: dur }, "*");     // the relief figure's older form
        }
        fr.knShown = true;
      }
      // ---- builds: put a slide in the state its clicks have reached, or play the click just made ----
      // o.play: run the rows of the last click in time; o.replay: the slide was arrived at going forwards;
      // o.quiet: leave the figure alone; o.at: a click to show instead; o.present: as in the talk, also while editing.
      function stop(sec) { (sec.knTimers || []).forEach(clearTimeout); sec.knTimers = []; }
      function apply(sec, o) {
        var s = data(sec);
        o = o || {};
        if (!s || !sec.querySelector(".kn-stack")) return 0;
        stop(sec);
        var ed = RT.editing && !o.present;
        var c = o.at != null ? o.at : ed ? RT.editClick : clicksOf(sec);
        if (ed && c == null) {             // editing with no click chosen: everything shows
          stateAt(sec, s, -1).nodes.forEach(function (v, n) { showNode(n, true, 0); });
          if (!o.quiet) liveStep(sec, o.replay);
          return 0;
        }
        var st = stateAt(sec, s, o.play ? c - 1 : c), stage = st.stage, t0 = 0, t1 = 0, end = 0;
        st.nodes.forEach(function (v, n) { showNode(n, v, 0, ed); });
        if (!o.quiet && (!o.play || c === 0)) liveStep(sec, o.replay, ed ? null : stage);
        if (!o.play) return 0;
        (st.groups[c] || []).forEach(function (r, j) {
          var at = (j === 0 ? 0 : r.start === "after" ? t1 : t0) + r.delay;
          var dur = r.do === "step" ? r.dur : r.fx === "dissolve" ? (r.dur || 1) : 0, fire;
          t0 = at; t1 = at + dur; end = Math.max(end, t1);
          if (r.do === "step") { var n = ++stage; fire = function () { liveStep(sec, true, n); }; }
          else { var ns = nodesFor(sec, r); fire = function () { ns.forEach(function (node) { showNode(node, r.do === "in", dur); }); }; }
          if (at <= 0) fire(); else sec.knTimers.push(setTimeout(fire, at * 1000));
        });
        return end;
      }
      function paint() { Array.prototype.forEach.call(slidesEl.querySelectorAll(":scope > section"), function (sec) { apply(sec, { quiet: true }); }); }
      RT.plan = plan; RT.apply = apply; RT.paint = paint;
      RT.stageAt = function (sec, c) { return stateAt(sec, data(sec), c).stage; };
      RT.stage = function (sec) { return stateAt(sec, data(sec), clicksOf(sec)).stage; };
      RT.clicks = function (i) {           // the build order of slide i changed: as many steps as it has clicks
        var sec = slidesEl.querySelector('section[data-i="' + i + '"]');
        spans(sec, deck.slides[i]); Reveal.syncFragments(sec);
        return sec;
      };

      // A figure that was clicked holds the keyboard, also after its slide is left: keys then
      // go to a page nobody can see. Take the keyboard back.
      function release() {
        var a = document.activeElement;
        if (a && a.tagName === "IFRAME") { a.blur(); window.focus(); }
      }
      function frameOf(win) {
        return Array.prototype.filter.call(slidesEl.querySelectorAll("iframe.kn-live"), function (fr) { return fr.contentWindow === win; })[0];
      }
      RT.liveLoaded = function (fr) { fr.knShown = false; liveStep(fr.closest("section"), false); };
      window.addEventListener("message", function (ev) {
        var d = ev.data, fr = d && frameOf(ev.source);
        if (!fr || typeof d.type !== "string") return;
        if (d.type === "deck-key" || d.type === "deck:key") {
          if (RT.editing) return;                  // while editing, keys in a figure do not turn the slide
          if (d.key === "e" || d.key === "E") {    // E pressed inside the figure: the deck's, like E anywhere else
            release();
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "e", bubbles: true }));
          } else if (d.key === "m" || d.key === "M") {   // the owner's menu (lib/sitemenu.js)
            release();
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
          } else if (d.key === "ArrowLeft" || d.key === "PageUp") Reveal.prev(); else Reveal.next();
        } else if (d.type === "deck:ready") {
          var fb = fr.parentNode.querySelector(".kn-fallback");
          if (fb) fb.remove();
          fr.knShown = false; liveStep(fr.closest("section"), false);
        } else if (d.type === "deck:state") {
          fr.knState = d.state;                    // the figure's view now; DECK_LIVE.state() reads it
        }
      });
      window.DECK_LIVE = {                         // for the console and, later, the editor's "capture this view"
        state: function () { var fr = Reveal.getCurrentSlide().querySelector("iframe.kn-live"); return fr ? fr.knState : null; },
        ask: function () { var fr = Reveal.getCurrentSlide().querySelector("iframe.kn-live"); if (fr) fr.contentWindow.postMessage({ type: "deck:get" }, "*"); }
      };

      // ---- the editor (lib/editor.js), loaded the first time E is pressed ----
      RT.frame = frame; RT.drawShape = drawShape; RT.glow = glow; RT.rgba = rgba; RT.slidesEl = slidesEl; RT.fillStack = fillStack;
      RT.resync = function (i) {             // after slides were added, removed or reordered
        Array.prototype.forEach.call(slidesEl.querySelectorAll(":scope > section"), function (n) { n.remove(); });
        deck.slides.forEach(section);
        slidesEl.appendChild(overlay);
        Reveal.sync();
        Reveal.slide(Math.max(0, Math.min(i, deck.slides.length - 1)));
        paint();
        near();
      };
      RT.render = function (i) {             // rebuild slide i from its data, in place
        var sec = slidesEl.querySelector('section[data-i="' + i + '"]');
        fill(sec, deck.slides[i]);
        RT.fetchAll(sec);
        Array.prototype.forEach.call(sec.querySelectorAll("[data-src]"), function (n) { if (n.tagName === "VIDEO") n.preload = "auto"; n.src = n.getAttribute("data-src"); n.removeAttribute("data-src"); });
        Reveal.syncFragments(sec);
        apply(sec, { quiet: true });
        return sec;
      };
      document.addEventListener("keydown", function (ev) {
        if (deck.published) return;            // a published copy is not edited
        if (ev.key !== "e" && ev.key !== "E" || ev.metaKey || ev.ctrlKey || ev.altKey || RT.editorLoaded) return;
        if (/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) return;
        if (deck.site && !deck.site.edit) {
          if (deck.site.owner) say(RT.notesMissing ? "The presenter notes could not be loaded, so editing is off: reload with a connection (once it has been opened with one, this device can edit it offline)." :
            "A published deck is not edited in place. Make a copy to revise it: /decks/");
          return;
        }
        RT.editorLoaded = true;
        var css = el("link", "", document.head); css.rel = "stylesheet"; css.href = BASE + "editor.css";
        el("script", "", document.body).src = BASE + "editor.js";
      });

      // ---- compare with Keynote's render: K cycles off → on → half ----
      document.addEventListener("keydown", function (ev) {
        if (RT.editing) return;
        if (ev.key !== "k" && ev.key !== "K" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
        var sec = Reveal.getCurrentSlide(), s = data(sec);
        if (!s.flat || !s.flat.length || s.use === "flat" || deck.published || deck.site) return;
        var ov = sec.querySelector(".kn-compare");
        if (!ov) { ov = el("img", "kn-compare", sec); ov.src = s.flat[s.flat.length - 1]; ov.style.opacity = 1; }
        else if (ov.style.opacity === "1") ov.style.opacity = 0.5;
        else ov.remove();
      });

      Reveal.on("slidechanged", function (ev) {
        var prev = ev.previousSlide, cur = ev.currentSlide;
        var forward = prev && index(cur) === index(prev) + 1;
        release();
        // dissolve out of the previous slide: a copy of it fades out over the new one
        var d = prev ? +data(prev).dissolve || 0 : 0;
        var st = prev && prev.querySelector(".kn-stack");
        clearTimeout(timer);
        overlay.style.display = "none"; overlay.innerHTML = "";
        if (forward && d > 0 && st && !st.querySelector("iframe") && !RT.editing) {
          var copy = st.cloneNode(true);
          Array.prototype.forEach.call(copy.querySelectorAll("video"), function (v) { v.removeAttribute("data-autoplay"); });
          overlay.appendChild(copy);
          overlay.style.transition = "none";
          overlay.style.opacity = 1;
          overlay.style.display = "block";
          void overlay.offsetWidth;
          overlay.style.transition = "opacity " + d + "s linear";
          overlay.style.opacity = 0;
          timer = setTimeout(function () { overlay.style.display = "none"; overlay.innerHTML = ""; }, d * 1000 + 50);
        }
        if (prev) panzoom(prev, false, false);
        var p = data(cur).panzoom;
        if (p) {
          var clickDone = cur.querySelector(".kn-pz.visible");
          panzoom(cur, p.on === "click" ? !!clickDone : false, false);
          if (p.on !== "click") { void cur.offsetWidth; panzoom(cur, true, true); }
        }
        if (prev) stop(prev);
        apply(cur, { play: !!forward && !RT.editing && clicksOf(cur) === 0, replay: !!forward });
        near();
      });
      Reveal.on("fragmentshown", function (ev) {
        var sec = ev.fragment.closest("section");
        if (ev.fragment.classList.contains("kn-pz")) panzoom(sec, true, true); else apply(sec, { play: true });
      });
      Reveal.on("fragmenthidden", function (ev) {
        var sec = ev.fragment.closest("section");
        if (ev.fragment.classList.contains("kn-pz")) panzoom(sec, false, true); else apply(sec, {});
      });
      paint();
      near();
      var cur0 = Reveal.getCurrentSlide(), p0 = cur0 && data(cur0).panzoom;
      if (p0) panzoom(cur0, !!cur0.querySelector(".kn-pz.visible") || p0.on !== "click", false);
    });
  }

  // ---- this device's copy (IndexedDB "kn-decks", keys prefixed with the deck's folder) ----
  // Kept by lib/editor.js when a save cannot reach the server (offline editing):
  //   unsent        {text: the whole deck.json, base: X-Mtime it was based on, at, since}
  //   file:<path>   a picture added offline (a Blob), shown through RT.blobs until it is sent
  //   notes         the owner's notes as last loaded, so that the deck can be edited offline
  // At the start an unsent copy is used instead of the deck that came (from the server or the
  // offline copy), unless the server's is newer than the one it was based on: then it is used
  // all the same and the editor's first save meets the usual conflict (keep it, or drop it).
  var FOLDER = location.pathname.replace(/[^\/]*$/, ""), DB = null;
  function idb(mode, fn) {
    if (!DB) DB = new Promise(function (ok, no) {
      if (!window.indexedDB) { no(new Error("no IndexedDB")); return; }
      var q = indexedDB.open("kn-decks", 1);
      q.onupgradeneeded = function () { q.result.createObjectStore("kv"); };
      q.onsuccess = function () { ok(q.result); };
      q.onerror = function () { no(q.error); };
    });
    return DB.then(function (d) {
      return new Promise(function (ok, no) {
        var t = d.transaction("kv", mode), r = fn(t.objectStore("kv"));
        t.oncomplete = function () { ok(r ? r.result : undefined); };
        t.onerror = t.onabort = function () { no(t.error); };
      });
    });
  }
  RT.store = {
    get: function (k) { return idb("readonly", function (st) { return st.get(FOLDER + k); }); },
    put: function (k, v) { return idb("readwrite", function (st) { st.put(v, FOLDER + k); }); },
    del: function (k) { return idb("readwrite", function (st) { st.delete(FOLDER + k); }); },
    keys: function (pre) {
      return idb("readonly", function (st) { return st.getAllKeys(IDBKeyRange.bound(FOLDER + pre, FOLDER + pre + "\uffff")); })
        .then(function (ks) { return (ks || []).map(function (k) { return k.slice(FOLDER.length); }); });
    }
  };
  RT.newer = function (a, b) {             // is version a (X-Mtime, nanoseconds as text) later than b
    a = String(a || ""); b = String(b || "");
    return a.length !== b.length ? a.length > b.length : a > b;
  };
  function local(d) {                      // the deck as this device has it
    if (d.published || (d.site && !d.site.edit)) return Promise.resolve(d);
    return RT.store.keys("file:").then(function (ks) {
      return Promise.all(ks.map(function (k) {
        return RT.store.get(k).then(function (b) { if (b) { RT.blobs[k.slice(5)] = URL.createObjectURL(b); RT.unsentFiles = (RT.unsentFiles || []).concat(k.slice(5)); } });
      }));
    }).then(function () { return RT.store.get("unsent"); }).then(function (u) {
      if (!u || !u.text) return d;
      var mine = JSON.parse(u.text);
      if (d.site) mine.site = d.site;
      RT.unsent = u;
      RT.unsentStale = RT.newer(RT.mtime, u.base);       // changed on the server since: the editor will ask
      RT.mtime = u.base;
      return mine;
    }).catch(function () { return d; });
  }
  fetch("deck.json", { cache: "no-store" }).then(function (r) { RT.mtime = r.headers.get("X-Mtime"); return r.json(); }).then(function (d) {
    if (!d.site || !d.site.owner) return d;
    // the owner's notes, before anything is drawn: the editor saves the whole deck, notes included
    return fetch("notes.json", { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (n) {
      RT.store.put("notes", n).catch(function () {});
      return n;
    }, function () {                       // no connection: the notes this device last had
      return RT.store.get("notes").then(function (n) { if (!n) throw new Error("none kept"); RT.notesKept = true; return n; });
    }).then(function (n) {
      d.slides.forEach(function (s, i) { if (n[i] != null) s.notes = n[i]; });
      return d;
    }).catch(function () { RT.notesMissing = true; d.site.edit = false; return d; });     // never save a deck whose notes are absent
  }).then(local).then(function (d) {
    deck = d; window.DECK = d; start();
    if (RT.unsent && !RT.editorLoaded) {   // changes kept on this device: the editor sends them (without opening)
      RT.editorLoaded = RT.quiet = true;
      var css = el("link", "", document.head); css.rel = "stylesheet"; css.href = BASE + "editor.css";
      el("script", "", document.body).src = BASE + "editor.js";
    }
  }).catch(function (e) {
    document.body.innerHTML = '<p style="color:#fff;font:20px sans-serif;padding:40px">Could not load deck.json: ' + e + "</p>";
  });
})();
