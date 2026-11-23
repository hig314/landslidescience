/*
 * lib/deck.js — renders decks/<name>/deck.json on reveal.js used as a plain 16:9 stage.
 *
 * deck.json:
 *   { title, width, height, slides: [ slide, … ] }      (published: true in a copy made by tools/publish.py;
 *                                                         site: {name, state, owner, edit} when served by the site, see below)
 *   slide: { kn, bg, notes, dissolve, panzoom, flat: [stage images], use: "elements" | "flat",
 *            check: [what the importer could not reproduce], owner, elements: [ element, … ] }
 *   element: frame x, y, w, h in slide pixels; optional rot (degrees), op (0–100), build
 *            (the click at which it appears: 1 or absent = with the slide)
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
 *     shape  { fill, radius }                 radius in percent (50 = ellipse)
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
  var RT = window.DECK_RT = { editing: false };      // what lib/editor.js works through
  var slidesEl = document.querySelector(".reveal .slides");

  function el(tag, cls, parent) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (parent) parent.appendChild(n);
    return n;
  }
  function family(f) { return '"' + f + '", "Helvetica Neue", Helvetica, Arial, sans-serif'; }

  // ---- building a slide from its elements ----
  function frame(node, e) {
    node.style.left = e.x + "px"; node.style.top = e.y + "px";
    node.style.width = e.w + "px"; node.style.height = e.h + "px";
    if (e.rot) node.style.transform = "rotate(" + (-e.rot) + "deg)";
    if (e.op != null && e.op !== 100) node.style.opacity = e.op / 100;
    if (e.build > 1) { node.classList.add("fragment"); node.setAttribute("data-fragment-index", e.build - 2); }
    return node;
  }
  function styleText(node, s) {
    if (s.font) node.style.fontFamily = family(s.font);
    if (s.weight) node.style.fontWeight = s.weight;
    if (s.italic != null) node.style.fontStyle = s.italic ? "italic" : "normal";
    if (s.size) node.style.fontSize = s.size + "px";
    if (s.color) node.style.color = s.color;
  }
  function build(e, stack) {
    var n;
    if (e.type === "text" || e.type === "shape") {
      n = el("div", "kn-el kn-text", stack);
      if (e.fill) n.style.background = e.fill;
      if (e.radius) n.style.borderRadius = e.radius + "%";
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
              var mk = e.markers && e.markers[np], pb = e.paraBuilds && e.paraBuilds[np];
              if (mk) { p.style.paddingLeft = (e.markerIndent || 0) + "px"; el("span", "kn-marker", p).textContent = mk; }
              if (pb > Math.max(1, e.build || 1)) { p.classList.add("fragment"); p.setAttribute("data-fragment-index", pb - 2); }
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
      im.setAttribute("data-src", e.src); im.alt = "";
      if (e.flip) im.style.transform = e.flip === "h" ? "scaleX(-1)" : "scaleY(-1)";
      if (e.fit) { im.style.left = e.fit[0] + "px"; im.style.top = e.fit[1] + "px"; im.style.width = e.fit[2] + "px"; im.style.height = e.fit[3] + "px"; }
    } else if (e.type === "video") {
      n = el("video", "kn-el", stack);
      n.setAttribute("data-src", e.src); n.playsInline = true;
      if (e.autoplay) n.setAttribute("data-autoplay", "");
    } else if (e.type === "live") {
      n = el("iframe", "kn-el kn-live", stack);
      n.setAttribute("data-src", e.src); n.setAttribute("data-preload", ""); n.allow = "fullscreen";
      n.addEventListener("load", function () { if (RT.liveLoaded) RT.liveLoaded(n); });
      var count = e.states ? e.states.length : (e.steps || 1);
      for (var i = 1; i < count; i++) el("span", "fragment kn-step", stack.parentNode);
      if (e.fallback) {                 // shown until the page reports ready (no network, slow tiles)
        var fb = frame(el("img", "kn-el kn-fallback", stack), e);
        fb.setAttribute("data-src", e.fallback); fb.alt = "";
      }
    } else return null;
    n.knEl = e;
    return frame(n, e);
  }
  // (re)build a slide's stack from its data
  function fill(sec, s) { fillStack(sec.querySelector(".kn-stack"), sec, s); }
  function fillStack(stack, sec, s) {
    stack.innerHTML = "";
    Array.prototype.forEach.call(sec.querySelectorAll(".kn-step:not(.kn-pz)"), function (n) { n.remove(); });
    if (s.use === "flat" || !s.elements) {
      (s.flat || []).forEach(function (src, j) {
        var im = el("img", "kn-flat" + (j ? " fragment" : ""), stack);
        im.setAttribute("data-src", src); im.alt = "";
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
  function say(text) {                 // a line at the bottom of the window for a few seconds
    var n = el("div", "kn-say", document.body);
    n.textContent = text;
    setTimeout(function () { n.remove(); }, 6000);
  }

  function start() {
    W = deck.width || W; H = deck.height || H;
    document.title = deck.title || document.title;
    deck.slides.forEach(section);
    if (deck.published) published();
    else if (deck.site) {
      el("script", "", document.body).src = BASE + "offline.js";
      if (deck.site.owner) el("script", "", document.body).src = BASE + "sitemenu.js";      // ☰: edit, publish, copy
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
      function liveStep(sec, replay) {
        var fr = sec.querySelector("iframe.kn-live"), e = liveData(sec);
        if (!fr || !fr.contentWindow || !e) return;
        var n = 1 + sec.querySelectorAll(".kn-step.visible:not(.kn-pz)").length, w = fr.contentWindow;
        if (RT.editing && RT.stepFor) n = RT.stepFor(e);          // the editor chooses the step shown
        if (e.states) w.postMessage({ type: "deck:state", state: e.states[n - 1], animate: !!fr.knShown }, "*");
        else {
          w.postMessage({ type: "deck:step", step: n, replay: !!replay }, "*");
          w.postMessage({ type: "relief-step", step: n, replay: !!replay }, "*");     // the relief figure's older form
        }
        fr.knShown = true;
      }
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
      RT.frame = frame; RT.slidesEl = slidesEl; RT.fillStack = fillStack;
      RT.resync = function (i) {             // after slides were added, removed or reordered
        Array.prototype.forEach.call(slidesEl.querySelectorAll(":scope > section"), function (n) { n.remove(); });
        deck.slides.forEach(section);
        slidesEl.appendChild(overlay);
        Reveal.sync();
        Reveal.slide(Math.max(0, Math.min(i, deck.slides.length - 1)));
      };
      RT.render = function (i) {             // rebuild slide i from its data, in place
        var sec = slidesEl.querySelector('section[data-i="' + i + '"]');
        fill(sec, deck.slides[i]);
        Array.prototype.forEach.call(sec.querySelectorAll("[data-src]"), function (n) { n.src = n.getAttribute("data-src"); n.removeAttribute("data-src"); });
        Reveal.syncFragments(sec);
        return sec;
      };
      document.addEventListener("keydown", function (ev) {
        if (deck.published) return;            // a published copy is not edited
        if (ev.key !== "e" && ev.key !== "E" || ev.metaKey || ev.ctrlKey || ev.altKey || RT.editorLoaded) return;
        if (/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) return;
        if (deck.site && !deck.site.edit) {
          if (deck.site.owner) say(RT.notesMissing ? "The presenter notes could not be loaded, so editing is off: reload with a connection." :
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
        liveStep(cur, forward);
      });
      Reveal.on("fragmentshown", function (ev) {
        var sec = ev.fragment.closest("section");
        if (ev.fragment.classList.contains("kn-pz")) panzoom(sec, true, true); else liveStep(sec, false);
      });
      Reveal.on("fragmenthidden", function (ev) {
        var sec = ev.fragment.closest("section");
        if (ev.fragment.classList.contains("kn-pz")) panzoom(sec, false, true); else liveStep(sec, false);
      });
      var cur0 = Reveal.getCurrentSlide(), p0 = cur0 && data(cur0).panzoom;
      if (p0) panzoom(cur0, !!cur0.querySelector(".kn-pz.visible") || p0.on !== "click", false);
    });
  }

  fetch("deck.json", { cache: "no-store" }).then(function (r) { RT.mtime = r.headers.get("X-Mtime"); return r.json(); }).then(function (d) {
    if (!d.site || !d.site.owner) return d;
    // the owner's notes, before anything is drawn: the editor saves the whole deck, notes included
    return fetch("notes.json", { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (n) {
      d.slides.forEach(function (s, i) { if (n[i] != null) s.notes = n[i]; });
      return d;
    }).catch(function () { RT.notesMissing = true; d.site.edit = false; return d; });     // never save a deck whose notes are absent
  }).then(function (d) {
    deck = d; window.DECK = d; start();
  }).catch(function (e) {
    document.body.innerHTML = '<p style="color:#fff;font:20px sans-serif;padding:40px">Could not load deck.json: ' + e + "</p>";
  });
})();
