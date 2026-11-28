/*
 * lib/editor.js — edit a deck in place. Loaded by lib/deck.js the first time E is pressed;
 * E again (or Done) returns to presenting. Changes are saved to deck.json through
 * tools/serve.py a moment after each one; a slide that is changed is marked "owner": "here",
 * so a Keynote re-import leaves it alone.
 *
 *   click             select (again in the same spot: the element underneath); Shift-click adds
 *   drag              move; snaps to the slide's edges and centre and to other elements
 *                     (Cmd held: no snapping; Shift: along one axis)
 *   Alt-drag          move a copy; the original stays. Alt-arrow: a copy nudged 1 px (with
 *                     Shift: 10). The copies are on top and selected. Not for a live figure.
 *   handles           resize, as in Keynote: Shift keeps the proportions (from any handle),
 *                     Alt resizes about the centre. With several things selected (a group, or
 *                     any selection of more than one) the corner handles of the box round them
 *                     all scale every one by the same amount about the opposite corner (Alt:
 *                     the centre): places, sizes, picture crops and text sizes together.
 *   rotate            just outside a corner or the middle of a side of the selection the pointer
 *                     becomes a turning arrow:
 *                     drag to rotate about the centre (Shift: to multiples of 45°; several things
 *                     turn together, by 45° steps). A rotated thing keeps its handles, which
 *                     resize it along its own sides. Not for a live figure; a rotated picture
 *                     cannot be cropped.
 *   arrows            nudge 1 px (Shift: 10); with nothing selected, previous / next slide
 *   double-click, Enter   retype a text box; Esc or a click elsewhere finishes
 *   Cmd-Z, Shift-Cmd-Z    undo, redo (per editing session)
 *   Cmd-C / X / V / D     copy, cut, paste, duplicate. A paste lands exactly where the
 *                     original was, on any slide; only if that spot already holds the same
 *                     thing is it offset, so that it can be seen.
 *   images            Insert ▸ Image…, Cmd-V of a picture copied in another program (or of image
 *                     files copied in the Finder), or image files dropped on the slide. The file
 *                     is stored in the deck's assets/.
 *   Delete            remove       ] [ } {   forward, backward, to front, to back
 *   Opt-Cmd-G         group the selection (Shift too: ungroup). A group is selected, moved, ordered,
 *                     copied and built as one thing; double-click one of its members to work on
 *                     that member alone. Groups do not nest: grouping groups makes one group.
 *
 * The bar's menus: Slide (sorter, new, delete, background and transition), Insert, Arrange
 * (stacking order, group), Animate (build the selection in or out, the build order).
 *   Opacity           the slider in the bar sets how opaque the selection is. Turn an image down to
 *                     see what is under it while lining the two up, then back to 100.
 *   Text…             (bar, with text selected or being typed) the Text panel: font, size (A− A+:
 *                     10%), bold, italic, colour, alignment, background (and its opacity) and a
 *                     glow round the letters for text over a picture. It applies to every selected
 *                     text box; while retyping one with some words selected, font, size, style and
 *                     colour go to those words only. A size change scales the line spacing with it.
 *   Shapes            Insert ▸ Shape: a box, an oval, or a path (click its points; Shift keeps a
 *                     line straight or at 45°; double-click or Enter ends it, a click on its first
 *                     point closes it, Delete takes back the last point, Esc gives it up). A
 *                     selected path shows its points: drag one to move it, double-click one to
 *                     remove it, double-click the line to add one. Double-clicking a shape, or
 *                     bar ▸ Style…, opens the panel for
 *                     every selected shape: fill and line colour (or None), their opacity, line
 *                     width and style, a box's corners, a path's arrowheads and whether it is
 *                     closed. New shapes take the style last set there.
 *   Crop              (bar, double-click an image, or Enter on one) the image's frame becomes a
 *                     mask: the handles move the mask's edges over the image, which stays where it
 *                     is and shows faintly beyond them; dragging inside, or the arrows, slide the
 *                     image under the mask. Enter, Esc or a click elsewhere finishes. Resizing an
 *                     image outside crop mode scales the picture and its crop together.
 *
 * S opens the slide sorter: click, Shift-click (a range) or Cmd-click slides; drag to
 * reorder; Cmd-C / X / V / D and Delete work on slides; double-click or Enter opens one.
 * In the slide view, Cmd-C with nothing selected copies the slide itself.
 * Copies cross decks: slides or elements copied in one deck paste into another (in another
 * tab or after opening it), and the images, movies and renders they use are copied into
 * that deck's folder by tools/serve.py. A pasted slide is a slide of this deck from then on:
 * it loses its Keynote number and keeps where it came from in "from".
 *
 * Live figures: a page that offers "copy for a presentation" puts a live element on the
 * system clipboard as JSON ({"type": "live", "src", "states": [view strings], x, y, w, h});
 * Cmd-V adds it to the slide. Pasting another view of the same page while that figure is
 * selected adds it as the next step. With a figure selected the bar shows its steps (show,
 * remove) and Use: operate the page in place. The view it is left in becomes the step that
 * was showing; Capture step keeps the current view as a new step instead. (Reading the
 * view needs the page to include lib/deck-embed.js.)
 *
 * All builds of a slide are shown while editing. Geometry is in slide pixels (1920 × 1080).
 */
(function () {
  "use strict";
  var RT = window.DECK_RT, deck = window.DECK, W = deck.width || 1920, H = deck.height || 1080;
  var on = false, sel = [], layer = null, bar = null, undo = [], redo = [], timer = 0;
  var mtime = RT.mtime, blocked = false, typing = null, drag = null;
  var sorter = null, picks = [], anchor = null, sdrag = null;
  var using = null, liveAt = 0, asking = null;
  var cropping = null, opBefore = null, liveFor = null, cycled = null;
  var HERE = location.pathname.replace(/[^\/]*$/, "");
  function csrf(h) { if (window.DECK_CSRF) h["X-CSRFToken"] = window.DECK_CSRF; return h; }    // on the site (see deck.js)
  function deckName(p) { var m = /\/decks\/([A-Za-z0-9_-]+)\/$/.exec(p || ""); return m && m[1]; }

  function mk(tag, cls, parent, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function cur() {
    var sec = Reveal.getCurrentSlide(), i = +sec.getAttribute("data-i");
    return { i: i, s: deck.slides[i], sec: sec, stack: sec.querySelector(".kn-stack") };
  }
  function editable(s) { return s.use !== "flat" && !!s.elements; }
  function nodeOf(e) {
    return Array.prototype.filter.call(cur().stack.children, function (n) { return n.knEl === e; })[0];
  }
  function point(ev) {
    var r = cur().sec.getBoundingClientRect(), k = r.width / W;
    return { x: (ev.clientX - r.left) / k, y: (ev.clientY - r.top) / k, k: k };
  }
  function round(v) { return Math.round(v * 10) / 10; }

  // ---------------------------------------------------------------- saving
  function py(v) {                         // JSON spaced as the importer writes it, so diffs stay small
    if (Array.isArray(v)) return "[" + v.map(py).join(", ") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).map(function (k) { return JSON.stringify(k) + ": " + py(v[k]); }).join(", ") + "}";
    return JSON.stringify(v);
  }
  function dump() {                        // one element per line
    var out = ["{"];
    Object.keys(deck).forEach(function (k) { if (k !== "slides" && k !== "site") out.push(" " + JSON.stringify(k) + ": " + py(deck[k]) + ","); });
    out.push(' "slides": [');
    deck.slides.forEach(function (s, i) {
      out.push("  {");
      Object.keys(s).forEach(function (k) { if (k !== "elements") out.push("   " + JSON.stringify(k) + ": " + py(s[k]) + ","); });
      out.push('   "elements": [');
      var els = s.elements || [];
      els.forEach(function (e, j) { out.push("    " + py(e) + (j < els.length - 1 ? "," : "")); });
      out.push("   ]");
      out.push("  }" + (i < deck.slides.length - 1 ? "," : ""));
    });
    out.push(" ]", "}");
    return out.join("\n") + "\n";
  }
  function status(t, bad) { if (bar) { bar.status.textContent = t; bar.status.className = "kn-ed-status" + (bad ? " bad" : ""); } }
  // ---------------------------------------------------------------- saving
  // One PUT at a time: each is based on the version the one before it made (X-Base-Mtime), so a
  // change made while a save is on its way waits for the answer. (Two at once: the second was
  // refused as "changed elsewhere", and nothing after it was saved. 2026-11-26.)
  // A save that fails is tried again, and says so where it cannot be missed (notice); so
  // does a real conflict (the deck saved from another tab or window), with the choice of
  // which version to keep. Leaving the page with unsaved changes asks first.
  var saving = false, again = false, pending = false, failed = 0, retry = 0, since = 0, note = null;
  function notice(text, buttons) {         // a red box under the bar, also after Done; no text: gone
    if (note) { note.remove(); note = null; }
    if (!text) return;
    note = mk("div", "kn-ed-notice", document.body);
    mk("div", "", note, text);
    (buttons || []).forEach(function (b) { var n = mk("button", "", note, b[0]); n.title = b[1]; n.onclick = b[2]; });
  }
  function clock(t) { var d = new Date(t); return d.getHours() + ":" + ("0" + d.getMinutes()).slice(-2); }
  function conflict() {
    blocked = true;
    status("Not saved", true);
    notice("NOT SAVED. This deck was saved from somewhere else (another tab or window?) after this page loaded it. Nothing done here since " + clock(since || Date.now()) + " is saved.", [
      ["Keep this page's version", "Save what is on this page over the other version (which is kept in the deck's history)", function () { save(true); }],
      ["Reload: take the other version", "Give up the changes made on this page since then", function () { pending = false; location.reload(); }]]);
  }
  function fail(why) {
    failed++; again = false;
    status("Not saved", true);
    notice("NOT SAVED since " + clock(since) + ": " + why + " Trying again every few seconds; keep this page open.",
      [["Try now", "", function () { save(); }]]);
    clearTimeout(retry);
    retry = setTimeout(save, Math.min(20000, 2000 * failed));
  }
  function save(force) {
    clearTimeout(timer); timer = 0; clearTimeout(retry); retry = 0;
    if (force !== true) force = false;     // (also called by timers, with their own argument)
    if (blocked && !force) return;
    if (saving) { again = true; return; }
    saving = true; again = false;
    var head = csrf(mtime && !force ? { "X-Base-Mtime": mtime } : {});
    return fetch("deck.json", { method: "PUT", body: dump(), headers: head }).then(function (r) {
      saving = false;
      if (r.status === 204 || r.status === 200) {
        mtime = r.headers.get("X-Mtime") || mtime;
        failed = 0; blocked = false; notice(null);
        if (again) save();
        else if (!timer) { pending = false; since = 0; status("Saved"); }
        if (r.status === 200) r.json().then(function (j) { if (j && j.files) RT.files(j.files); }).catch(function () {});   // the site: how to ask for each picture now
      }
      else if (r.status === 409) conflict();
      else if (r.status === 403) fail("the site refused it (403). You may have been signed out: sign in again in another tab.");
      else fail("the server answered " + r.status + ".");
    }).catch(function () { saving = false; fail("the server did not answer."); });
  }
  function touch() {
    if (!pending) { pending = true; since = Date.now(); }
    if (blocked) { conflict(); return; }
    status(failed ? "Not saved" : "Saving…", !!failed);
    clearTimeout(timer); timer = setTimeout(save, 500);
  }

  // ---------------------------------------------------------------- history
  // A change is recorded as the slide's data before it; undo puts that back.
  function mine(s) {                       // mark a slide as edited here, so that a re-import leaves it alone
    if (s.owner === "here") return;        // owner goes after kn, where the importer looks for it
    var o = {}; Object.keys(s).forEach(function (k) { o[k] = s[k]; if (k === "kn") o.owner = "here"; });
    if (!o.owner) o.owner = "here";
    Object.keys(s).forEach(function (k) { delete s[k]; });
    Object.keys(o).forEach(function (k) { s[k] = o[k]; });
  }
  function snapshot() { var c = cur(); return { i: c.i, json: JSON.stringify(c.s) }; }
  function commit(before) {
    var c = cur();
    tidy(c.s); mend(c.s);
    if (JSON.stringify(c.s) === before.json) return false;
    mine(c.s);
    undo.push(before); redo = [];
    if (undo.length > 200) undo.shift();
    touch();
    return true;
  }
  function change(fn) { var b = snapshot(); fn(cur()); return commit(b); }
  function snapshotAll() { return { all: JSON.stringify(deck.slides), at: cur().i }; }
  function commitAll(before, at) {         // after slides were added, removed or moved
    if (JSON.stringify(deck.slides) === before.all) return;
    undo.push(before); redo = [];
    deck.source = deck.source || {}; deck.source.restructured = true;     // the importer asks before resetting this
    touch(); resynced(at);
  }
  function resynced(at) { sel = []; RT.resync(at); prepare(); draw(); if (sorter) drawSorter(); else fmtFill(); }
  function restore(from, to) {
    var h = from.pop();
    if (!h) return;
    if (h.all) {
      to.push(snapshotAll());
      deck.slides.length = 0; JSON.parse(h.all).forEach(function (x) { deck.slides.push(x); });
      picks = []; touch(); resynced(h.at);
      return;
    }
    to.push({ i: h.i, json: JSON.stringify(deck.slides[h.i]) });
    deck.slides[h.i] = JSON.parse(h.json);
    sel = [];
    if (cur().i !== h.i) Reveal.slide(h.i);
    rebuild(); touch();
  }

  // ---------------------------------------------------------------- drawing
  function rebuild() {                     // the current slide from its data, keeping the selection
    var c = cur();
    RT.render(c.i);
    prepare();
    sel = sel.filter(function (e) { return c.s.elements.indexOf(e) >= 0; });
    draw();
    fmtFill(); bpFill(); shpFill(); txtFill();
  }
  function prepare() {                     // a slide as it should be while it is edited
    var c = cur();
    c.stack.style.transition = "none"; c.stack.style.transform = "none";
    Array.prototype.forEach.call(c.sec.querySelectorAll("video"), function (v) { v.pause(); });
    c.sec.appendChild(layer);
    bar.where.textContent = "Slide " + (c.i + 1) + " of " + deck.slides.length + (editable(c.s) ? "" : " (shown flat: not editable)");
  }
  function boxOf(list) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    list.forEach(function (e) { x0 = Math.min(x0, e.x); y0 = Math.min(y0, e.y); x1 = Math.max(x1, e.x + e.w); y1 = Math.max(y1, e.y + e.h); });
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  // Several things selected (a group, or any selection of more than one) are resized as one:
  // the corner handles of the box round them all scale every one by the same amount about
  // one point (the opposite corner; Alt: the box's centre). Text is scaled with its box.
  var TXT = ["size", "lineHeight", "paraGap", "indent", "markerIndent", "glowSize"];
  function keepOf(e) {                     // what a drag may change, as it was when the drag began
    var o = { x: e.x, y: e.y, w: e.w, h: e.h, fit: e.fit && e.fit.slice(), rot: e.rot || 0 };
    if (e.type === "text") { o.t = {}; TXT.forEach(function (k) { if (typeof e[k] === "number") o.t[k] = e[k]; }); o.rs = (e.runs || []).map(function (r) { return r.size; }); }
    if (e.type === "shape") { o.t = {}; ["strokeW", "corner"].forEach(function (k) { if (typeof e[k] === "number") o.t[k] = e[k]; }); o.rs = []; }
    return o;
  }
  function scaleAll(k, rx, ry) {           // the selection, from what drag.orig holds, by k about (rx, ry)
    sel.forEach(function (e, j) {
      var o = drag.orig[j];
      e.x = round(rx + (o.x - rx) * k); e.y = round(ry + (o.y - ry) * k); e.w = round(o.w * k); e.h = round(o.h * k);
      if (o.fit) e.fit = o.fit.map(function (v) { return round(v * k); });
      if (o.t) {
        Object.keys(o.t).forEach(function (key) { e[key] = round(o.t[key] * k); });
        o.rs.forEach(function (v, i) { if (typeof v === "number") e.runs[i].size = round(v * k); });
        if (e.type === "text") retext(e);
      }
      place(e);
    });
  }
  function retext(e) {                     // a text box's node after its sizes changed (as deck.js builds it; rebuilt exactly when the drag ends)
    var n = nodeOf(e), body = n && n.querySelector(".kn-body");
    if (!body) return;
    if (e.size) n.style.fontSize = e.size + "px";
    if (e.glow) n.style.textShadow = RT.glow(e);
    if (e.indent) n.style.paddingLeft = e.indent + "px";
    if (e.lineHeight) {
      body.style.lineHeight = e.lineHeight + "px";
      if ((e.valign || "top") === "top") body.style.marginTop = (1.193 * e.size - e.lineHeight) / 2 + "px";
      else if (e.valign === "bottom") body.style.marginBottom = (1.193 * e.size - e.lineHeight) + "px";
    }
    Array.prototype.forEach.call(body.children, function (p, i) {
      if (e.paraGap && i) p.style.marginTop = e.paraGap + "px";
      if (e.markerIndent && p.querySelector(".kn-marker")) p.style.paddingLeft = e.markerIndent + "px";
    });
    Array.prototype.forEach.call(body.querySelectorAll("span[data-r]"), function (sp) {
      var r = e.runs && e.runs[+sp.getAttribute("data-r")];
      if (r && r.size) sp.style.fontSize = r.size + "px";
    });
  }
  function draw(guides) {
    layer.innerHTML = "";
    layer.style.setProperty("--k", 1 / (cur().sec.getBoundingClientRect().width / W));
    if (cropping && (sel.length !== 1 || sel[0] !== cropping || cur().s.elements.indexOf(cropping) < 0)) cropping = null;
    if (cropping) {                        // the whole picture, faint, behind its mask
      var ce = cropping, f = ce.fit || [0, 0, ce.w, ce.h], cn = nodeOf(ce), g = mk("img", "kn-ed-ghost", layer);
      g.src = cn && cn.firstChild && (cn.firstChild.currentSrc || cn.firstChild.src) || RT.url(ce.src);
      g.style.left = ce.x + f[0] + "px"; g.style.top = ce.y + f[1] + "px"; g.style.width = f[2] + "px"; g.style.height = f[3] + "px";
      if (ce.flip) g.style.transform = ce.flip === "h" ? "scaleX(-1)" : "scaleY(-1)";
    }
    sel.forEach(function (e) {
      var b = mk("div", "kn-ed-box" + (e === cropping ? " kn-ed-crop" : ""), layer);
      b.style.left = e.x + "px"; b.style.top = e.y + "px"; b.style.width = e.w + "px"; b.style.height = e.h + "px";
      if (e.rot) b.style.transform = "rotate(" + (-e.rot) + "deg)";
      if (sel.length === 1 && !typing) {
        ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(function (d) { mk("div", "kn-ed-h kn-ed-" + d, b).setAttribute("data-h", d); });
        if (e !== cropping && e.type !== "live") ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(function (d) { mk("div", "kn-ed-rot kn-ed-r" + d, b).setAttribute("data-rot", d); });      // just outside the corners: rotate
      }
    });
    units(sel).forEach(function (u) {      // a group selected whole: one box round it
      if (!u.g || !u.es.every(function (e) { return sel.indexOf(e) >= 0; })) return;
      var gb = boxOf(u.es), n = mk("div", "kn-ed-box kn-ed-grp", layer);
      n.style.left = gb.x + "px"; n.style.top = gb.y + "px"; n.style.width = gb.w + "px"; n.style.height = gb.h + "px";
    });
    var pe = sel.length === 1 && sel[0].type === "shape" && sel[0].shape === "path" && !typing ? sel[0] : null;
    if (pe) (pe.pts || []).forEach(function (q, i) {       // a path's points: drag to move; double-click one to remove it, the line to add one
      var h = mk("div", "kn-ed-h kn-ed-pt", layer);
      h.style.left = pe.x + q[0] * pe.w + "px"; h.style.top = pe.y + q[1] * pe.h + "px";
      h.setAttribute("data-pt", i);
    });
    if (drawing) drawDraft();
    if (sel.length > 1 && !typing && !cropping) {          // the handles that resize them all as one
      var ab = boxOf(sel), an = mk("div", "kn-ed-box kn-ed-all", layer);
      an.style.left = ab.x + "px"; an.style.top = ab.y + "px"; an.style.width = ab.w + "px"; an.style.height = ab.h + "px";
      ["nw", "ne", "se", "sw"].forEach(function (d) { mk("div", "kn-ed-h kn-ed-" + d, an).setAttribute("data-h", d); });
      if (!sel.some(function (x) { return x.type === "live"; })) ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(function (d) { mk("div", "kn-ed-rot kn-ed-r" + d, an).setAttribute("data-rot", d); });
    }
    (guides || []).forEach(function (g) {
      var n = mk("div", "kn-ed-guide", layer);
      if (g.x != null) { n.style.left = g.x + "px"; n.style.top = 0; n.style.height = H + "px"; n.style.width = "0"; }
      else { n.style.top = g.y + "px"; n.style.left = 0; n.style.width = W + "px"; n.style.height = "0"; }
    });
    var t = "";
    if (sel.length) { var b = boxOf(sel); t = "x " + round(b.x) + "  y " + round(b.y) + "  w " + round(b.w) + "  h " + round(b.h) + (sel.length > 1 ? "  (" + sel.length + ")" : "") + (sel.length === 1 && sel[0].rot ? "  ↻ " + round(sel[0].rot) + "°" : ""); }
    if (cropping) { var cf = cropping.fit || [0, 0, cropping.w, cropping.h]; t += "   picture " + round(cf[2]) + " × " + round(cf[3]); }
    bar.geo.textContent = t;
    drawTools();
    drawLive();
    bpMark();
  }
  // ---------------------------------------------------------------- opacity and crop (in the bar)
  function drawTools() {
    var any = (sel.length || typing) && !sorter;
    bar.tools.style.display = any ? "" : "none";
    if (!any) { shpSync(); txtSync(); return; }
    bar.op.disabled = !!typing;            // (kept in place, so the buttons do not move when typing starts)
    bar.text.style.display = textsSel().length ? "" : "none";
    txtSync();
    bar.text.classList.toggle("on", !!txt);
    if (!opBefore && sel.length) { var v = sel[0].op == null ? 100 : sel[0].op; bar.op.value = v; bar.opn.textContent = Math.round(v) + "%"; }
    bar.style.style.display = !typing && shapesSel().length ? "" : "none";
    bar.style.classList.toggle("on", !!shp);
    shpSync();
    var img = !typing && sel.length === 1 && sel[0].type === "image" && !sel[0].rot;
    bar.crop.style.display = img ? "" : "none";
    bar.crop.textContent = cropping ? "Finish crop" : "Crop";
  }
  // ---------------------------------------------------------------- text: the Text panel
  // Bar ▸ Text… opens it; it stays open while text is selected or being typed. It sets font, size,
  // bold, italic, colour, alignment, a background and a glow (a halo round the letters, for text
  // over a picture) for every selected text box. While retyping a box with some words selected,
  // the font, size, bold, italic and colour go to those words only: the box becomes one of runs
  // (deck.js: runs carry the text and what differs). The words are remembered when the panel is
  // clicked (setup: pointerdown), marked in the text and read back by finish().
  var txt = null, txtWant = false, txtBefore = null;
  var RUNKEYS = ["font", "weight", "italic", "size", "color"];
  var FONTS = [["Liberation Sans HN", "Sans (Helvetica)"], ["Liberation Sans", "Sans (Arial)"], ["Liberation Serif", "Serif"], ["Liberation Mono", "Mono"]];
  function joinRuns(e) {                   // neighbours that no longer differ become one; a box of one plain run is plain text again
    var out = [];
    function but(r) { var o = {}; Object.keys(r).sort().forEach(function (k) { if (k !== "t") o[k] = r[k]; }); return JSON.stringify(o); }
    (e.runs || []).forEach(function (r) {
      var p = out[out.length - 1];
      if (p && but(p) === but(r)) p.t += r.t; else out.push(r);
    });
    if (out.length === 1 && Object.keys(out[0]).length === 1) { e.text = out[0].t; delete e.runs; }
    else if (out.length) e.runs = out;
  }
  function textsSel() { return typing ? [typing.e] : sel.filter(function (e) { return e.type === "text"; }); }
  function words() { return typing && typing.range ? typing.range : null; }
  function boxSet(e, props) {              // the whole box: what the runs said otherwise is dropped (a size scales them)
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (k === "size") {
        var f = v / (e.size || v);
        e.size = v;
        ["lineHeight", "paraGap", "indent", "markerIndent"].forEach(function (x) { if (typeof e[x] === "number") e[x] = round(e[x] * f); });
        (e.runs || []).forEach(function (r) { if (typeof r.size === "number") r.size = round(r.size * f); });
        return;
      }
      if (v == null) delete e[k]; else e[k] = v;
      if (RUNKEYS.indexOf(k) >= 0) (e.runs || []).forEach(function (r) { delete r[k]; });
    });
    if (e.runs) joinRuns(e);
  }
  function textSet(props, done) {          // a control moving (one undo step), then let go; or a button
    if (typing) {
      var rg = words(), runs = {};
      Object.keys(props).forEach(function (k) { if (RUNKEYS.indexOf(k) >= 0) runs[k] = props[k]; });
      if (rg && Object.keys(runs).length) {                // some words: when the value is chosen
        if (!done) return;
        var a = rg.cloneRange(), b = rg.cloneRange();
        b.collapse(false); b.insertNode(document.createTextNode(""));
        a.collapse(true); a.insertNode(document.createTextNode(""));
        typing.paint = runs;
        finish();
        return;
      }
      finish();
    }
    var list = sel.filter(function (e) { return e.type === "text"; });
    if (!list.length) return;
    if (!txtBefore) txtBefore = snapshot();
    list.forEach(function (e) { boxSet(e, props); });
    if (done) { var was = txtBefore; txtBefore = null; commit(was); }
    rebuild();
  }
  function txtSync() {
    var want = on && txtWant && !sorter && !drawing && textsSel().length;
    if (!want) { if (txt) { txt.el.remove(); txt = null; } }
    else { if (!txt) txtMake(); if (!txtBefore) txtFill(); }
    if (shp) shp.el.style.top = txt ? txt.el.offsetTop + txt.el.offsetHeight + 8 + "px" : "";   // both open: the shape panel below
  }
  function txtMake() {
    txt = { el: mk("div", "kn-ed-fmt kn-ed-shp kn-ed-txt", document.body) };
    var head = mk("div", "kn-ed-fmt-head", txt.el);
    txt.head = mk("span", "", head, "Text");
    var x = mk("button", "kn-ed-x", head, "×"); x.title = "Close (bar ▸ Text… opens it again)";
    x.onclick = function () { txtWant = false; txtSync(); };
    txt.hint = mk("div", "kn-ed-fmt-note kn-ed-hint", txt.el, "");
    function row(label) { var r = mk("div", "kn-ed-row", txt.el); mk("span", "", r, label); return r; }
    function keep(b) { b.addEventListener("mousedown", function (ev) { ev.preventDefault(); }); return b; }   // the words being typed stay selected
    function seg(r, opts, fn) {            // a row of buttons, one of them lit
      var bs = opts.map(function (o) { var b = mk("button", "kn-ed-seg", r, o[1]); keep(b); b.title = o[2] || ""; b.knVal = o[0]; b.onclick = function () { b.blur(); fn(o[0]); }; return b; });
      return { bs: bs, set: function (v) { bs.forEach(function (b) { b.classList.toggle("on", b.knVal === v); }); } };
    }
    var r = row("Font");
    txt.font = mk("select", "", r);
    FONTS.forEach(function (o) { mk("option", "", txt.font, o[1]).value = o[0]; });
    txt.font.onchange = function () { var v = txt.font.value; txt.font.blur(); textSet({ font: v }, true); };
    r = row("Size");
    txt.size = mk("input", "", r); txt.size.type = "number"; txt.size.min = 6; txt.size.max = 600; txt.size.step = 1;
    mk("span", "kn-ed-fmt-s", r, "px");
    function bump(f) { var e = textsSel()[0]; if (e) textSet({ size: Math.max(6, Math.round((curSize() || e.size || 48) * f)) }, true); }
    var sm = keep(mk("button", "kn-ed-seg", r, "A−")); sm.title = "Smaller (10%)"; sm.onclick = function () { sm.blur(); bump(1 / 1.1); };
    var bg = keep(mk("button", "kn-ed-seg", r, "A+")); bg.title = "Larger (10%)"; bg.onclick = function () { bg.blur(); bump(1.1); };
    txt.size.onchange = function () { var v = Math.max(6, Math.min(600, Math.round(+txt.size.value) || 48)); textSet({ size: v }, true); };
    r = row("Style");
    txt.bold = keep(mk("button", "kn-ed-seg kn-ed-b", r, "B")); txt.bold.title = "Bold";
    txt.bold.onclick = function () { txt.bold.blur(); textSet({ weight: txt.bold.classList.contains("on") ? 400 : 700 }, true); };
    txt.ital = keep(mk("button", "kn-ed-seg kn-ed-i", r, "I")); txt.ital.title = "Italic";
    txt.ital.onclick = function () { txt.ital.blur(); textSet({ italic: !txt.ital.classList.contains("on") }, true); };
    r = row("Colour");
    txt.col = mk("input", "", r); txt.col.type = "color";
    txt.col.addEventListener("input", function () { textSet({ color: txt.col.value }, false); });
    txt.col.addEventListener("change", function () { textSet({ color: txt.col.value }, true); txt.col.blur(); });
    txt.sw = mk("div", "kn-ed-fmt-sw", txt.el);
    txt.align = seg(row("Align"), [["left", "Left"], ["center", "Centre"], ["right", "Right"]], function (v) { textSet({ align: v }, true); });
    txt.valign = seg(row("In the box"), [["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]], function (v) { textSet({ valign: v }, true); });
    function colourRow(label, key, dflt, extra) {     // None, a picker, and an opacity or a size
      var r = row(label), none = mk("button", "kn-ed-none", r, "None");
      var c = mk("input", "", r); c.type = "color";
      none.onclick = function () { none.blur(); textSet(fieldsOff(key), true); };
      c.addEventListener("input", function () { var p = {}; p[key] = c.value; textSet(p, false); });
      c.addEventListener("change", function () { var p = {}; p[key] = c.value; textSet(p, true); c.blur(); });
      return { none: none, c: c, dflt: dflt };
    }
    function fieldsOff(key) { var p = {}; p[key] = null; return p; }
    function slider(label, key, min, max, dflt, unit) {
      var r = row(label), s = mk("input", "", r); s.type = "range"; s.min = min; s.max = max; s.step = 1;
      var n = mk("span", "kn-ed-fmt-s", r);
      function set(done) { var v = +s.value, p = {}; n.textContent = v + unit; p[key] = v === dflt && unit === "%" ? null : v; textSet(p, done); }
      s.addEventListener("input", function () { set(false); });
      s.addEventListener("change", function () { set(true); s.blur(); });
      return { s: s, n: n, dflt: dflt, unit: unit, key: key };
    }
    txt.fill = colourRow("Background", "fill", "#000000");
    txt.fillOp = slider("Background opacity", "fillOp", 0, 100, 100, "%");
    txt.glow = colourRow("Glow", "glow", "#000000");
    txt.glowSize = slider("Glow size", "glowSize", 1, 40, 8, " px");
    txt.glowStrength = slider("Glow strength", "glowStrength", 1, 6, 2, "");
    mk("div", "kn-ed-fmt-note", txt.el, "Glow: a soft halo round the letters, to keep text readable over a picture (dark glow behind light text, or the reverse).");
  }
  function curSize() {                     // the size of the words selected, while typing
    var rg = words(), n = rg && rg.startContainer;
    if (n && n.nodeType === 3) n = n.parentNode;
    return n && n.nodeType === 1 ? Math.round(parseFloat(getComputedStyle(n).fontSize)) : null;
  }
  function txtFill() {
    if (!txt) return;
    var list = textsSel(), e = list[0];
    if (!e) return;
    var rg = words(), n = rg && rg.startContainer, cs = null;
    if (n && n.nodeType === 3) n = n.parentNode;
    if (n && n.nodeType === 1) cs = getComputedStyle(n);
    txt.head.textContent = typing ? "Text (typing)" : list.length > 1 ? list.length + " text boxes" : "Text";
    txt.hint.textContent = typing ? (rg ? "Font, size, style and colour go to the words selected; the rest to the whole box." :
      "Select some words to give them their own font, size, style or colour; otherwise the whole box changes.") : "";
    txt.hint.style.display = typing ? "" : "none";
    var f = e.font || "Liberation Sans HN";
    if (cs) f = (cs.fontFamily.match(/"([^"]+)"/) || [0, f])[1];
    txt.font.value = FONTS.some(function (o) { return o[0] === f; }) ? f : "Liberation Sans HN";
    if (document.activeElement !== txt.size) txt.size.value = cs ? Math.round(parseFloat(cs.fontSize)) : e.size || 48;
    txt.bold.classList.toggle("on", cs ? +cs.fontWeight >= 600 : (e.weight || 400) >= 600);
    txt.ital.classList.toggle("on", cs ? cs.fontStyle === "italic" : !!e.italic);
    var col = cs ? hex(rgbHex(cs.color)) : hex(e.color);
    if (document.activeElement !== txt.col) txt.col.value = col || "#000000";
    txt.align.set(e.align || "left"); txt.valign.set(e.valign || "top");
    [[txt.fill, "fill"], [txt.glow, "glow"]].forEach(function (p) {
      var v = hex(e[p[1]]);
      p[0].none.classList.toggle("on", !v); p[0].c.classList.toggle("off", !v);
      if (document.activeElement !== p[0].c) p[0].c.value = v || p[0].dflt;
    });
    [txt.fillOp, txt.glowSize, txt.glowStrength].forEach(function (sl) {
      var v = e[sl.key] == null ? sl.dflt : e[sl.key];
      sl.s.value = v; sl.n.textContent = v + sl.unit;
    });
    txt.fillOp.s.disabled = !e.fill; txt.glowSize.s.disabled = txt.glowStrength.s.disabled = !e.glow;
    // swatches: black, white, and the text colours this deck uses most
    var cnt = {};
    function count(c) { c = hex(c); if (c) cnt[c] = (cnt[c] || 0) + 1; }
    deck.slides.forEach(function (y) { (y.elements || []).forEach(function (x) { if (x.type !== "text") return; count(x.color); (x.runs || []).forEach(function (r) { count(r.color); }); }); });
    var used = Object.keys(cnt).sort(function (a, b) { return cnt[b] - cnt[a]; });
    used = ["#000000", "#ffffff"].concat(used.filter(function (c) { return c !== "#000000" && c !== "#ffffff"; })).slice(0, 10);
    txt.sw.innerHTML = "";
    used.forEach(function (c) {
      var b = mk("button", c === col ? "on" : "", txt.sw);
      b.style.background = c; b.title = c;
      b.addEventListener("mousedown", function (ev) { ev.preventDefault(); });      // the text being typed keeps its selection
      b.onclick = function () { textSet({ color: c }, true); b.blur(); };
    });
  }
  function rgbHex(c) { var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || ""); return m ? "#" + [m[1], m[2], m[3]].map(function (v) { return ("0" + (+v).toString(16)).slice(-2); }).join("") : c; }
  function opacity(v, done) {              // the slider moving (one undo step per drag), then let go
    if (!sel.length) return;
    if (!opBefore) opBefore = snapshot();
    sel.forEach(function (e) {
      if (v >= 100) delete e.op; else e.op = v;
      var n = nodeOf(e);
      if (n) n.style.opacity = v >= 100 ? "" : v / 100;
    });
    bar.opn.textContent = Math.round(v) + "%";
    if (done) { var b = opBefore; opBefore = null; commit(b); }
  }
  function crop(e) {                       // start or finish masking an image
    if (cropping || !e) {
      var c = cropping; cropping = null;
      if (c && c.fit && Math.abs(c.fit[0]) < 0.6 && Math.abs(c.fit[1]) < 0.6 && Math.abs(c.fit[2] - c.w) < 0.6 && Math.abs(c.fit[3] - c.h) < 0.6) {
        change(function () { delete c.fit; }); place(c);       // the mask is the whole picture again
      }
    } else if (e.type === "image" && !e.rot) { sel = [e]; cropping = e; }
    draw();
  }
  function slide(dx, dy) {                 // the picture under its mask, kept covering it
    var e = cropping, f = e.fit || [0, 0, e.w, e.h];
    var fx = Math.min(0, Math.max(e.w - f[2], f[0] + dx)), fy = Math.min(0, Math.max(e.h - f[3], f[1] + dy));
    e.fit = [round(fx), round(fy), f[2], f[3]];
    place(e);
  }
  function place(e) {                      // an element's node, after its frame changed
    var n = nodeOf(e);
    if (!n) return;
    RT.frame(n, e);
    if (e.type === "shape") RT.drawShape(n, e);
    if (e.type === "image" && e.fit) {
      var im = n.firstChild;
      im.style.left = e.fit[0] + "px"; im.style.top = e.fit[1] + "px"; im.style.width = e.fit[2] + "px"; im.style.height = e.fit[3] + "px";
    } else if (e.type === "image" && n.firstChild) {
      var i2 = n.firstChild; i2.style.left = i2.style.top = i2.style.width = i2.style.height = "";
    }
  }

  // ---------------------------------------------------------------- snapping
  function lines(c, skip) {
    var xs = [0, W / 2, W], ys = [0, H / 2, H];
    c.s.elements.forEach(function (e) {
      if (skip.indexOf(e) >= 0 || e.rot) return;
      xs.push(e.x, e.x + e.w / 2, e.x + e.w); ys.push(e.y, e.y + e.h / 2, e.y + e.h);
    });
    return { xs: xs, ys: ys };
  }
  function nearest(vals, cands, tol) {     // the smallest shift that puts one of vals on a candidate
    var best = null;
    vals.forEach(function (v) { cands.forEach(function (c) { var d = c - v; if (Math.abs(d) <= tol && (!best || Math.abs(d) < Math.abs(best.d))) best = { d: d, at: c }; }); });
    return best;
  }

  // ---------------------------------------------------------------- pointer
  function hits(p) {
    return cur().s.elements.filter(function (e) {
      var x = p.x, y = p.y;
      if (e.rot) {                         // the point in the element's own axes (it is drawn turned by rotate(-rot) about its centre)
        var a = e.rot * Math.PI / 180, cx = e.x + e.w / 2, cy = e.y + e.h / 2, dx = x - cx, dy = y - cy;
        x = cx + dx * Math.cos(a) - dy * Math.sin(a); y = cy + dx * Math.sin(a) + dy * Math.cos(a);
      }
      return x >= e.x && x <= e.x + e.w && y >= e.y && y <= e.y + e.h;
    }).reverse();
  }
  function down(ev) {
    if (ev.button || typing || using) return;
    var c = cur();
    if (!editable(c.s)) return;
    ev.preventDefault();
    if (drawing) { drawAdd(point(ev), ev.shiftKey); return; }
    var rz = ev.target.getAttribute && ev.target.getAttribute("data-rot");
    if (rz && sel.length) {                // just outside a corner: rotate the selection about its centre
      var p0 = point(ev), rb = sel.length === 1 ? sel[0] : boxOf(sel), rc = { x: rb.x + rb.w / 2, y: rb.y + rb.h / 2 };
      drag = { p: p0, rotate: true, c: rc, a0: Math.atan2(p0.y - rc.y, p0.x - rc.x), before: snapshot(), moved: false, under: [], was: sel.slice(), orig: sel.map(keepOf) };
      try { layer.setPointerCapture(ev.pointerId); } catch (x) {}
      return;
    }
    var pt = ev.target.getAttribute && ev.target.getAttribute("data-pt");
    if (pt != null && sel.length === 1 && sel[0].shape === "path") {
      drag = { p: point(ev), pt: +pt, abs: absPts(sel[0]), before: snapshot(), moved: false, under: [], was: sel.slice(), orig: sel.map(keepOf) };
      try { layer.setPointerCapture(ev.pointerId); } catch (x) {}
      return;
    }
    bpKeys = false;                        // a click on the slide: Delete means what is selected there
    var p = point(ev), h = ev.target.getAttribute && ev.target.getAttribute("data-h");
    var under = hits(p), was = sel.slice();
    if (cropping) {
      var ce = cropping, inside = p.x >= ce.x && p.x <= ce.x + ce.w && p.y >= ce.y && p.y <= ce.y + ce.h;
      if (!h && !inside) { crop(null); return; }
      var f0 = (ce.fit || [0, 0, ce.w, ce.h]).slice();
      drag = { p: p, handle: h, crop: true, before: snapshot(), moved: false, under: [], was: was,
               orig: [{ x: ce.x, y: ce.y, w: ce.w, h: ce.h, fit: f0 }], snap: lines(c, sel) };
      drag.snap.xs.push(ce.x + f0[0], ce.x + f0[0] + f0[2]); drag.snap.ys.push(ce.y + f0[1], ce.y + f0[1] + f0[3]);
      try { layer.setPointerCapture(ev.pointerId); } catch (x) {}
      return;
    }
    if (!h) {
      var top = (ev.shiftKey ? null : under.filter(function (e) { return sel.indexOf(e) >= 0; })[0]) || under[0];   // Shift: what is on top here
      if (!top) { sel = []; draw(); return; }
      var u = unit(top);                   // a member of a group stands for the group
      if (ev.shiftKey) { if (sel.indexOf(top) >= 0) sel = sel.filter(function (e) { return u.indexOf(e) < 0; }); else u.forEach(function (e) { if (sel.indexOf(e) < 0) sel.push(e); }); }
      else if (sel.indexOf(top) < 0) sel = u;
      if (!sel.length) { draw(); return; }
    }
    drag = { p: p, handle: h, before: snapshot(), moved: false, under: under, was: was, shift: ev.shiftKey, top: top, alt: ev.altKey && !h, id: ev.pointerId,
             orig: sel.map(keepOf),
             snap: lines(c, sel) };
    try { layer.setPointerCapture(ev.pointerId); } catch (x) {}
    draw();
  }
  function move(ev) {
    if (drawing) { drawing.at = constrain(point(ev), ev.shiftKey); draw(); return; }
    if (!drag) return;
    var p = point(ev), dx = p.x - drag.p.x, dy = p.y - drag.p.y, tol = ev.metaKey ? -1 : 7 / p.k, guides = [];
    if (!drag.moved && Math.abs(dx) * p.k < 3 && Math.abs(dy) * p.k < 3) return;
    if (drag.rotate) {                     // turning: by the angle the pointer has swept round the centre
      drag.moved = true;
      var sw = (Math.atan2(p.y - drag.c.y, p.x - drag.c.x) - drag.a0) * 180 / Math.PI;     // clockwise on screen
      var one = sel.length === 1, r0 = drag.orig[0].rot;
      if (ev.shiftKey) sw = one ? -(Math.round((r0 - sw) / 45) * 45 - r0) : Math.round(sw / 45) * 45;   // Shift: one thing to a multiple of 45°, several by 45° steps
      var rad = sw * Math.PI / 180, cs = Math.cos(rad), sn = Math.sin(rad);
      sel.forEach(function (e, j) {
        var o = drag.orig[j], ox = o.x + o.w / 2 - drag.c.x, oy = o.y + o.h / 2 - drag.c.y;
        var r = o.rot - sw;
        r = ((r % 360) + 540) % 360 - 180;                  // (-180, 180]
        if (Math.abs(r) < 0.05) delete e.rot; else e.rot = round(r);
        if (!one) { e.x = round(drag.c.x + ox * cs - oy * sn - o.w / 2); e.y = round(drag.c.y + ox * sn + oy * cs - o.h / 2); }
        place(e);
      });
      status(one ? "↻ " + round(sel[0].rot || 0) + "°" : "↻ " + round(-sw) + "°");
      draw();
      return;
    }
    if (drag.pt != null) {                 // one point of a path
      drag.moved = true;
      var ap = drag.abs.map(function (q) { return q.slice(); });
      ap[drag.pt] = [Math.round(drag.abs[drag.pt][0] + dx), Math.round(drag.abs[drag.pt][1] + dy)];
      setPts(sel[0], ap); place(sel[0]); draw();
      return;
    }
    if (!drag.moved && drag.alt) {         // Alt-drag: copies are made as the drag begins, and they are what moves
      var made = cloneSel(cur().s);
      if (made.length) {
        sel = made;
        drag.orig = sel.map(keepOf);
        rebuild();
        drag.snap = lines(cur(), sel);     // the originals are now something to line up with
        try { layer.setPointerCapture(drag.id); } catch (x) {}
        status(made.length > 1 ? made.length + " copies" : "Copy");
      }
    }
    drag.moved = true;
    if (drag.crop) {
      var ce = sel[0], co = drag.orig[0], cf = co.fit, ch = drag.handle || "";
      if (!ch) { ce.fit = cf.slice(); slide(Math.round(dx), Math.round(dy)); draw(); return; }
      // the picture stays where it is on the slide; the mask's edges move over it, never beyond it
      var ix0 = co.x + cf[0], iy0 = co.y + cf[1], ix1 = ix0 + cf[2], iy1 = iy0 + cf[3];
      var mx0 = co.x, my0 = co.y, mx1 = co.x + co.w, my1 = co.y + co.h, cs;
      if (ch.indexOf("w") >= 0) { mx0 += dx; cs = nearest([mx0], drag.snap.xs, tol); if (cs) { mx0 += cs.d; guides.push({ x: cs.at }); } mx0 = Math.max(ix0, Math.min(mx0, mx1 - 10)); }
      if (ch.indexOf("e") >= 0) { mx1 += dx; cs = nearest([mx1], drag.snap.xs, tol); if (cs) { mx1 += cs.d; guides.push({ x: cs.at }); } mx1 = Math.min(ix1, Math.max(mx1, mx0 + 10)); }
      if (ch.indexOf("n") >= 0) { my0 += dy; cs = nearest([my0], drag.snap.ys, tol); if (cs) { my0 += cs.d; guides.push({ y: cs.at }); } my0 = Math.max(iy0, Math.min(my0, my1 - 10)); }
      if (ch.indexOf("s") >= 0) { my1 += dy; cs = nearest([my1], drag.snap.ys, tol); if (cs) { my1 += cs.d; guides.push({ y: cs.at }); } my1 = Math.min(iy1, Math.max(my1, my0 + 10)); }
      if (!guides.length) { mx0 = Math.round(mx0); mx1 = Math.round(mx1); my0 = Math.round(my0); my1 = Math.round(my1); }   // whole pixels unless snapped
      ce.x = round(mx0); ce.y = round(my0); ce.w = round(mx1 - mx0); ce.h = round(my1 - my0);
      ce.fit = [round(ix0 - ce.x), round(iy0 - ce.y), cf[2], cf[3]];
      place(ce); draw(guides);
      return;
    }
    if (!drag.handle) {
      if (ev.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      var b = boxOf(drag.orig);
      var sx = nearest([b.x + dx, b.x + b.w / 2 + dx, b.x + b.w + dx], drag.snap.xs, tol);
      var sy = nearest([b.y + dy, b.y + b.h / 2 + dy, b.y + b.h + dy], drag.snap.ys, tol);
      if (sx) { dx += sx.d; guides.push({ x: sx.at }); }
      if (sy) { dy += sy.d; guides.push({ y: sy.at }); }
      if (!sx) dx = Math.round(b.x + dx) - b.x;          // whole pixels, unless snapped to something that is not
      if (!sy) dy = Math.round(b.y + dy) - b.y;
      sel.forEach(function (e, j) { e.x = round(drag.orig[j].x + dx); e.y = round(drag.orig[j].y + dy); place(e); });
    } else if (sel.length > 1) {           // a corner of the box round several things: all of them, by one amount about one point
      var gb = drag.box || (drag.box = boxOf(drag.orig)), gh = drag.handle;
      var gw = gb.w + (gh.indexOf("w") >= 0 ? -dx : dx) * (ev.altKey ? 2 : 1), gh2 = gb.h + (gh.indexOf("n") >= 0 ? -dy : dy) * (ev.altKey ? 2 : 1);
      var gk = Math.max(gw / gb.w, gh2 / gb.h), least = 0;
      drag.orig.forEach(function (q) { least = Math.max(least, 10 / q.w, 10 / q.h); });
      gk = Math.max(gk, Math.min(1, least));
      scaleAll(gk, ev.altKey ? gb.x + gb.w / 2 : gh.indexOf("w") >= 0 ? gb.x + gb.w : gb.x,
                   ev.altKey ? gb.y + gb.h / 2 : gh.indexOf("n") >= 0 ? gb.y + gb.h : gb.y);
      status(Math.round(gk * 100) + "%");
    } else {
      var e = sel[0], o = drag.orig[0], hd = drag.handle;
      if (o.rot) { turned(e, o, hd, dx, dy, ev); draw(); return; }
      var x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h, s;
      if (hd.indexOf("w") >= 0) { x0 += dx; s = nearest([x0], drag.snap.xs, tol); if (s) { x0 += s.d; guides.push({ x: s.at }); } x0 = Math.min(x0, x1 - 10); }
      if (hd.indexOf("e") >= 0) { x1 += dx; s = nearest([x1], drag.snap.xs, tol); if (s) { x1 += s.d; guides.push({ x: s.at }); } x1 = Math.max(x1, x0 + 10); }
      if (hd.indexOf("n") >= 0) { y0 += dy; s = nearest([y0], drag.snap.ys, tol); if (s) { y0 += s.d; guides.push({ y: s.at }); } y0 = Math.min(y0, y1 - 10); }
      if (hd.indexOf("s") >= 0) { y1 += dy; s = nearest([y1], drag.snap.ys, tol); if (s) { y1 += s.d; guides.push({ y: s.at }); } y1 = Math.max(y1, y0 + 10); }
      // Keynote's modifiers: Shift keeps the proportions (from any handle), Alt resizes about the centre
      var nw = x1 - x0, nh = y1 - y0, horiz = /[ew]/.test(hd), vert = /[ns]/.test(hd);
      if (ev.altKey) { nw = o.w + 2 * (nw - o.w); nh = o.h + 2 * (nh - o.h); }
      if (ev.shiftKey) {
        var k = horiz && vert ? Math.max(nw / o.w, nh / o.h) : horiz ? nw / o.w : nh / o.h;
        nw = o.w * k; nh = o.h * k; guides = [];
      }
      nw = Math.max(10, nw); nh = Math.max(10, nh);
      var cx = o.x + o.w / 2, cy = o.y + o.h / 2;
      x0 = ev.altKey || !horiz ? cx - nw / 2 : hd.indexOf("w") >= 0 ? o.x + o.w - nw : o.x;
      y0 = ev.altKey || !vert ? cy - nh / 2 : hd.indexOf("n") >= 0 ? o.y + o.h - nh : o.y;
      e.x = round(x0); e.y = round(y0); e.w = Math.max(10, Math.round(nw)); e.h = Math.max(10, Math.round(nh));
      if (o.fit) { var fx = e.w / o.w, fy = e.h / o.h; e.fit = [round(o.fit[0] * fx), round(o.fit[1] * fy), round(o.fit[2] * fx), round(o.fit[3] * fy)]; }
      place(e);
    }
    draw(guides);
  }
  function turned(e, o, hd, dx, dy, ev) {   // a handle of a rotated element: the same rules, in the element's own axes (no snapping)
    var t = -o.rot * Math.PI / 180, ux = [Math.cos(t), Math.sin(t)], uy = [-Math.sin(t), Math.cos(t)];
    var lx = dx * ux[0] + dy * ux[1], ly = dx * uy[0] + dy * uy[1];           // the pointer's move along the element's sides
    var x0 = 0, y0 = 0, x1 = o.w, y1 = o.h, horiz = /[ew]/.test(hd), vert = /[ns]/.test(hd);
    if (hd.indexOf("w") >= 0) x0 = Math.min(lx, x1 - 10); if (hd.indexOf("e") >= 0) x1 = Math.max(o.w + lx, 10);
    if (hd.indexOf("n") >= 0) y0 = Math.min(ly, y1 - 10); if (hd.indexOf("s") >= 0) y1 = Math.max(o.h + ly, 10);
    var nw = x1 - x0, nh = y1 - y0;
    if (ev.altKey) { nw = o.w + 2 * (nw - o.w); nh = o.h + 2 * (nh - o.h); }
    if (ev.shiftKey) { var k = horiz && vert ? Math.max(nw / o.w, nh / o.h) : horiz ? nw / o.w : nh / o.h; nw = o.w * k; nh = o.h * k; }
    nw = Math.max(10, Math.round(nw)); nh = Math.max(10, Math.round(nh));
    // the new centre, in the element's axes from its old top-left corner: the opposite side stays put
    var cx = ev.altKey || !horiz ? o.w / 2 : hd.indexOf("w") >= 0 ? o.w - nw / 2 : nw / 2;
    var cy = ev.altKey || !vert ? o.h / 2 : hd.indexOf("n") >= 0 ? o.h - nh / 2 : nh / 2;
    var mx = cx - o.w / 2, my = cy - o.h / 2;
    var gx = o.x + o.w / 2 + mx * ux[0] + my * uy[0], gy = o.y + o.h / 2 + mx * ux[1] + my * uy[1];
    e.w = nw; e.h = nh; e.x = round(gx - nw / 2); e.y = round(gy - nh / 2);
    if (o.fit) { var fx = e.w / o.w, fy = e.h / o.h; e.fit = [round(o.fit[0] * fx), round(o.fit[1] * fy), round(o.fit[2] * fx), round(o.fit[3] * fy)]; }
    place(e);
  }
  function up() {
    if (!drag) return;
    var d = drag; drag = null;
    if (d.moved) { commit(d.before); if (d.handle && sel.length > 1) rebuild(); }
    else if (d.crop) { /* a click in crop mode changes nothing */ }
    else if (!d.handle && !d.shift && d.top && d.was.length === sel.length && sel.every(function (e) { return d.was.indexOf(e) >= 0; }) &&
             (sel.length === 1 || sel.length === unit(sel[0]).length && sel.every(function (e) { return e.group === sel[0].group; }))) {
      var at = d.under.indexOf(d.top), next = null;                        // a second click: the one underneath
      for (var k = 1; k < d.under.length && !next; k++) { var o = d.under[(at + k) % d.under.length]; if (sel.indexOf(o) < 0) next = o; }
      if (next) { cycled = { from: sel, t: Date.now() }; sel = unit(next); }
    }
    draw();
  }
  function dbl(ev) {
    if (cropping || typing) return;        // (while typing, a double-click selects a word: the browser's own)
    if (drawing) { drawEnd(false); return; }
    var pe = sel.length === 1 && sel[0].shape === "path" ? sel[0] : null;
    if (pe) {
      var pt = ev.target.getAttribute && ev.target.getAttribute("data-pt"), ap = absPts(pe);
      if (pt != null) { if (ap.length > 2) { ap.splice(+pt, 1); change(function () { setPts(pe, ap); }); rebuild(); } return; }
      var q = point(ev), best = null;                      // on the line: a new point there
      for (var i = 0; i + 1 < ap.length + (pe.closed ? 1 : 0); i++) {
        var a = ap[i], b = ap[(i + 1) % ap.length], vx = b[0] - a[0], vy = b[1] - a[1], L = vx * vx + vy * vy || 1;
        var t = Math.max(0, Math.min(1, ((q.x - a[0]) * vx + (q.y - a[1]) * vy) / L)), d = Math.hypot(a[0] + t * vx - q.x, a[1] + t * vy - q.y);
        if (!best || d < best.d) best = { d: d, i: i + 1 };
      }
      if (best && best.d * q.k < 12) { ap.splice(best.i, 0, [Math.round(q.x), Math.round(q.y)]); change(function () { setPts(pe, ap); }); rebuild(); return; }
    }
    if (cycled && Date.now() - cycled.t < 600) sel = cycled.from;          // its second click was not a wish for the one underneath
    cycled = null;
    var gm = hits(point(ev)).filter(function (x) { return sel.indexOf(x) >= 0; })[0];
    if (gm && gm.group && sel.length > 1) { sel = [gm]; draw(); status("One member of the group. Click another to have the group again."); return; }
    var top = hits(point(ev)).filter(function (x) { return sel.indexOf(x) >= 0; })[0] || hits(point(ev))[0];
    if (top && top.type === "image" && !top.rot) { crop(top); return; }
    if (top && top.type === "shape") {    // a shape: its Style panel
      if (sel.indexOf(top) < 0) sel = unit(top);
      shpWant = true; draw(); return;
    }
    var e = hits(point(ev)).filter(function (x) { return x.type === "text"; })[0];
    if (e) { sel = [e]; retype(e); }
  }

  // ---------------------------------------------------------------- live figures
  function liveSel() { return !sorter && sel.length === 1 && sel[0].type === "live" && sel[0].states ? sel[0] : null; }
  function showStep(e, i, animate) {
    liveAt = Math.max(0, Math.min(e.states.length - 1, i));
    var n = nodeOf(e);
    if (n && n.contentWindow) n.contentWindow.postMessage({ type: "deck:state", state: e.states[liveAt], animate: animate !== false }, "*");
    drawLive();
  }
  function drawLive() {
    var e = liveSel(), b = bar.live;
    b.innerHTML = "";
    if (e !== liveFor) {                   // a figure was just selected: the step named in the bar and the view
      liveFor = e;                         // in the figure must be the same one, or Use would store the wrong view
      if (e) {
        var shown = (RT.editClick != null ? RT.stageAt(cur().sec, RT.editClick) : RT.stage(cur().sec)) - 1;   // where the talk had got to
        showStep(e, Math.min(e.states.length - 1, shown), false);
        return;
      }
    }
    if (!e) { lvOpen(false); if (using) use(null); return; }
    liveAt = Math.min(liveAt, e.states.length - 1);
    lvFill();
    function btn(label, title, fn) { var x = mk("button", "", b, label); x.title = title; x.onclick = function () { fn(); x.blur(); }; }
    btn("◀", "Show the previous step", function () { showStep(e, liveAt - 1); });
    mk("span", "", b, "step " + (liveAt + 1) + " of " + e.states.length);
    btn("▶", "Show the next step", function () { showStep(e, liveAt + 1); });
    if (!using) btn("Use", "Operate the figure in place, to set up a view", function () { use(e); });
    else {
      btn("Capture step", "Keep the figure's current view as a new step after this one", function () { ask(e, "add"); });
      btn("Finish", "Stop operating the figure; the view it is left in becomes this step", function () { use(null); });
    }
    if (!using) btn("View…", "This step's view as text (the settings the figure's page is sent), to edit by hand", function () { lvOpen(!lv); });
    if (e.states.length > 1) btn("Remove step", "Remove this step", function () {
      change(function (c) { e.states.splice(liveAt, 1); stepRow(c.s, liveAt, false); opensAt(e); });
      steps(e);
      showStep(e, liveAt); if (!using) rebuild();
    });
  }
  // The "View…" panel: a step's view string, one setting per line, and the page's address.
  var lv = null;
  function lvPage(e) { var h = e.src.indexOf("#"); return h >= 0 ? e.src.slice(0, h) : e.src.split("?")[0]; }
  function lvFill() {
    var e = liveSel();
    if (!lv || !e) return;
    lv.head.textContent = "Step " + (liveAt + 1) + " of " + e.states.length + ": its view, one setting per line";
    lv.text.value = String(e.states[liveAt] || "").split("&").join("\n");
    lv.page.value = lvPage(e);
  }
  function lvApply() {
    var e = liveSel();
    if (!lv || !e) return;
    var st = lv.text.value.split(/\n/).map(function (x) { return x.trim().replace(/^&+|&+$/g, ""); }).filter(Boolean).join("&");
    var pg = lv.page.value.trim(), was = lvPage(e);
    if (!st) { status("The view is empty: nothing was changed", true); return; }
    var did = change(function () {
      e.states[liveAt] = st;
      if (pg && pg !== was) e.src = pg + e.src.slice(was.length);
      opensAt(e);
    });
    if (pg && pg !== was) rebuild();       // another page: the figure loads again
    else showStep(e, liveAt);              // the same page is sent the new view
    status(did ? "Step " + (liveAt + 1) + " changed" : "Nothing changed");
  }
  function lvOpen(yes) {
    if (!yes) { if (lv) { lv.el.remove(); lv = null; } return; }
    if (lv || !liveSel()) return;
    bpOpen(false);
    lv = { el: mk("div", "kn-ed-fmt kn-ed-lv", document.body) };
    lv.head = mk("div", "kn-ed-fmt-head", lv.el);
    lv.text = mk("textarea", "", lv.el); lv.text.spellcheck = false; lv.text.rows = 14;
    var r = mk("label", "", lv.el);
    mk("span", "", r, "Page");
    lv.page = mk("input", "", r); lv.page.type = "text"; lv.page.spellcheck = false;
    lv.page.title = "The page the figure shows. Changing it reloads the figure.";
    r = mk("div", "kn-ed-lv-row", lv.el);
    mk("button", "go", r, "Apply").onclick = lvApply;
    mk("button", "", r, "Revert").onclick = lvFill;
    mk("button", "", r, "Close").onclick = function () { lvOpen(false); };
    mk("span", "", r, "Cmd-Enter applies");
    lv.el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); lvApply(); }
      else if (ev.key === "Escape") { ev.target.blur(); }
    });
    lvFill();
  }
  function steps(e) {                      // a step was added or removed: the slide's clicks follow, without reloading the figure
    var i = deck.slides.findIndex(function (x) { return x.elements && x.elements.indexOf(e) >= 0; });
    if (i < 0) return;
    RT.clicks(i);
    bpFill();
  }
  function stepRow(s, k, add) {            // a build order that is written down gains or loses the row of stage k (from 0)
    if (!s.builds) return;
    var live = s.elements.filter(function (x) { return x.type === "live"; })[0];
    var at = [];
    s.builds.forEach(function (b, j) { if (b.do === "step") at.push(j); });
    if (add) s.builds.splice(k > 0 && at.length >= k ? at[k - 1] + 1 : at.length ? at[0] : s.builds.length, 0, { el: eid(s, live), do: "step" });
    else if (at.length) s.builds.splice(at[Math.max(0, Math.min(at.length - 1, k - 1))], 1);
  }
  function opensAt(e) {                    // the page's address opens at its first step, so nothing jumps when it loads
    if (!e.states[0] || e.states[0].indexOf("=") < 0) return;
    var h = e.src.indexOf("#");             // a page that keeps its view after "#" (the inventory): only that part changes
    if (h >= 0) { e.src = e.src.slice(0, h) + "#" + e.states[0]; return; }
    if (e.src.indexOf("?") < 0) return;
    e.src = e.src.split("?")[0] + "?" + e.states[0] + (/[?&]embed=1(&|$)/.test(e.src) ? "&embed=1" : "");
  }
  function use(e) {                        // let the pointer through to the figure, or stop
    var was = using;
    using = e;
    Array.prototype.forEach.call(document.querySelectorAll(".kn-ed-use"), function (n) { n.classList.remove("kn-ed-use"); });
    layer.classList.toggle("kn-ed-typing", !!e);
    if (e) { var n = nodeOf(e); if (n) n.classList.add("kn-ed-use"); drawLive(); }
    else if (was) ask(was, "keep");                   // the view it was left in becomes the step
  }
  function ask(e, how) {
    var i = deck.slides.findIndex(function (x) { return x.elements && x.elements.indexOf(e) >= 0; });
    var sec = RT.slidesEl.querySelector('section[data-i="' + i + '"]');
    var n = sec && Array.prototype.filter.call(sec.querySelectorAll("iframe.kn-live"), function (x) { return x.knEl === e; })[0];
    if (!n || !n.contentWindow) return;
    var a = asking = { e: e, how: how, win: n.contentWindow, i: i, at: liveAt };
    n.contentWindow.postMessage({ type: "deck:get" }, "*");
    setTimeout(function () {               // no answer: the last view the page reported, if it reported any
      if (asking !== a) return;
      if (typeof n.knState === "string") capture(n.knState);
      else { asking = null; status("The figure's view could not be read, so it was not kept (the page needs lib/deck-embed.js).", true); if (how === "keep" && cur().i === i) rebuild(); }
    }, 800);
  }
  function capture(state) {
    var a = asking, e = a.e, s = deck.slides[a.i]; asking = null;
    if (typeof state !== "string" || !state || !s) return;
    var before = { i: a.i, json: JSON.stringify(s) }, old = e.states[a.at];
    if (a.how === "add") { e.states.splice(a.at + 1, 0, state); liveAt = a.at + 1; stepRow(s, a.at + 1, true); }
    else if (state !== old) e.states[a.at] = state;
    opensAt(e); mend(s); steps(e);
    if (JSON.stringify(s) !== before.json) {
      s.owner = "here"; undo.push(before); redo = []; touch();
      status(a.how === "add" ? "Step " + (liveAt + 1) + " captured" : "Step " + (a.at + 1) + " now shows this view");
    }
    if (a.how === "keep") { if (on && cur().i === a.i) rebuild(); else if (on) RT.render(a.i); }   // (after Done: the slide is left as it is)
    else if (on) drawLive();
  }
  RT.stepFor = function (e) {              // which step a figure shows while editing
    if (liveSel() === e) return liveAt + 1;
    return RT.editClick != null && cur().s.elements.indexOf(e) >= 0 ? RT.stageAt(cur().sec, RT.editClick) : 1;
  };

  // ---------------------------------------------------------------- text
  document.addEventListener("selectionchange", function () {     // while typing: the panel shows the words selected
    if (!typing || !txt || txtBefore || document.activeElement !== typing.body) return;    // (a click in the panel moves the focus: the words stay chosen)
    var gs = getSelection(), g0 = gs.rangeCount ? gs.getRangeAt(0) : null;
    if (g0 && typing.body.contains(g0.commonAncestorContainer)) { typing.range = g0.collapsed ? null : g0.cloneRange(); txtFill(); }
  });
  function retype(e) {
    var n = nodeOf(e), body = n && n.querySelector(".kn-body");
    if (!body) return;
    typing = { e: e, node: n, body: body, before: snapshot() };
    Array.prototype.forEach.call(body.querySelectorAll(".kn-marker"), function (m) { m.contentEditable = "false"; });
    body.contentEditable = "plaintext-only";
    if (body.contentEditable !== "plaintext-only") body.contentEditable = "true";
    layer.classList.add("kn-ed-typing");
    n.classList.add("kn-ed-live");
    body.focus();
    var r = document.createRange(); r.selectNodeContents(body); r.collapse(false);
    var s = getSelection(); s.removeAllRanges(); s.addRange(r);
    draw();
  }
  function finish() {
    if (!typing) return;
    var t = typing, e = t.e, parts = [], last = 0;
    typing = null;
    function add(text, r) { if (!text) return; if (parts.length && parts[parts.length - 1].r === r) parts[parts.length - 1].t += text; else parts.push({ t: text, r: r }); }
    (function walk(node, top) {
      Array.prototype.forEach.call(node.childNodes, function (c, k) {
        if (c.nodeType === 3) add(c.nodeValue, last);
        else if (c.nodeType !== 1 || c.classList.contains("kn-marker")) return;
        else if (c.tagName === "BR") { if (c.parentNode.childNodes.length > 1 || !top) add("\n", last); }
        else {
          if ((c.tagName === "DIV" || c.tagName === "P") && parts.length && !/\n$/.test(parts[parts.length - 1].t)) add("\n", last);
          if (c.hasAttribute("data-r")) last = +c.getAttribute("data-r");
          walk(c, false);
        }
      });
    })(t.body, true);
    if (t.paint) {                         // the run properties for the part between the two marks textSet() left in the text
      var inside = false, cut = [];
      parts.forEach(function (p) {
        p.t.split(/([\uE000\uE001])/).forEach(function (x) {
          if (x === "\uE000") inside = true; else if (x === "\uE001") inside = false;
          else if (x) cut.push({ t: x, r: p.r, c: inside });
        });
      });
      parts = cut;
      if (!e.runs) e.runs = [{ t: "" }];
    }
    var text = parts.map(function (p) { return p.t; }).join("").replace(/\n+$/, "");
    if (e.runs) {
      var left = text.length;
      e.runs = parts.map(function (p) { var r = {}, src = e.runs[p.r] || {}; Object.keys(src).forEach(function (k) { r[k] = src[k]; }); r.t = p.t.slice(0, left); left -= r.t.length;
                      if (p.c) Object.keys(t.paint).forEach(function (k) {      // the words chosen: what differs from the box
                        var v = t.paint[k], base = k === "color" ? hex(e.color) : k === "weight" ? e.weight || 400 : k === "italic" ? !!e.italic : e[k];
                        if (v == null || v === base) delete r[k]; else r[k] = v;
                      });
                      return r; })
                    .filter(function (r) { return r.t; });
      if (t.paint) joinRuns(e);
    }
    var n = text.split("\n").length;
    ["markers", "paraBuilds"].forEach(function (key) {      // lists follow the paragraphs
      var a = e[key];
      if (!a || a.length === n) return;
      a = a.slice(0, n);
      while (a.length < n) a.push(a[a.length - 1]);
      e[key] = a;
    });
    if (e.markers) {                                        // numbers count again from 1
      var num = 0;
      e.markers = e.markers.map(function (m) { if (/^\d+\.$/.test(m)) { num++; return num + "."; } num = 0; return m; });
    }
    e.text = text;
    layer.classList.remove("kn-ed-typing");
    if (!text && !e.fill) { var els = cur().s.elements; els.splice(els.indexOf(e), 1); sel = []; }
    commit(t.before);
    rebuild();
  }

  // ---------------------------------------------------------------- commands
  function remove() { if (sel.length) { change(function (c) { c.s.elements = c.s.elements.filter(function (e) { return sel.indexOf(e) < 0; }); }); sel = []; rebuild(); } }
  function cloneSel(s) {                   // copies of the selection, on top of the stack, where the originals are (Alt-drag, Alt-arrow)
    var src = s.elements.filter(function (e) { return sel.indexOf(e) >= 0 && e.type !== "live"; }), gm = {};   // a slide holds one figure
    return src.map(function (e) {
      var n = JSON.parse(JSON.stringify(e)), g = e.group;
      delete n.id;                         // a build row names the original
      if (g && !gm[g]) gm[g] = s.elements.every(function (x) { return x.group !== g || src.indexOf(x) >= 0; }) ? gid(s) : "-";
      if (g) { if (gm[g] === "-") delete n.group; else n.group = gm[g]; }       // a whole group is copied as a new group
      s.elements.push(n);
      return n;
    });
  }
  function nudgeCopy(dx, dy) {             // Alt-arrow: a copy, moved; the original stays
    var made = [];
    change(function (c) { made = cloneSel(c.s); made.forEach(function (e) { e.x = round(e.x + dx); e.y = round(e.y + dy); }); });
    if (!made.length) { status("A figure is not copied this way: a slide holds one.", true); return; }
    sel = made; rebuild(); status(made.length > 1 ? made.length + " copies" : "Copy");
  }
  function nudge(dx, dy) { change(function () { sel.forEach(function (e) { e.x = round(e.x + dx); e.y = round(e.y + dy); place(e); }); }); draw(); }
  function order(how) {
    if (!sel.length) return;
    change(function (c) {
      var a = c.s.elements, pick = a.filter(function (e) { return sel.indexOf(e) >= 0; }), rest = a.filter(function (e) { return sel.indexOf(e) < 0; });
      if (how === "front") c.s.elements = rest.concat(pick);
      else if (how === "back") c.s.elements = pick.concat(rest);
      else {
        var below = function (i) { return a.slice(0, i).filter(function (e) { return sel.indexOf(e) < 0; }).length; };
        var at = how === "forward" ? Math.min(rest.length, below(a.indexOf(pick[pick.length - 1])) + 1) : Math.max(0, below(a.indexOf(pick[0])) - 1);
        c.s.elements = rest.slice(0, at).concat(pick, rest.slice(at));
      }
    });
    rebuild();
  }
  // ---- groups: elements of a slide that share a "group" name are one thing ----
  function unit(e) { return e.group ? cur().s.elements.filter(function (x) { return x.group === e.group; }) : [e]; }
  function units(list) {                   // a selection as its groups and lone elements, in its order: [{g, es}]
    var out = [], seen = {};
    list.forEach(function (e) {
      if (!e.group) out.push({ es: [e] });
      else if (!seen[e.group]) { seen[e.group] = 1; out.push({ g: e.group, es: unit(e) }); }
    });
    return out;
  }
  function gid(s) { var id; do { id = "g" + Math.random().toString(36).slice(2, 6); } while (id.length < 3 || s.elements.some(function (x) { return x.group === id; })); return id; }
  function tidy(s) {                       // a group left with one member is no group; its build rows become that member's
    var n = {};
    (s.elements || []).forEach(function (e) { if (e.group) n[e.group] = (n[e.group] || 0) + 1; });
    (s.elements || []).forEach(function (e) {
      if (!e.group || n[e.group] > 1) return;
      (s.builds || []).forEach(function (b) { if (b.grp === e.group) { delete b.grp; b.el = eid(s, e); } });
      delete e.group;
    });
  }
  function group() {
    var c = cur(), list = c.s.elements.filter(function (e) { return sel.indexOf(e) >= 0; }), merged = false;
    if (sorter || list.length < 2) { status("Select two or more things to group", true); return; }
    change(function (c) {
      var s = c.s, id = gid(s), ids = {}, grps = {}, keep = null;
      if (RT.plan(s).some(function (r) { return r.do !== "step" && r.es.some(function (e) { return list.indexOf(e) >= 0; }); })) {
        written(s);                        // the group takes the builds of the member that builds first; the others' go
        list.forEach(function (e) { if (e.id) ids[e.id] = 1; if (e.group) grps[e.group] = 1; });
        s.builds = s.builds.filter(function (b) {
          if (b.do === "step" || !(b.grp ? grps[b.grp] : ids[b.el])) return true;
          var k = b.grp ? "g" + b.grp : "e" + b.el;
          if (b.para != null) { merged = true; return false; }
          if (keep == null) keep = k;
          if (k !== keep) { merged = true; return false; }
          delete b.el; b.grp = id;
          return true;
        });
      }
      list.forEach(function (e) { e.group = id; });
      var a = s.elements, top = a.indexOf(list[list.length - 1]);          // together in the stack, where the topmost was
      s.elements = a.slice(0, top + 1).filter(function (e) { return list.indexOf(e) < 0; }).concat(list, a.slice(top + 1));
    });
    sel = list; RT.clicks(c.i); rebuild();
    status(merged ? "Grouped. The group keeps the builds of its first member to build; the others' separate builds are gone (Undo brings them back)." : "Grouped");
  }
  function ungroup() {
    var gs = {}, any = false;
    sel.forEach(function (e) { if (e.group) { gs[e.group] = 1; any = true; } });
    if (sorter || !any) { status("No group is selected", true); return; }
    var all = [];
    change(function (c) {
      var s = c.s, out = [];
      (s.builds || []).forEach(function (b) {          // a group's build row: one row per member, together
        if (!b.grp || !gs[b.grp]) { out.push(b); return; }
        s.elements.filter(function (e) { return e.group === b.grp; }).forEach(function (e, k) {
          var n = { el: eid(s, e) };
          Object.keys(b).forEach(function (key) { if (key !== "grp" && key !== "el") n[key] = b[key]; });
          if (k) { n.start = "with"; delete n.delay; }
          out.push(n);
        });
      });
      if (s.builds) s.builds = out;
      s.elements.forEach(function (e) { if (e.group && gs[e.group]) { all.push(e); delete e.group; } });
    });
    sel = all; RT.clicks(cur().i); rebuild(); status("Ungrouped");
  }
  // ---- clipboard: one for elements and slides, shared by every deck on this server ----
  function setClip(kind, items) { var c = { kind: kind, from: HERE, items: items }; try { localStorage.setItem("kn-clip", JSON.stringify(c)); } catch (x) { status("Could not copy: " + x.message, true); } return c; }
  function readClip(text) {                // what the system clipboard holds, if it is ours or an element
    var o; try { o = JSON.parse(text); } catch (x) { return null; }
    if (o && o.kind && Array.isArray(o.items)) return o;
    var list = Array.isArray(o) ? o : [o];
    if (!list.length || !list.every(function (e) { return e && /^(live|text|image|video|shape)$/.test(e.type); })) return null;
    list.forEach(function (e) {
      if (typeof e.x !== "number") e.x = 0; if (typeof e.y !== "number") e.y = 0;
      if (typeof e.w !== "number") e.w = W; if (typeof e.h !== "number") e.h = H;
    });
    return { kind: "elements", from: HERE, items: list };
  }
  function page(src) { return String(src || "").split(/[?#]/)[0]; }
  function getClip() { try { return JSON.parse(localStorage.getItem("kn-clip") || "null"); } catch (x) { return null; } }
  function own(p) { return typeof p === "string" && /^(assets|media|slides)\//.test(p); }       // a file in the deck's folder
  function refs(items, fn) {               // every file path in copied elements or slides, through fn
    items.forEach(function (it) {
      if (it.flat) it.flat = it.flat.map(fn);
      (it.elements || [it]).forEach(function (e) { ["src", "fallback"].forEach(function (k) { if (e[k]) e[k] = fn(e[k]); }); });
    });
  }
  function bring(clip) {                   // files of a copy made in another deck → this deck's folder
    if (clip.from === HERE) return Promise.resolve(clip.items);
    var src = deckName(clip.from), dst = deckName(HERE), files = [];
    if (!src || !dst) return Promise.reject(new Error("decks must be under /decks/"));
    refs(clip.items, function (p) { if (own(p)) files.push(p); return p; });
    status("Copying files…");
    return fetch("/decks/" + dst + "/copy", { method: "POST", headers: csrf({}), body: JSON.stringify({ from: src, files: files }) })
      .then(function (r) { if (!r.ok) throw new Error("the server answered " + r.status + " (needs tools/serve.py)"); return r.json(); })
      .then(function (j) {
        var missing = files.filter(function (f) { return !j.map[f]; });
        if (missing.length) throw new Error(missing.length + " file(s) not found in " + src + ", e.g. " + missing[0]);
        refs(clip.items, function (p) { return own(p) ? j.map[p] : p; });
        return clip.items;
      });
  }
  function copy() {
    var c = cur(), clip = null;
    if (sorter || !sel.length) { var list = sorter ? deck.slides.filter(function (x) { return picks.indexOf(x) >= 0; }) : [c.s]; if (list.length) { clip = setClip("slides", list); status(list.length + (list.length > 1 ? " slides" : " slide") + " copied"); } }
    else clip = setClip("elements", c.s.elements.filter(function (e) { return sel.indexOf(e) >= 0; }));
    return clip;
  }
  function paste(given) {
    var clip = given ? JSON.parse(JSON.stringify(given)) : getClip();      // copies, never the things copied themselves
    if (!clip || !clip.items || !clip.items.length) return;
    var one = clip.kind === "elements" && clip.items.length === 1 && clip.items[0];
    if (one && one.type === "live" && one.states && sel.length === 1 && sel[0].type === "live" && sel[0].states && page(sel[0].src) === page(one.src) && !sorter) {
      var e = sel[0], added = 0;                 // another view of the selected figure: its next step
      change(function () { one.states.forEach(function (st) { if (e.states.indexOf(st) < 0) { e.states.push(st); added++; } }); });
      liveAt = e.states.length - 1;
      rebuild(); status(added ? "Step " + e.states.length + " added to the figure" : "The figure already has that view");
      return;
    }
    bring(clip).then(function (items) {
      if (clip.kind === "slides") {
        var before = snapshotAll(), src = deckName(clip.from), at = cur().i + 1;
        if (sorter && picks.length) at = Math.max.apply(null, picks.map(function (x) { return deck.slides.indexOf(x); })) + 1;
        picks = items.map(function (x) {
          var o = { owner: "here" };
          if (x.kn != null || x.from) o.from = x.from || { deck: src, kn: x.kn };
          Object.keys(x).forEach(function (k) { if (k !== "kn" && k !== "owner" && k !== "from" && k !== "check") o[k] = x[k]; });
          return o;
        });
        Array.prototype.splice.apply(deck.slides, [at, 0].concat(picks));
        commitAll(before, at);
      } else if (!sorter && editable(cur().s)) {
        change(function (c) {
          function taken(e) { return c.s.elements.some(function (o) { return o.type === e.type && o.x === e.x && o.y === e.y && o.w === e.w && o.h === e.h; }); }
          var off = 0, gn = {}, gm = {};         // a pasted group is a new group; a lone member of one is alone
          items.forEach(function (e) { if (e.group) gn[e.group] = (gn[e.group] || 0) + 1; });
          items.forEach(function (e) { if (!e.group) return; if (gn[e.group] < 2) { delete e.group; return; } gm[e.group] = gm[e.group] || gid(c.s) + Object.keys(gm).length; e.group = gm[e.group]; });
          while (items.some(function (e) { return taken({ type: e.type, x: e.x + off, y: e.y + off, w: e.w, h: e.h }); })) off += 24;
          sel = items.map(function (e) { e.x += off; e.y += off; c.s.elements.push(e); return e; });
        });
        rebuild();
      }
    }).catch(function (x) { status("Paste failed: " + x.message, true); });
  }

  // ---------------------------------------------------------------- build order
  // The "Builds" panel: the slide's rows in order (deck.js: builds). A slide that came from the
  // importer has its order in the elements; the first change here writes it down as rows.
  var bp = null, bpRow = -1, bpDrag = -1, bpTimer = 0, bpKeys = false;   // bpKeys: a row was clicked last, so Delete means the row
  function bpRemove() {                    // Delete on the selected row
    var i = bpRow, rows = RT.plan(cur().s), r = rows[i];
    if (!r) return;
    if (r.do === "step") { status("A figure's stages stay in the order. To drop one, select the figure and use Remove step.", true); return; }
    bpChange(function (b) { b.splice(i, 1); bpRow = Math.min(i, b.length - 1); });
    status("Removed from the build order (the thing itself stays on the slide)");
  }
  function parasOf(e) { return (e.runs ? e.runs.map(function (r) { return r.t; }).join("") : e.text || "").split("\n").length; }
  function eid(s, e) {                     // the name a build row knows an element by
    if (e.id) return e.id;
    var id;
    do { id = Math.random().toString(36).slice(2, 6); } while (!id || s.elements.some(function (x) { return x.id === id; }));
    e.id = id;
    return id;
  }
  function mend(s) {                       // after any change: rows whose element went are dropped, a figure has a row per stage
    if (!s.builds || !s.elements) return;
    var seen = {};
    s.elements.forEach(function (e) {
      if (e.id) { if (seen[e.id]) delete e.id; else seen[e.id] = e; }
      delete e.build; delete e.paraBuilds;
    });
    var live = s.elements.filter(function (e) { return e.type === "live"; })[0], n = 1, max = live ? (live.states ? live.states.length : live.steps || 1) : 1;
    var grp = {};
    s.elements.forEach(function (e) { if (e.group) grp[e.group] = 1; });
    s.builds = s.builds.filter(function (b) {
      if (b.grp) return b.do !== "step" && !!grp[b.grp];
      var e = seen[b.el];
      if (!e) return false;
      if (b.do === "step") return e === live && n < max && !!(++n);
      return b.para == null || b.para < parasOf(e);
    });
    for (; n < max; n++) s.builds.push({ el: eid(s, live), do: "step" });
    if (!s.builds.length) delete s.builds;
  }
  function written(s) {                    // the order as rows in the slide, made from the importer's form if need be
    if (s.builds) return s.builds;
    var rows = RT.plan(s), seen = {};
    s.elements.forEach(function (e) { if (e.id) { if (seen[e.id]) delete e.id; else seen[e.id] = 1; } });   // a pasted copy keeps no name
    s.builds = rows.map(function (r) {
      var b = { el: eid(s, r.e), do: r.do };
      if (r.para != null) b.para = r.para;
      if (r.start !== "click") b.start = r.start;
      return b;
    });
    s.elements.forEach(function (e) { delete e.build; delete e.paraBuilds; });
    return s.builds;
  }
  function rowName(r) {
    if (!r.grp) return nameOf(r.e, r.para) + (r.para != null ? "  ¶" + (r.para + 1) : "");
    var t = r.es.filter(function (e) { return e.type === "text"; })[0] || r.es[0];
    return "Group of " + r.es.length + ": " + nameOf(t);
  }
  function nameOf(e, para) {
    if (e.type === "text") {
      var t = e.runs ? e.runs.map(function (r) { return r.t; }).join("") : e.text || "";
      if (para != null) t = t.split("\n")[para] || "";
      return "“" + t.replace(/\s+/g, " ").trim().slice(0, 30) + "”";
    }
    if (e.type === "image") return String(e.src || "").split("/").pop().slice(0, 30);
    if (e.type === "shape") return { path: "Path", oval: "Oval" }[e.shape] || (e.radius >= 50 ? "Oval" : "Box");
    return { video: "Movie", live: "Figure" }[e.type] || e.type;
  }
  function bpChange(fn) {                  // a change to the rows, then the slide and the panel follow
    var did = change(function (c) { fn(written(c.s), c.s); });
    RT.clicks(cur().i);
    if (bpRow >= 0) RT.editClick = clickOf(bpRow);        // the slide shows the click of the row being worked on
    bpShow();
    return did;
  }
  function clickOf(i) { var g = 0; RT.plan(cur().s).forEach(function (r, j) { if (r.start === "click" && j <= i) g++; }); return g; }
  function bpShow() { clearTimeout(bpTimer); RT.apply(cur().sec, {}); draw(); bpFill(); }
  function bpSet(b, key, v, plain) { if (v === plain || v == null || v === "") delete b[key]; else b[key] = v; }
  // Build what is selected in or out. A group is one row. Several things: `mode` says how they
  // run ("with": together; "seq": one after another, each dissolving; "clicks": a click each),
  // and without it the question is asked first.
  function bpAdd(what, mode) {
    var c = cur(), us = units(sel);
    bpAsk(null);
    if (sorter || !editable(c.s)) return;
    if (!us.length) { status("Select what should build " + (what === "out" ? "out" : "in") + " first", true); return; }
    if (what === "paras" && (sel.length !== 1 || sel[0].type !== "text")) { status("By paragraph is for one text box", true); return; }
    if (what !== "paras" && us.length > 1 && !mode) { bpAsk(what, us.length); return; }
    bpOpen(true);
    bpChange(function (rows, s) {
      if (what === "paras") for (var k = 0; k < parasOf(sel[0]); k++) rows.push({ el: eid(s, sel[0]), do: "in", para: k });
      else us.forEach(function (u, k) {
        var b = u.g ? { grp: u.g } : { el: eid(s, u.es[0]) };
        b.do = what === "out" ? "out" : "in";
        if (k && mode !== "clicks") b.start = mode === "seq" ? "after" : "with";
        if (mode === "seq") b.fx = "dissolve";
        rows.push(b);
      });
      bpRow = rows.length - 1;
    });
  }
  var asker = null;
  function bpAsk(what, n) {                // several things to build: together, or in turn?
    if (asker) { asker.remove(); asker = null; }
    if (!what) return;
    asker = mk("div", "kn-ed-fmt kn-ed-ask", document.body);
    mk("div", "kn-ed-fmt-head", asker, "Build " + what + " " + n + " things");
    function opt(label, hint, mode) {
      var b = mk("button", "", asker); mk("b", "", b, label); mk("span", "", b, hint);
      b.onclick = function () { bpAdd(what, mode); };
    }
    opt("Together", "one click, all at once", "with");
    opt("One after another", "one click, each dissolves " + what + " in turn", "seq");
    opt("One click each", "a click for every one", "clicks");
    mk("button", "cancel", asker, "Cancel").onclick = function () { bpAsk(null); };
    mk("div", "kn-ed-bhint", asker, "Timing and effect can be changed afterwards in the build order. To have them always act as one thing, group them (Arrange).");
  }
  function bpFill() {
    if (!bp) return;
    var c = cur(), rows = editable(c.s) ? RT.plan(c.s) : [], g = 0, stage = 1, total = rows.filter(function (r) { return r.start === "click"; }).length;
    if (RT.editClick != null && RT.editClick > total) RT.editClick = total;
    if (bpRow >= rows.length) bpRow = -1;
    bp.at.textContent = RT.editClick == null ? "everything" : RT.editClick === 0 ? "on arrival" : "click " + RT.editClick + " of " + total;
    bp.list.innerHTML = "";
    var fig = editable(c.s) && c.s.elements.filter(function (e) { return e.type === "live" && !e.states && e.steps > 1; })[0];
    if (fig) {                             // stage 1 plays when the slide arrives and has no row: its time is set here
      var f1 = mk("div", "kn-ed-bdet kn-ed-b1", bp.list);
      mk("span", "", f1, "Figure → stage 1, on arrival: plays for");
      var t1 = mk("input", "", f1); t1.type = "number"; t1.min = 0; t1.max = 120; t1.step = 0.5; t1.value = fig.dur || ""; t1.placeholder = "own";
      t1.title = "How long the figure's first stage plays (empty: its own pace)";
      mk("span", "", f1, "s");
      t1.onchange = function () { t1.blur(); change(function () { bpSet(fig, "dur", Math.max(0, Math.min(120, +t1.value || 0)), 0); }); RT.apply(c.sec, {}); bpFill(); };
    }
    if (!rows.length && !fig) mk("div", "kn-ed-bnone", bp.list, "Nothing builds on this slide. Select something on it, then Build in or Build out.");
    rows.forEach(function (r, i) {
      if (r.start === "click") g++;
      if (r.do === "step") stage++;
      var mine_ = g, row = mk("div", "kn-ed-brow" + (i === bpRow ? " sel" : "") + (r.es.some(function (e) { return sel.indexOf(e) >= 0; }) ? " hit" : ""), bp.list);
      row.draggable = true; row.knEls = r.es;
      var num = mk("span", "num", row, r.start === "click" ? String(g) : g === 0 && i === 0 ? "•" : "");
      num.title = r.start === "click" ? "Click " + g : g === 0 ? "When the slide arrives" : "";
      mk("span", "what", row, r.do === "step" ? "Figure → stage " + stage : (r.do === "in" ? "In  " : "Out  ") + rowName(r));
      if (r.do === "step") {               // how long the figure plays this stage, in the row itself
        mk("span", "s", row, "plays");
        var tk = mk("input", "play", row); tk.type = "number"; tk.min = 0; tk.max = 120; tk.step = 0.5; tk.value = r.dur || ""; tk.placeholder = "own";
        tk.title = "How long the figure plays this stage, in seconds. A figure that animates in its own time draws at this pace (empty: its own pace), and a row set to After previous waits this long";
        mk("span", "s", row, "s");
        tk.onchange = function () { tk.blur(); bpRow = i; bpChange(function (b) { bpSet(b[i], "dur", Math.max(0, Math.min(120, +tk.value || 0)), 0); }); };
      }
      var st = mk("select", "", row);
      [["click", "On click"], ["with", "With previous"], ["after", "After previous"]].forEach(function (o) { mk("option", "", st, o[1]).value = o[0]; });
      st.value = r.start;
      st.onchange = function () { st.blur(); bpRow = i; bpChange(function (b) { bpSet(b[i], "start", st.value, "click"); }); };
      var dl = mk("input", "", row); dl.type = "number"; dl.min = 0; dl.max = 60; dl.step = 0.5; dl.value = r.delay; dl.title = "Delay: seconds to wait before this row starts";
      dl.onchange = function () { dl.blur(); bpRow = i; bpChange(function (b) { bpSet(b[i], "delay", Math.max(0, Math.min(60, +dl.value || 0)), 0); }); };
      mk("span", "s", row, "s");
      row.addEventListener("click", function (ev) {
        if (/^(SELECT|INPUT|BUTTON|OPTION)$/.test(ev.target.tagName)) return;
        bpRow = i; RT.editClick = mine_; bpKeys = true;
        if (r.e.type !== "live" || r.e.states) sel = r.es.slice();
        bpShow();
      });
      row.addEventListener("dragstart", function (ev) { bpDrag = i; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", String(i)); });
      row.addEventListener("dragover", function (ev) { if (bpDrag < 0 || bpDrag === i) return; ev.preventDefault(); row.classList.add(bpDrag < i ? "below" : "above"); });
      row.addEventListener("dragleave", function () { row.classList.remove("above", "below"); });
      row.addEventListener("dragend", function () { bpDrag = -1; });
      row.addEventListener("drop", function (ev) {
        ev.preventDefault();
        var from = bpDrag; bpDrag = -1;
        if (from < 0 || from === i) return;
        bpRow = i;
        bpChange(function (b) { b.splice(i, 0, b.splice(from, 1)[0]); });
      });
      if (i !== bpRow) return;
      var det = mk("div", "kn-ed-bdet", bp.list);          // the selected row's details
      if (r.do === "step") {
        mk("span", "", det, "Set how long this stage plays in the row above (empty: the figure's own pace).");
      } else {
        mk("span", "", det, "Effect");
        var fx = mk("select", "", det);
        mk("option", "", fx, r.do === "in" ? "Appear" : "Disappear").value = "";
        mk("option", "", fx, "Dissolve").value = "dissolve";
        fx.value = r.fx;
        var du = mk("input", "", det); du.type = "number"; du.min = 0.1; du.max = 20; du.step = 0.5; du.value = r.dur || 1; du.disabled = !r.fx;
        mk("span", "", det, "s");
        fx.onchange = function () { fx.blur(); bpChange(function (b) { bpSet(b[i], "fx", fx.value, ""); if (!fx.value) delete b[i].dur; }); };
        du.onchange = function () { du.blur(); bpChange(function (b) { bpSet(b[i], "dur", Math.max(0.1, Math.min(20, +du.value || 1)), 1); }); };
        mk("button", "", det, "Remove").onclick = function () { bpRow = -1; bpChange(function (b) { b.splice(i, 1); }); };
      }
      mk("button", "", det, "▶ Play").onclick = function () {       // this row's click, as the talk shows it
        clearTimeout(bpTimer);
        var end = RT.apply(c.sec, { present: true, play: true, at: mine_ });
        bpTimer = setTimeout(function () { if (bp) RT.apply(cur().sec, {}); }, end * 1000 + 1200);
      };
    });
    var one = sel.length === 1 && sel[0].type === "text" && parasOf(sel[0]) > 1;
    bp.para.style.display = one ? "" : "none";
  }
  function bpMark() {                      // the rows of what is selected on the slide
    if (!bp) return;
    Array.prototype.forEach.call(bp.list.querySelectorAll(".kn-ed-brow"), function (n) { n.classList.toggle("hit", n.knEls.some(function (e) { return sel.indexOf(e) >= 0; })); });
    bp.para.style.display = sel.length === 1 && sel[0].type === "text" && parasOf(sel[0]) > 1 ? "" : "none";
  }
  function bpStep(d) {                     // which click the slide is shown at: everything, on arrival, 1, 2, …
    var total = RT.plan(cur().s).filter(function (r) { return r.start === "click"; }).length;
    var at = RT.editClick == null ? -1 : RT.editClick;
    at += d;
    RT.editClick = at < -1 ? total : at > total ? null : at < 0 ? null : at;
    bpShow();
  }
  function bpOpen(yes) {
    if (!yes) {
      if (!bp) return;
      clearTimeout(bpTimer); bp.el.remove(); bp = null; bpRow = -1; bpKeys = false; RT.editClick = null;
      RT.apply(cur().sec, {});
      return;
    }
    if (bp) return;
    lvOpen(false);
    bp = { el: mk("div", "kn-ed-fmt kn-ed-bp", document.body) };
    var head = mk("div", "kn-ed-fmt-head", bp.el);
    mk("span", "", head, "Build order");
    var nav = mk("span", "kn-ed-bnav", head);
    mk("span", "", nav, "Showing");
    var a = mk("button", "", nav, "◀"); a.title = "Show the slide one click earlier"; a.onclick = function () { bpStep(-1); a.blur(); };
    bp.at = mk("span", "at", nav);
    var z = mk("button", "", nav, "▶"); z.title = "Show the slide one click later"; z.onclick = function () { bpStep(1); z.blur(); };
    var cols = mk("div", "kn-ed-bcols", bp.el);
    mk("span", "", cols, "Starts"); mk("span", "d", cols, "Delay");
    bp.list = mk("div", "kn-ed-blist", bp.el);
    var r = mk("div", "kn-ed-lv-row", bp.el);
    mk("button", "go", r, "Build in").onclick = function () { bpAdd("in"); };
    mk("button", "", r, "Build out").onclick = function () { bpAdd("out"); };
    bp.para = mk("button", "", r, "In, by paragraph"); bp.para.onclick = function () { bpAdd("paras"); };
    mk("button", "", r, "Close").onclick = function () { bpOpen(false); };
    mk("div", "kn-ed-bhint", bp.el, "Drag a row to reorder. Click a row for its details; Delete removes it from the order. What is not showing at the chosen click is drawn faint.");
    bpFill();
  }

  // ---------------------------------------------------------------- slide settings
  // The "Slide…" panel: background colour and the transition to the next slide, for the slide
  // showing or, in the sorter, for every selected slide. One undo step per choice (a drag in
  // the colour picker counts as one).
  var fmt = null, fmtBefore = null, colBefore = null;
  function fmtSlides() { return sorter ? deck.slides.filter(function (x) { return picks.indexOf(x) >= 0; }) : [cur().s]; }
  function hex(c) {
    c = String(c || "").trim().toLowerCase();
    if (/^[0-9a-f]{3}$|^[0-9a-f]{6}$/.test(c)) c = "#" + c;
    if (/^#[0-9a-f]{3}$/.test(c)) c = "#" + c[1] + c[1] + c[2] + c[2] + c[3] + c[3];
    return /^#[0-9a-f]{6}$/.test(c) ? c : null;
  }
  function fmtSet(fn, done) {
    var list = fmtSlides();
    if (!list.length) return;
    if (!fmtBefore) fmtBefore = snapshotAll();
    list.forEach(function (x) { fn(x); if (sorter) RT.render(deck.slides.indexOf(x)); });
    if (!sorter) rebuild();
    if (!done) return;
    var b = fmtBefore; fmtBefore = null;
    if (JSON.stringify(deck.slides) !== b.all) {
      list.forEach(mine);
      undo.push(b); redo = [];
      if (undo.length > 200) undo.shift();
      touch();
    }
    if (sorter) drawSorter(); else fmtFill();
  }
  function fmtFill() {                     // the panel shows the (first) slide it applies to
    if (!fmt) return;
    var list = fmtSlides(), x = list[0];
    fmt.head.textContent = !x ? "No slide selected" : list.length > 1 ? list.length + " slides selected" : "Slide " + (deck.slides.indexOf(x) + 1);
    [fmt.col, fmt.hex, fmt.tr, fmt.dur].forEach(function (n) { n.disabled = !x; });
    if (!x) return;
    var bg = hex(x.bg) || "#000000";
    fmt.col.value = bg;
    if (document.activeElement !== fmt.hex) fmt.hex.value = bg;
    fmt.tr.value = x.dissolve ? "dissolve" : "cut";
    if (document.activeElement !== fmt.dur) fmt.dur.value = x.dissolve || 1;
    fmt.dur.disabled = !x.dissolve;
    var n = {}, used;
    deck.slides.forEach(function (y) { var c = hex(y.bg); if (c) n[c] = (n[c] || 0) + 1; });
    used = Object.keys(n).sort(function (a, b) { return n[b] - n[a]; });
    used = ["#000000", "#ffffff"].concat(used.filter(function (c) { return c !== "#000000" && c !== "#ffffff"; })).slice(0, 10);
    fmt.sw.innerHTML = "";
    used.forEach(function (c) {
      var b = mk("button", c === bg ? "on" : "", fmt.sw);
      b.style.background = c; b.title = c + (n[c] ? ": on " + n[c] + " slide" + (n[c] > 1 ? "s" : "") : "");
      b.onclick = function () { fmtSet(function (y) { y.bg = c; }, true); b.blur(); };
    });
  }
  function fmtOpen(yes) {
    if (!yes) { if (fmt) { fmt.el.remove(); fmt = null; fmtBefore = null; } return; }
    if (fmt) return;
    fmt = { el: mk("div", "kn-ed-fmt", document.body) };
    fmt.head = mk("div", "kn-ed-fmt-head", fmt.el);
    var r = mk("label", "", fmt.el);
    mk("span", "", r, "Background");
    fmt.col = mk("input", "", r); fmt.col.type = "color";
    fmt.hex = mk("input", "", r); fmt.hex.type = "text"; fmt.hex.size = 8; fmt.hex.spellcheck = false;
    fmt.sw = mk("div", "kn-ed-fmt-sw", fmt.el);
    fmt.sw.title = "Black, white, and the backgrounds this deck already uses";
    r = mk("label", "", fmt.el);
    mk("span", "", r, "To the next slide");
    fmt.tr = mk("select", "", r);
    mk("option", "", fmt.tr, "Cut").value = "cut"; mk("option", "", fmt.tr, "Dissolve").value = "dissolve";
    fmt.dur = mk("input", "", r); fmt.dur.type = "number"; fmt.dur.min = 0.1; fmt.dur.max = 10; fmt.dur.step = 0.5;
    mk("span", "kn-ed-fmt-s", r, "s");
    fmt.col.addEventListener("input", function () { var c = fmt.col.value; fmt.hex.value = c; fmtSet(function (y) { y.bg = c; }, false); });
    fmt.col.addEventListener("change", function () { var c = fmt.col.value; fmtSet(function (y) { y.bg = c; }, true); });
    fmt.hex.addEventListener("change", function () {
      var c = hex(fmt.hex.value);
      fmt.hex.blur();
      if (c) fmtSet(function (y) { y.bg = c; }, true); else { status("A colour is written like #1a2b3c", true); fmtFill(); }
    });
    function trans() {
      var d = fmt.tr.value === "dissolve" ? Math.max(0.1, Math.min(10, +fmt.dur.value || 1)) : 0;
      fmt.tr.blur(); fmt.dur.blur();
      fmtSet(function (y) { if (d) y.dissolve = d; else delete y.dissolve; }, true);
    }
    fmt.tr.addEventListener("change", trans);
    fmt.dur.addEventListener("change", trans);
    fmtFill();
  }

  // ---------------------------------------------------------------- slides
  function removeSlides() {
    if (cropping) crop(null);
    var list = sorter ? picks.slice() : [cur().s];       // in the sorter: the selected slides; otherwise the one showing
    if (!list.length) { status("Select a slide to delete first", true); return; }
    if (list.length >= deck.slides.length) { status("A deck keeps at least one slide", true); return; }
    var before = snapshotAll(), at = deck.slides.indexOf(list[0]);
    list.forEach(function (x) { deck.slides.splice(deck.slides.indexOf(x), 1); });
    picks = [];
    commitAll(before, Math.min(at, deck.slides.length - 1));
    status((list.length > 1 ? list.length + " slides" : "Slide") + " deleted. Undo (Cmd-Z) brings " + (list.length > 1 ? "them" : "it") + " back");
  }
  function addSlide() {
    var before = snapshotAll(), at = cur().i + 1;
    if (sorter && picks.length) at = deck.slides.indexOf(picks[picks.length - 1]) + 1;
    var n = { owner: "here", bg: cur().s.bg || "#000000", use: "elements", elements: [] };
    deck.slides.splice(at, 0, n); picks = [n];
    commitAll(before, at);
  }
  function moveSlides(to) {                // picks, kept in order, to before the slide now at index `to`
    var before = snapshotAll(), list = deck.slides.filter(function (x) { return picks.indexOf(x) >= 0; });
    var target = deck.slides[to], rest = deck.slides.filter(function (x) { return picks.indexOf(x) < 0; });
    while (target && picks.indexOf(target) >= 0) target = deck.slides[++to];
    var at = target ? rest.indexOf(target) : rest.length;
    deck.slides.length = 0;
    rest.slice(0, at).concat(list, rest.slice(at)).forEach(function (x) { deck.slides.push(x); });
    commitAll(before, at);
  }
  function drawSorter() {
    var here = cur().i;
    fmtFill();
    sorter.innerHTML = "";
    deck.slides.forEach(function (s, i) {
      var t = mk("div", "kn-ed-thumb" + (picks.indexOf(s) >= 0 ? " picked" : "") + (i === here ? " current" : ""), sorter);
      t.knSlide = s;
      var f = mk("div", "kn-ed-frame", t);
      if (s.owner !== "here" && s.flat && s.flat.length) {             // untouched since import: Keynote's render
        var im = mk("img", "", f); im.loading = "lazy"; im.draggable = false; im.src = s.flat[s.flat.length - 1];
      } else {                                                          // drawn small from its data
        var st = mk("div", "kn-stack", f);
        RT.fillStack(st, f, s);
        Array.prototype.forEach.call(st.querySelectorAll("img[data-kn-src]"), function (n) { n.loading = "lazy"; n.draggable = false; });
        RT.fetchAll(st);
        Array.prototype.forEach.call(st.querySelectorAll("iframe"), function (n) { var d = mk("div", "kn-el kn-ed-livemark", null, "live"); d.style.cssText = n.style.cssText; n.replaceWith(d); });
      }
      mk("div", "kn-ed-num", t, String(i + 1));
    });
    bar.where.textContent = deck.slides.length + " slides" + (picks.length ? ", " + picks.length + " selected" : "");
  }
  function openSorter(yes) {
    finish(); sel = [];
    if (yes && !sorter) {
      sorter = mk("div", "kn-ed-sorter", document.querySelector(".reveal"));
      sorter.addEventListener("pointerdown", sDown); sorter.addEventListener("pointermove", sMove);
      sorter.addEventListener("pointerup", sUp); sorter.addEventListener("dblclick", function (ev) { var t = thumbAt(ev); if (t) { Reveal.slide(deck.slides.indexOf(t.knSlide)); openSorter(false); } });
      picks = [cur().s]; anchor = picks[0];
      drawSorter();
      var c = sorter.querySelector(".current"); if (c) c.scrollIntoView({ block: "center" });
    } else if (!yes && sorter) { sorter.remove(); sorter = null; prepare(); draw(); fmtFill(); }
  }
  function thumbAt(ev) { var t = ev.target; while (t && t !== sorter && !t.knSlide) t = t.parentNode; return t && t.knSlide ? t : null; }
  function sDown(ev) {
    var t = thumbAt(ev);
    if (ev.button) return;
    if (!t) { picks = []; drawSorter(); return; }
    var s = t.knSlide, i = deck.slides.indexOf(s);
    sdrag = { x: ev.clientX, y: ev.clientY, s: s, plain: !ev.shiftKey && !ev.metaKey && !ev.ctrlKey, was: picks.indexOf(s) >= 0, moved: false, to: null };
    if (ev.shiftKey && anchor) { var a = deck.slides.indexOf(anchor); picks = deck.slides.slice(Math.min(a, i), Math.max(a, i) + 1); }
    else if (ev.metaKey || ev.ctrlKey) { if (sdrag.was) picks = picks.filter(function (x) { return x !== s; }); else picks.push(s); anchor = s; }
    else if (!sdrag.was) { picks = [s]; anchor = s; }
    try { sorter.setPointerCapture(ev.pointerId); } catch (x) {}
    drawSorter();
  }
  function sMove(ev) {
    if (!sdrag || !sdrag.plain) return;
    if (!sdrag.moved && Math.abs(ev.clientX - sdrag.x) + Math.abs(ev.clientY - sdrag.y) < 8) return;
    sdrag.moved = true;
    var best = null;
    Array.prototype.forEach.call(sorter.children, function (t, i) {
      var r = t.getBoundingClientRect();
      t.classList.remove("drop-before", "drop-after");
      if (ev.clientY >= r.top && ev.clientY <= r.bottom + 12 && ev.clientX >= r.left - 12 && ev.clientX <= r.right + 12) best = { t: t, i: i, after: ev.clientX > r.left + r.width / 2 };
    });
    sdrag.to = best ? best.i + (best.after ? 1 : 0) : null;
    if (best) best.t.classList.add(best.after ? "drop-after" : "drop-before");
  }
  function sUp() {
    var d = sdrag; sdrag = null;
    if (!d) return;
    if (d.moved && d.to != null) moveSlides(d.to);
    else if (!d.moved && d.plain && d.was && picks.length > 1) { picks = [d.s]; anchor = d.s; drawSorter(); }
    else if (d.moved) drawSorter();
  }
  function sKey(ev, cmd, k) {              // keys while the sorter is open; true if used
    var i = picks.length ? deck.slides.indexOf(picks[picks.length - 1]) : cur().i, per = 1;
    if (sorter.children.length > 1) { var y0 = sorter.children[0].offsetTop; per = Array.prototype.filter.call(sorter.children, function (t) { return t.offsetTop === y0; }).length; }
    function pick(j) { j = Math.max(0, Math.min(deck.slides.length - 1, j)); var s = deck.slides[j];
      if (ev.shiftKey && anchor) { var a = deck.slides.indexOf(anchor); picks = deck.slides.slice(Math.min(a, j), Math.max(a, j) + 1); if (a > j) picks.reverse(); }
      else { picks = [s]; anchor = s; }
      drawSorter(); sorter.children[j].scrollIntoView({ block: "nearest" }); }
    if (cmd && k === "a") { picks = deck.slides.slice(); drawSorter(); }
    else if (cmd) return false;
    else if (k === "s" || k === "S" || k === "Escape") openSorter(false);
    else if (k === "Enter") { Reveal.slide(i); openSorter(false); }
    else if (k === "Backspace" || k === "Delete") removeSlides();
    else if (k === "ArrowLeft") pick(i - 1); else if (k === "ArrowRight") pick(i + 1);
    else if (k === "ArrowUp") pick(i - per); else if (k === "ArrowDown") pick(i + per);
    else return false;
    return true;
  }

  // ---------------------------------------------------------------- shapes
  // Insert ▸ Shape: a box or an oval in the middle of the slide, or a path drawn by clicking its
  // points. New shapes take the style last set in the Style panel (kept in this browser).
  // The Style panel (bar ▸ Style…) sets fill, line and the rest for every selected shape.
  var drawing = null, shp = null, shpWant = false, shpBefore = null;
  var STYLE = ["fill", "fillOp", "stroke", "strokeW", "strokeOp", "dash"];
  function lastStyle() {
    var st = null;
    try { st = JSON.parse(localStorage.getItem("kn-shape-style") || "null"); } catch (x) {}
    return st && typeof st === "object" ? st : { stroke: "#e53935", strokeW: 6 };
  }
  function remember(e) {
    var st = {};
    STYLE.forEach(function (k) { if (e[k] != null) st[k] = e[k]; });
    try { localStorage.setItem("kn-shape-style", JSON.stringify(st)); } catch (x) {}
  }
  function styled(e, path) {
    var st = lastStyle();
    STYLE.forEach(function (k) { if (st[k] != null && !(path && /^fill/.test(k))) e[k] = st[k]; });
    if (path && !e.stroke) { e.stroke = "#e53935"; e.strokeW = 6; }
    if (!e.fill && !e.stroke) e.stroke = "#e53935";
    return e;
  }
  function shapesSel() { return sel.filter(function (e) { return e.type === "shape"; }); }
  function addShape(kind) {
    if (!editable(cur().s)) return;
    var e = styled({ type: "shape", shape: kind, x: W / 2 - 240, y: H / 2 - 160, w: 480, h: 320 });
    change(function (c) { c.s.elements.push(e); });
    sel = [e]; shpWant = true; rebuild();
  }
  function addPath() {
    if (!editable(cur().s)) return;
    sel = []; drawing = { pts: [], at: null };
    layer.classList.add("kn-ed-drawing");
    status("Click the path's points (Shift: straight or 45°). Double-click or Enter ends it, a click on its first point closes it. Esc: give up.");
    draw();
  }
  function constrain(p, shift) {           // Shift: the line from the last point is horizontal, vertical or at 45°
    var last = drawing && drawing.pts[drawing.pts.length - 1];
    if (!shift || !last) return { x: Math.round(p.x), y: Math.round(p.y), k: p.k };
    var dx = p.x - last[0], dy = p.y - last[1], a = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * Math.PI / 4, r = Math.hypot(dx, dy);
    return { x: Math.round(last[0] + r * Math.cos(a)), y: Math.round(last[1] + r * Math.sin(a)), k: p.k };
  }
  function drawAdd(p, shift) {
    var q = constrain(p, shift), f = drawing.pts[0];
    if (f && drawing.pts.length > 2 && Math.hypot(q.x - f[0], q.y - f[1]) * q.k < 10) { drawEnd(true); return; }
    drawing.pts.push([q.x, q.y]);
    draw();
  }
  function drawDraft() {                   // the path so far, and the line to the pointer
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "kn-ed-draft"); svg.setAttribute("width", W); svg.setAttribute("height", H);
    var ps = drawing.pts.concat(drawing.at && drawing.pts.length ? [[drawing.at.x, drawing.at.y]] : []);
    var pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    pl.setAttribute("points", ps.map(function (q) { return q.join(","); }).join(" "));
    svg.appendChild(pl); layer.appendChild(svg);
  }
  function drawEnd(closed) {
    var pts = [];
    drawing.pts.forEach(function (q) { var l = pts[pts.length - 1]; if (!l || Math.hypot(q[0] - l[0], q[1] - l[1]) > 2) pts.push(q); });   // (a double-click's clicks add the same point twice)
    drawing = null; layer.classList.remove("kn-ed-drawing"); status("");
    if (pts.length < 2) { draw(); return; }
    var e = styled({ type: "shape", shape: "path" }, !closed);
    if (closed) e.closed = true;
    setPts(e, pts);
    change(function (c) { c.s.elements.push(e); });
    sel = [e]; shpWant = true; rebuild();
  }
  function absPts(e) { return (e.pts || []).map(function (q) { return [e.x + q[0] * e.w, e.y + q[1] * e.h]; }); }
  function setPts(e, ap) {                 // a path from points on the slide: its frame is their bounds
    var xs = ap.map(function (q) { return q[0]; }), ys = ap.map(function (q) { return q[1]; });
    var x0 = Math.min.apply(null, xs), y0 = Math.min.apply(null, ys), w = Math.max.apply(null, xs) - x0, h = Math.max.apply(null, ys) - y0;
    if (w < 2) { x0 -= 1; w = 2; }         // a straight line across: its frame has some width all the same
    if (h < 2) { y0 -= 1; h = 2; }
    e.x = round(x0); e.y = round(y0); e.w = round(w); e.h = round(h);
    e.pts = ap.map(function (q) { return [Math.round((q[0] - e.x) / e.w * 1e4) / 1e4, Math.round((q[1] - e.y) / e.h * 1e4) / 1e4]; });
  }
  function shpSet(fn, done) {              // every selected shape; a slider or picker moving is one undo step when let go
    var list = shapesSel();
    if (!list.length) return;
    if (!shpBefore) shpBefore = snapshot();
    list.forEach(function (e) { fn(e); place(e); });
    if (done) { var b = shpBefore; shpBefore = null; commit(b); remember(list[0]); shpFill(); }
  }
  function shpSync() {                     // the panel is there while it is wanted and shapes are selected
    var want = on && shpWant && !sorter && !typing && !drawing && shapesSel().length;
    if (!want) { if (shp) { shp.el.remove(); shp = null; } return; }
    if (!shp) shpMake();
    if (!shpBefore) shpFill();
  }
  function shpMake() {
    shp = { el: mk("div", "kn-ed-fmt kn-ed-shp", document.body) };
    var head = mk("div", "kn-ed-fmt-head", shp.el);
    shp.head = mk("span", "", head, "Shape");
    var x = mk("button", "kn-ed-x", head, "×"); x.title = "Close (bar ▸ Style… opens it again)";
    x.onclick = function () { shpWant = false; shpSync(); };
    function row(label) { var r = mk("label", "", shp.el); mk("span", "", r, label); return r; }
    function colour(r, key) {             // "None" (no fill, no line), the picker, and the deck's colours
      var none = mk("button", "kn-ed-none", r, "None");
      none.title = key === "fill" ? "No fill: only the line is drawn" : "No line: only the fill is drawn";
      none.onclick = function () { none.blur(); shpSet(function (e) { delete e[key]; }, true); };
      var c = mk("input", "", r); c.type = "color"; c.title = "Choose a colour";
      function set(v, done) { shpSet(function (e) { e[key] = v; if (key === "stroke" && e.strokeW == null) e.strokeW = 4; }, done); }
      c.addEventListener("input", function () { set(c.value, false); });
      c.addEventListener("change", function () { set(c.value, true); c.blur(); });
      return { none: none, c: c, sw: sw(key) };
    }
    function slider(r, key) {
      var s = mk("input", "", r); s.type = "range"; s.min = 0; s.max = 100; s.step = 1;
      var n = mk("span", "kn-ed-fmt-s", r);
      function set(done) { var v = +s.value; n.textContent = v + "%"; shpSet(function (e) { if (v >= 100) delete e[key]; else e[key] = v; }, done); }
      s.addEventListener("input", function () { set(false); });
      s.addEventListener("change", function () { set(true); s.blur(); });
      return { s: s, n: n };
    }
    function sw(key) {                     // colours this deck already uses for shapes and text
      var box = mk("div", "kn-ed-fmt-sw", shp.el), n = {};
      box.knKey = key;
      deck.slides.forEach(function (y) { (y.elements || []).forEach(function (e) { [e.fill, e.stroke, e.color].forEach(function (c) { c = hex(c); if (c) n[c] = (n[c] || 0) + 1; }); }); });
      var used = Object.keys(n).sort(function (a, b) { return n[b] - n[a]; });
      ["#000000", "#ffffff", "#e53935"].concat(used.filter(function (c) { return !/^#(000000|ffffff|e53935)$/.test(c); })).slice(0, 10).forEach(function (c) {
        var b = mk("button", "", box); b.style.background = c; b.title = c; b.knColour = c;
        b.onclick = function () { shpSet(function (e) { e[key] = c; if (key === "stroke" && e.strokeW == null) e.strokeW = 4; }, true); b.blur(); };
      });
      return box;
    }
    shp.fill = colour(row("Fill"), "fill");
    shp.fillOp = slider(row("Fill opacity"), "fillOp");
    shp.stroke = colour(row("Line"), "stroke");
    var r = row("Line width");
    shp.w = mk("input", "", r); shp.w.type = "number"; shp.w.min = 0.5; shp.w.max = 80; shp.w.step = 0.5;
    mk("span", "kn-ed-fmt-s", r, "px");
    shp.w.onchange = function () { var v = Math.max(0.5, Math.min(80, +shp.w.value || 4)); shpSet(function (e) { e.strokeW = v; }, true); };
    shp.strokeOp = slider(row("Line opacity"), "strokeOp");
    r = row("Line style");
    shp.dash = mk("select", "", r);
    [["", "Solid"], ["dash", "Dashed"], ["dot", "Dotted"]].forEach(function (o) { mk("option", "", shp.dash, o[1]).value = o[0]; });
    shp.dash.onchange = function () { var v = shp.dash.value; shp.dash.blur(); shpSet(function (e) { if (v) e.dash = v; else delete e.dash; }, true); };
    shp.cornerRow = r = row("Corners");
    shp.corner = mk("input", "", r); shp.corner.type = "number"; shp.corner.min = 0; shp.corner.max = 1000; shp.corner.step = 1;
    mk("span", "kn-ed-fmt-s", r, "px");
    shp.corner.onchange = function () { var v = Math.max(0, +shp.corner.value || 0); shpSet(function (e) { if ((e.shape || "rect") !== "rect") return; delete e.radius; if (v) e.corner = v; else delete e.corner; }, true); };
    shp.arrowRow = r = row("Arrowheads");
    shp.arrow = mk("select", "", r);
    [["", "None"], ["end", "At the end"], ["start", "At the start"], ["both", "Both ends"]].forEach(function (o) { mk("option", "", shp.arrow, o[1]).value = o[0]; });
    shp.arrow.onchange = function () { var v = shp.arrow.value; shp.arrow.blur(); shpSet(function (e) { if (e.shape !== "path") return; if (v) e.arrow = v; else delete e.arrow; }, true); };
    shp.closedRow = r = row("Closed");
    shp.closed = mk("input", "", r); shp.closed.type = "checkbox";
    shp.closed.onchange = function () { var v = shp.closed.checked; shpSet(function (e) { if (e.shape !== "path") return; if (v) e.closed = true; else delete e.closed; }, true); };
    shp.note = mk("div", "kn-ed-fmt-note", shp.el, "Drag the path's points; double-click a point to remove it, the line to add one.");
  }
  function shpFill() {
    if (!shp) return;
    var list = shapesSel(), e = list[0];
    if (!e) return;
    var kinds = list.map(function (x) { return x.shape || (x.radius >= 50 ? "oval" : "rect"); });
    shp.head.textContent = list.length > 1 ? list.length + " shapes" : { rect: "Box", oval: "Oval", path: "Path" }[kinds[0]];
    function put(ctl, key, dflt) {         // which is chosen: None, or a colour (shown on the picker and its swatch)
      var v = hex(e[key]);
      ctl.none.classList.toggle("on", !v);
      ctl.c.classList.toggle("off", !v);
      if (document.activeElement !== ctl.c) ctl.c.value = v || dflt;
      Array.prototype.forEach.call(ctl.sw.children, function (b) { b.classList.toggle("on", !!v && b.knColour === v); });
    }
    put(shp.fill, "fill", "#ffffff"); put(shp.stroke, "stroke", "#e53935");
    [[shp.fillOp, "fillOp"], [shp.strokeOp, "strokeOp"]].forEach(function (p) { var v = e[p[1]] == null ? 100 : e[p[1]]; p[0].s.value = v; p[0].n.textContent = v + "%"; });
    shp.fillOp.s.disabled = !e.fill;
    shp.strokeOp.s.disabled = shp.w.disabled = shp.dash.disabled = !e.stroke;
    if (document.activeElement !== shp.w) shp.w.value = e.strokeW == null ? 4 : e.strokeW;
    shp.dash.value = e.dash || "";
    shp.cornerRow.style.display = kinds.indexOf("rect") >= 0 ? "" : "none";
    var cr = list.filter(function (x, i) { return kinds[i] === "rect"; })[0];
    if (cr && document.activeElement !== shp.corner) shp.corner.value = cr.corner || (cr.radius ? Math.round(cr.radius / 100 * Math.min(cr.w, cr.h)) : 0);
    var pth = list.filter(function (x) { return x.shape === "path"; })[0];
    shp.arrowRow.style.display = shp.closedRow.style.display = shp.note.style.display = pth ? "" : "none";
    if (pth) { shp.arrow.value = pth.arrow || ""; shp.closed.checked = !!pth.closed; }
  }
  function addText() {
    if (!editable(cur().s)) return;
    var e;
    change(function (c) {
      var like = c.s.elements.filter(function (x) { return x.type === "text"; }).pop() || {};
      e = { type: "text", text: "Text", font: like.font || "Liberation Sans HN", weight: like.weight || 400, size: like.size || 48,
            color: like.color || "#ffffff", align: "left", valign: "top", x: W / 2 - 300, y: H / 2 - 40, w: 600, h: 80 };
      c.s.elements.push(e);
    });
    sel = [e]; rebuild(); retype(e);
    var r = document.createRange(); r.selectNodeContents(typing.body); var s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  // ---- images from outside: Insert ▸ Image…, a paste, or files dropped on the slide ----
  // Each file is stored in the deck's assets/ (PUT, tools/serve.py or the site) and then
  // added to fill the slide (scaled, up or down, until it covers it; it runs off the slide in
  // one direction), centred on the slide. Several land on top of one another.
  function pictures(list) { return Array.prototype.filter.call(list || [], function (f) { return /^image\//.test(f.type) || /\.(png|jpe?g|gif|webp|svg|avif)$/i.test(f.name || ""); }); }
  function measure(file) {                 // its size in pixels, which also shows that this browser can draw it
    return new Promise(function (ok, no) {
      var u = URL.createObjectURL(file), im = new Image();
      im.onload = function () { URL.revokeObjectURL(u); if (im.naturalWidth) ok({ w: im.naturalWidth, h: im.naturalHeight }); else no(new Error(file.name + " has no size of its own")); };
      im.onerror = function () { URL.revokeObjectURL(u); no(new Error("the browser cannot show " + (file.name || "that image") + " (use PNG or JPEG)")); };
      im.src = u;
    });
  }
  function assetName(file) {               // a name the server takes, and one that is not in assets/ already
    var ext = ({ "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg", "image/avif": "avif" })[file.type];
    var m = /^(.*?)(?:\.([A-Za-z0-9]+))?$/.exec(file.name || "image");
    var base = (m[1] || "image").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "image";
    return base + "-" + Math.random().toString(36).slice(2, 6) + "." + (ext || (m[2] || "png").toLowerCase());
  }
  function addImages(files) {
    files = pictures(files);
    if (!files.length) return false;
    if (sorter || !editable(cur().s)) { status("Open a slide with elements to add an image.", true); return true; }
    if (blocked) { conflict(); return true; }
    var slide = cur().s;
    status("Adding " + (files.length > 1 ? files.length + " images…" : "the image…"));
    Promise.all(files.map(function (f) {
      return measure(f).then(function (d) {
        var name = "assets/" + assetName(f);
        return fetch(name, { method: "PUT", body: f, headers: csrf({ "Content-Type": f.type || "application/octet-stream" }) }).then(function (r) {
          if (r.status === 413) throw new Error((f.name || "the image") + " is too large to store (50 MB at most)");
          if (!r.ok) throw new Error("the server answered " + r.status);
          return { src: name, w: d.w, h: d.h };
        });
      });
    })).then(function (got) {
      if (cur().s !== slide) { status("The slide changed before the image arrived. Add it again.", true); return; }
      var added = [];
      change(function (c) {
        got.forEach(function (g) {
          var k = Math.max(W / g.w, H / g.h), w = Math.round(g.w * k), h = Math.round(g.h * k);   // covers the slide
          var e = { type: "image", src: g.src, x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w: w, h: h };
          c.s.elements.push(e); added.push(e);
        });
      });
      sel = added; rebuild();
    }).catch(function (x) { status("Image not added: " + x.message, true); });
    return true;
  }
  var picker = null;
  function pickImage() {
    if (!picker) {
      picker = mk("input", "", document.body); picker.type = "file"; picker.accept = "image/*"; picker.multiple = true; picker.style.display = "none";
      picker.addEventListener("change", function () { addImages(picker.files); picker.value = ""; });
    }
    picker.click();
  }
  function go(d) { var i = cur().i + d; if (i >= 0 && i < deck.slides.length) { sel = []; Reveal.slide(i); } }

  function field(ev) {
    var t = ev.target || {};                // a slider or a button that kept the focus is not typing
    if (t.tagName === "INPUT") return !/^(range|checkbox|radio|button|submit|reset|color|file|image)$/.test(t.type || "");
    return /^(TEXTAREA|SELECT)$/.test(t.tagName || "");
  }   // typing in a form field (the site's menu)
  function key(ev) {
    if (field(ev)) return;
    if (!on) { if ((ev.key === "e" || ev.key === "E") && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) toggle(); return; }
    if (typing) { ev.stopPropagation(); if (ev.key === "Escape") { ev.preventDefault(); finish(); } return; }
    if ((menuOpen || asker) && ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); menuClose(); bpAsk(null); return; }
    menuClose();
    if (using && ev.key === "Escape") { ev.preventDefault(); use(null); return; }
    var cmd = ev.metaKey || ev.ctrlKey, k = ev.key, done = true, step = ev.shiftKey ? 10 : 1;
    if (drawing && !cmd) {                 // drawing a path: Enter ends it, Esc gives it up, Delete takes back a point
      if (k === "Enter") drawEnd(false);
      else if (k === "Escape") { drawing = null; layer.classList.remove("kn-ed-drawing"); status(""); draw(); }
      else if (k === "Backspace" || k === "Delete") { drawing.pts.pop(); draw(); }
      ev.preventDefault(); ev.stopPropagation();
      return;
    }
    if (cropping && !cmd) {                // crop mode: arrows slide the picture, Enter or Esc finishes
      var a = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[k];
      if (a) { change(function () { slide(a[0], a[1]); }); draw(); }
      else if (k === "Enter" || k === "Escape") crop(null);
      ev.preventDefault(); ev.stopPropagation();
      return;
    }
    if (cmd && (k === "z" || k === "Z")) { if (ev.shiftKey) restore(redo, undo); else restore(undo, redo); }
    else if (cmd && (k === "c" || k === "x" || k === "v")) done = false;      // the copy / cut / paste events below
    else if (cmd && k === "d") paste(copy());
    else if (sorter) done = sKey(ev, cmd, k);
    else if (cmd && k === "a") { sel = cur().s.elements.slice(); draw(); }
    else if (cmd && ev.altKey && ev.code === "KeyG") { if (ev.shiftKey) ungroup(); else group(); }
    else if (ev.altKey && !cmd && sel.length && /^Arrow/.test(k)) {        // Alt-arrow: a copy, nudged (Shift: by 10)
      if (!ev.repeat) nudgeCopy(k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0);
    }
    else if (cmd || ev.altKey) done = false;
    else if (k === "e" || k === "E") toggle();
    else if (k === "s" || k === "S") openSorter(true);
    else if (k === "Escape") { sel = []; draw(); }
    else if (k === "Backspace" || k === "Delete") { if (bp && bpKeys) bpRemove(); else remove(); }      // after the last row, Delete does not go on to the slide's things
    else if (k === "Enter" && sel.length === 1 && sel[0].type === "text") retype(sel[0]);
    else if (k === "Enter" && sel.length === 1 && sel[0].type === "image") crop(sel[0]);
    else if (k === "]") order("forward"); else if (k === "[") order("backward");
    else if (k === "}") order("front"); else if (k === "{") order("back");
    else if (k === "PageDown") go(1); else if (k === "PageUp") go(-1);
    else if (k === "ArrowLeft") { if (sel.length) nudge(-step, 0); else go(-1); }
    else if (k === "ArrowRight") { if (sel.length) nudge(step, 0); else go(1); }
    else if (k === "ArrowUp") { if (sel.length) nudge(0, -step); }
    else if (k === "ArrowDown") { if (sel.length) nudge(0, step); }
    else done = false;
    if (done) { ev.preventDefault(); ev.stopPropagation(); }
  }

  // ---------------------------------------------------------------- on and off
  var menuOpen = null;                     // the bar's menus: a button that drops a list of commands
  function menuClose() { if (menuOpen) { menuOpen.el.remove(); menuOpen.btn.classList.remove("open"); menuOpen = null; } }
  function menu(label, title, items) {     // items(): [[label, key, fn, enabled?] | null for a line]
    var b = mk("button", "kn-ed-mbtn", bar.el, label + " ▾");
    b.title = title;
    function show() {
      menuClose();
      var el = mk("div", "kn-ed-menu", bar.el);
      el.style.left = b.offsetLeft + "px";
      items().forEach(function (it) {
        if (!it) { mk("div", "sep", el); return; }
        var x = mk("button", "", el);
        mk("span", "", x, it[0]); mk("span", "k", x, it[1] || "");
        x.disabled = it[3] === false;
        x.onclick = function () { menuClose(); it[2](); };
      });
      b.classList.add("open");
      menuOpen = { el: el, btn: b };
    }
    b.onclick = function () { b.blur(); if (menuOpen && menuOpen.btn === b) menuClose(); else show(); };
    b.onmouseenter = function () { if (menuOpen && menuOpen.btn !== b) show(); };
  }
  function button(label, title, fn) { var b = mk("button", "", bar.el, label); b.title = title; b.onclick = function () { fn(); b.blur(); }; return b; }
  function setup() {
    layer = mk("div", "kn-ed-layer");
    layer.addEventListener("pointerdown", down); layer.addEventListener("pointermove", move);
    layer.addEventListener("pointerup", up); layer.addEventListener("pointercancel", up);
    layer.addEventListener("dblclick", dbl);
    bar = { el: mk("div", "kn-ed-bar", document.body) };
    bar.where = mk("span", "kn-ed-where", bar.el);
    var some = function () { return !sorter && sel.length > 0; };
    menu("Slide", "Slides: the sorter, new, delete, background and transition", function () { return [
      [sorter ? "Close the slide sorter" : "Slide sorter", "S", function () { openSorter(!sorter); }],
      ["New slide", "", addSlide],
      [sorter ? "Delete the selected slides" : "Delete this slide", sorter ? "Delete" : "", removeSlides],
      null,
      ["Background and transition…", "", function () { fmtOpen(true); }]
    ]; });
    menu("Insert", "Add to the slide", function () { return [
      ["Text box", "", addText, !sorter && editable(cur().s)],
      ["Image…", "", pickImage, !sorter && editable(cur().s)],
      null,
      ["Shape: box", "", function () { addShape("rect"); }, !sorter && editable(cur().s)],
      ["Shape: oval", "", function () { addShape("oval"); }, !sorter && editable(cur().s)],
      ["Shape: path (click points)", "", addPath, !sorter && editable(cur().s)]
    ]; });
    menu("Arrange", "Stacking order and groups", function () {
      var us = units(sel), grouped = sel.some(function (e) { return e.group; });
      return [
        ["Bring to front", "}", function () { order("front"); }, some()],
        ["Bring forward", "]", function () { order("forward"); }, some()],
        ["Send backward", "[", function () { order("backward"); }, some()],
        ["Send to back", "{", function () { order("back"); }, some()],
        null,
        ["Group", "⌥⌘G", group, !sorter && sel.length > 1 && !(us.length === 1 && us[0].es.length === sel.length)],
        ["Ungroup", "⌥⇧⌘G", ungroup, !sorter && grouped]
      ];
    });
    menu("Animate", "Builds: what appears or goes at each click, as in Keynote", function () { return [
      ["Build in", "", function () { bpAdd("in"); }, some()],
      ["Build out", "", function () { bpAdd("out"); }, some()],
      ["Build in, by paragraph", "", function () { bpAdd("paras"); }, !sorter && sel.length === 1 && sel[0].type === "text" && parasOf(sel[0]) > 1],
      null,
      [bp ? "Close the build order" : "Build order…", "", function () { bpOpen(!bp); }]
    ]; });
    button("Undo", "Cmd-Z", function () { restore(undo, redo); });
    button("Redo", "Shift-Cmd-Z", function () { restore(redo, undo); });
    bar.geo = mk("span", "kn-ed-geo", bar.el);
    bar.tools = mk("span", "kn-ed-tools", bar.el);
    mk("span", "", bar.tools, "Opacity");
    bar.op = mk("input", "", bar.tools); bar.op.type = "range"; bar.op.min = 0; bar.op.max = 100; bar.op.step = 1;
    bar.op.title = "How opaque the selection is. Turn an image down to line it up with what is under it.";
    bar.opn = mk("span", "kn-ed-opn", bar.tools);
    bar.op.addEventListener("input", function () { opacity(+bar.op.value, false); });
    bar.op.addEventListener("change", function () { opacity(+bar.op.value, true); bar.op.blur(); });
    bar.crop = mk("button", "", bar.tools, "Crop");
    bar.crop.title = "Mask the image: drag the handles over it, drag inside to slide it. Double-click an image does the same.";
    bar.crop.onclick = function () { crop(cropping ? null : sel[0]); bar.crop.blur(); };
    bar.style = mk("button", "", bar.tools, "Style…");
    bar.style.title = "Fill, line, transparency, corners and arrowheads of the selected shapes";
    bar.style.onclick = function () { shpWant = !shp; shpSync(); bar.style.blur(); };
    bar.text = mk("button", "", bar.tools, "Text…");
    bar.text.title = "Font, size, bold and italic, colour, alignment, background and glow of the selected text";
    bar.text.addEventListener("mousedown", function (ev) { ev.preventDefault(); });   // the text being typed keeps its selection
    bar.text.onclick = function () { txtWant = !txt; txtSync(); bar.text.blur(); };
    bar.live = mk("span", "kn-ed-livebar", bar.el);
    bar.status = mk("span", "kn-ed-status", bar.el);
    button("Done", "Back to presenting  E", toggle).className = "kn-ed-done";
    document.addEventListener("keydown", key, true);
    document.addEventListener("pointerdown", function (ev) {
      if (typing && txt && txt.el.contains(ev.target)) {       // the Text panel: the words selected are remembered before the click takes the selection away
        var gs = getSelection(), g0 = gs.rangeCount ? gs.getRangeAt(0) : null;
        if (g0 && typing.body.contains(g0.commonAncestorContainer)) typing.range = g0.collapsed ? null : g0.cloneRange();
      } else if (typing && bar.text.contains(ev.target)) {
        /* opening the panel while typing goes on */
      } else if (typing && !typing.node.contains(ev.target)) finish();
      if (using && !bar.el.contains(ev.target)) use(null);
      if (menuOpen && !menuOpen.el.contains(ev.target) && !menuOpen.btn.contains(ev.target)) menuClose();
    }, true);
    // Copies also go on the system clipboard, and a paste reads it: so a copy made in another
    // browser, or an element offered by another page, pastes here.
    function onCopy(ev, cut) {
      if (!on || typing || field(ev)) return;
      var c = copy();
      if (!c) return;
      ev.preventDefault();
      try { ev.clipboardData.setData("text/plain", JSON.stringify(c)); } catch (x) {}
      if (cut) { if (sorter) removeSlides(); else remove(); }
    }
    document.addEventListener("copy", function (ev) { onCopy(ev, false); });
    document.addEventListener("cut", function (ev) { onCopy(ev, true); });
    document.addEventListener("paste", function (ev) {
      if (!on || typing || field(ev)) return;
      ev.preventDefault();
      if (addImages(ev.clipboardData.files)) return;          // a picture copied in another program, or image files from the Finder
      paste(readClip(ev.clipboardData.getData("text/plain")));
    });
    function hasFiles(ev) { return on && ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], "Files") >= 0; }
    document.addEventListener("dragover", function (ev) { if (hasFiles(ev)) { ev.preventDefault(); ev.dataTransfer.dropEffect = "copy"; } });
    document.addEventListener("drop", function (ev) {        // image files dropped on the slide fill it, like any added picture
      if (!hasFiles(ev)) return;
      ev.preventDefault();
      if (!addImages(ev.dataTransfer.files)) status("Only images can be dropped on a slide.", true);
    });
    window.addEventListener("message", function (ev) {       // the answer to Capture step
      var d = ev.data;
      if (!asking || !d || d.type !== "deck:state" || ev.source !== asking.win) return;
      capture(d.state);
    });
    Reveal.on("slidechanged", function () { if (on) { fmtFill(); if (bp) { bpRow = -1; RT.editClick = null; RT.apply(cur().sec, {}); bpFill(); } } });
    Reveal.on("slidechanged", function () { bpAsk(null); if (on && !sorter) { finish(); cropping = null; liveFor = null; sel = []; prepare(); draw(); } });
    window.addEventListener("resize", function () { if (on) setTimeout(draw, 50); });
    window.addEventListener("beforeunload", function (ev) {
      if (timer) save();
      if (pending && (blocked || failed)) { ev.preventDefault(); ev.returnValue = ""; }     // the browser asks before leaving
    });
  }
  function toggle() {
    if (!layer) setup();
    finish();
    if (cropping) crop(null);
    if (using) use(null);                  // Done while operating a figure: its view is kept, as with Finish
    if (sorter) openSorter(false);
    fmtOpen(false); lvOpen(false); bpOpen(false); bpAsk(null); menuClose();
    on = !on; RT.editing = on;
    RT.paint();
    document.body.classList.toggle("kn-editing", on);
    Reveal.layout();                       // the stage sits below the bar while editing
    Reveal.configure({ keyboard: on ? false : { 75: null } });
    sel = [];
    liveFor = null;
    if (drawing) { drawing = null; layer.classList.remove("kn-ed-drawing"); }
    shpSync(); txtSync();
    if (on) { prepare(); draw(); status(blocked || failed ? "Not saved" : "", blocked || !!failed); }
    else {
      var fr = cur().sec.querySelector("iframe.kn-live");       // back to the step the talk is at
      if (fr && RT.liveLoaded) RT.liveLoaded(fr);
      layer.remove(); if (timer) save(); }
  }
  toggle();
})();
