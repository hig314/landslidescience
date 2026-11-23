/*
 * lib/editor.js — edit a deck in place. Loaded by lib/deck.js the first time E is pressed;
 * E again (or Done) returns to presenting. Changes are saved to deck.json through
 * tools/serve.py a moment after each one; a slide that is changed is marked "owner": "here",
 * so a Keynote re-import leaves it alone.
 *
 *   click             select (again in the same spot: the element underneath); Shift-click adds
 *   drag              move; snaps to the slide's edges and centre and to other elements
 *                     (Cmd held: no snapping; Shift: along one axis)
 *   handles           resize, as in Keynote: Shift keeps the proportions (from any handle),
 *                     Alt resizes about the centre
 *   arrows            nudge 1 px (Shift: 10); with nothing selected, previous / next slide
 *   double-click, Enter   retype a text box; Esc or a click elsewhere finishes
 *   Cmd-Z, Shift-Cmd-Z    undo, redo (per editing session)
 *   Cmd-C / X / V / D     copy, cut, paste, duplicate. A paste lands exactly where the
 *                     original was, on any slide; only if that spot already holds the same
 *                     thing is it offset, so that it can be seen.
 *   Delete            remove       ] [ } {   forward, backward, to front, to back
 *   Opacity           the slider in the bar sets how opaque the selection is. Turn an image down to
 *                     see what is under it while lining the two up, then back to 100.
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
  var cropping = null, opBefore = null, liveFor = null;
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
  function save() {
    timer = 0;
    if (blocked) return;
    var head = csrf(mtime ? { "X-Base-Mtime": mtime } : {});
    return fetch("deck.json", { method: "PUT", body: dump(), headers: head }).then(function (r) {
      if (r.status === 204) { mtime = r.headers.get("X-Mtime") || mtime; if (!timer) status("Saved"); }
      else if (r.status === 409) { blocked = true; status("deck.json was changed on disk. Reload the page to go on editing; changes since the last save are not kept.", true); }
      else status("Not saved (" + r.status + "). Editing needs tools/serve.py.", true);
    }).catch(function () { status("Not saved: the server did not answer.", true); });
  }
  function touch() { status("Saving…"); clearTimeout(timer); timer = setTimeout(save, 500); }

  // ---------------------------------------------------------------- history
  // A change is recorded as the slide's data before it; undo puts that back.
  function snapshot() { var c = cur(); return { i: c.i, json: JSON.stringify(c.s) }; }
  function commit(before) {
    var c = cur();
    if (JSON.stringify(c.s) === before.json) return false;
    if (c.s.owner !== "here") {            // owner goes after kn, where the importer looks for it
      var o = {}; Object.keys(c.s).forEach(function (k) { o[k] = c.s[k]; if (k === "kn") o.owner = "here"; });
      if (!o.owner) o.owner = "here";
      Object.keys(c.s).forEach(function (k) { delete c.s[k]; });
      Object.keys(o).forEach(function (k) { c.s[k] = o[k]; });
    }
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
  function resynced(at) { sel = []; RT.resync(at); prepare(); draw(); if (sorter) drawSorter(); }
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
  function draw(guides) {
    layer.innerHTML = "";
    layer.style.setProperty("--k", 1 / (cur().sec.getBoundingClientRect().width / W));
    if (cropping && (sel.length !== 1 || sel[0] !== cropping || cur().s.elements.indexOf(cropping) < 0)) cropping = null;
    if (cropping) {                        // the whole picture, faint, behind its mask
      var ce = cropping, f = ce.fit || [0, 0, ce.w, ce.h], cn = nodeOf(ce), g = mk("img", "kn-ed-ghost", layer);
      g.src = cn && cn.firstChild ? (cn.firstChild.currentSrc || cn.firstChild.src || cn.firstChild.getAttribute("data-src")) : ce.src;
      g.style.left = ce.x + f[0] + "px"; g.style.top = ce.y + f[1] + "px"; g.style.width = f[2] + "px"; g.style.height = f[3] + "px";
      if (ce.flip) g.style.transform = ce.flip === "h" ? "scaleX(-1)" : "scaleY(-1)";
    }
    sel.forEach(function (e) {
      var b = mk("div", "kn-ed-box" + (e === cropping ? " kn-ed-crop" : ""), layer);
      b.style.left = e.x + "px"; b.style.top = e.y + "px"; b.style.width = e.w + "px"; b.style.height = e.h + "px";
      if (e.rot) b.style.transform = "rotate(" + (-e.rot) + "deg)";
      if (sel.length === 1 && !e.rot && !typing) ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(function (d) { mk("div", "kn-ed-h kn-ed-" + d, b).setAttribute("data-h", d); });
    });
    (guides || []).forEach(function (g) {
      var n = mk("div", "kn-ed-guide", layer);
      if (g.x != null) { n.style.left = g.x + "px"; n.style.top = 0; n.style.height = H + "px"; n.style.width = "0"; }
      else { n.style.top = g.y + "px"; n.style.left = 0; n.style.width = W + "px"; n.style.height = "0"; }
    });
    var t = "";
    if (sel.length) { var b = boxOf(sel); t = "x " + round(b.x) + "  y " + round(b.y) + "  w " + round(b.w) + "  h " + round(b.h) + (sel.length > 1 ? "  (" + sel.length + ")" : ""); }
    if (cropping) { var cf = cropping.fit || [0, 0, cropping.w, cropping.h]; t += "   picture " + round(cf[2]) + " × " + round(cf[3]); }
    bar.geo.textContent = t;
    drawTools();
    drawLive();
  }
  // ---------------------------------------------------------------- opacity and crop (in the bar)
  function drawTools() {
    var any = sel.length && !sorter && !typing;
    bar.tools.style.display = any ? "" : "none";
    if (!any) return;
    if (!opBefore) { var v = sel[0].op == null ? 100 : sel[0].op; bar.op.value = v; bar.opn.textContent = Math.round(v) + "%"; }
    var img = sel.length === 1 && sel[0].type === "image" && !sel[0].rot;
    bar.crop.style.display = img ? "" : "none";
    bar.crop.textContent = cropping ? "Finish crop" : "Crop";
  }
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
    return cur().s.elements.filter(function (e) { return p.x >= e.x && p.x <= e.x + e.w && p.y >= e.y && p.y <= e.y + e.h; }).reverse();
  }
  function down(ev) {
    if (ev.button || typing || using) return;
    var c = cur();
    if (!editable(c.s)) return;
    ev.preventDefault();
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
      var top = under.filter(function (e) { return sel.indexOf(e) >= 0; })[0] || under[0];
      if (!top) { sel = []; draw(); return; }
      if (ev.shiftKey) { if (sel.indexOf(top) >= 0) sel = sel.filter(function (e) { return e !== top; }); else sel.push(top); }
      else if (sel.indexOf(top) < 0) sel = [top];
      if (!sel.length) { draw(); return; }
    }
    drag = { p: p, handle: h, before: snapshot(), moved: false, under: under, was: was, shift: ev.shiftKey,
             orig: sel.map(function (e) { return { x: e.x, y: e.y, w: e.w, h: e.h, fit: e.fit && e.fit.slice() }; }),
             snap: lines(c, sel) };
    try { layer.setPointerCapture(ev.pointerId); } catch (x) {}
    draw();
  }
  function move(ev) {
    if (!drag) return;
    var p = point(ev), dx = p.x - drag.p.x, dy = p.y - drag.p.y, tol = ev.metaKey ? -1 : 7 / p.k, guides = [];
    if (!drag.moved && Math.abs(dx) * p.k < 3 && Math.abs(dy) * p.k < 3) return;
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
    } else {
      var e = sel[0], o = drag.orig[0], hd = drag.handle;
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
  function up() {
    if (!drag) return;
    var d = drag; drag = null;
    if (d.moved) commit(d.before);
    else if (d.crop) { /* a click in crop mode changes nothing */ }
    else if (!d.handle && !d.shift && d.was.length === 1 && sel.length === 1 && d.was[0] === sel[0] && d.under.length > 1) {
      sel = [d.under[(d.under.indexOf(sel[0]) + 1) % d.under.length]];     // a second click: the one underneath
    }
    draw();
  }
  function dbl(ev) {
    if (cropping) return;
    var top = hits(point(ev)).filter(function (x) { return sel.indexOf(x) >= 0; })[0] || hits(point(ev))[0];
    if (top && top.type === "image" && !top.rot) { crop(top); return; }
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
        var shown = cur().sec.querySelectorAll(".kn-step.visible:not(.kn-pz)").length;   // where the talk had got to
        showStep(e, Math.min(e.states.length - 1, shown), false);
        return;
      }
    }
    if (!e) { if (using) use(null); return; }
    liveAt = Math.min(liveAt, e.states.length - 1);
    function btn(label, title, fn) { var x = mk("button", "", b, label); x.title = title; x.onclick = function () { fn(); x.blur(); }; }
    btn("◀", "Show the previous step", function () { showStep(e, liveAt - 1); });
    mk("span", "", b, "step " + (liveAt + 1) + " of " + e.states.length);
    btn("▶", "Show the next step", function () { showStep(e, liveAt + 1); });
    if (!using) btn("Use", "Operate the figure in place, to set up a view", function () { use(e); });
    else {
      btn("Capture step", "Keep the figure's current view as a new step after this one", function () { ask(e, "add"); });
      btn("Finish", "Stop operating the figure; the view it is left in becomes this step", function () { use(null); });
    }
    if (e.states.length > 1) btn("Remove step", "Remove this step", function () {
      change(function () { e.states.splice(liveAt, 1); opensAt(e); });
      steps(e);
      showStep(e, liveAt); if (!using) rebuild();
    });
  }
  function steps(e) {                      // one click per step: the slide's step markers follow the figure's steps,
    var i = deck.slides.findIndex(function (x) { return x.elements && x.elements.indexOf(e) >= 0; });   // without reloading the figure
    var sec = RT.slidesEl.querySelector('section[data-i="' + i + '"]');
    if (!sec) return;
    var have = sec.querySelectorAll(".kn-step:not(.kn-pz)"), want = e.states.length - 1, k;
    for (k = have.length; k < want; k++) mk("span", "fragment kn-step", sec);
    for (k = have.length - 1; k >= want; k--) have[k].remove();
    Reveal.syncFragments(sec);
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
    if (a.how === "add") { e.states.splice(a.at + 1, 0, state); liveAt = a.at + 1; }
    else if (state !== old) e.states[a.at] = state;
    opensAt(e); steps(e);
    if (JSON.stringify(s) !== before.json) {
      s.owner = "here"; undo.push(before); redo = []; touch();
      status(a.how === "add" ? "Step " + (liveAt + 1) + " captured" : "Step " + (a.at + 1) + " now shows this view");
    }
    if (a.how === "keep") { if (on && cur().i === a.i) rebuild(); else if (on) RT.render(a.i); }   // (after Done: the slide is left as it is)
    else if (on) drawLive();
  }
  RT.stepFor = function (e) { return (liveSel() === e ? liveAt : 0) + 1; };    // which step a figure shows while editing

  // ---------------------------------------------------------------- text
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
    var text = parts.map(function (p) { return p.t; }).join("").replace(/\n+$/, "");
    if (e.runs) {
      var left = text.length;
      e.runs = parts.map(function (p) { var r = {}, src = e.runs[p.r] || {}; Object.keys(src).forEach(function (k) { r[k] = src[k]; }); r.t = p.t.slice(0, left); left -= r.t.length; return r; })
                    .filter(function (r) { return r.t; });
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
    var clip = given || getClip();
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
          var off = 0;
          while (items.some(function (e) { return taken({ type: e.type, x: e.x + off, y: e.y + off, w: e.w, h: e.h }); })) off += 24;
          sel = items.map(function (e) { e.x += off; e.y += off; c.s.elements.push(e); return e; });
        });
        rebuild();
      }
    }).catch(function (x) { status("Paste failed: " + x.message, true); });
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
        Array.prototype.forEach.call(st.querySelectorAll("img[data-src]"), function (n) { n.loading = "lazy"; n.draggable = false; n.src = n.getAttribute("data-src"); });
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
    } else if (!yes && sorter) { sorter.remove(); sorter = null; prepare(); draw(); }
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
    if (using && ev.key === "Escape") { ev.preventDefault(); use(null); return; }
    var cmd = ev.metaKey || ev.ctrlKey, k = ev.key, done = true, step = ev.shiftKey ? 10 : 1;
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
    else if (cmd || ev.altKey) done = false;
    else if (k === "e" || k === "E") toggle();
    else if (k === "s" || k === "S") openSorter(true);
    else if (k === "Escape") { sel = []; draw(); }
    else if (k === "Backspace" || k === "Delete") remove();
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
  function button(label, title, fn) { var b = mk("button", "", bar.el, label); b.title = title; b.onclick = function () { fn(); b.blur(); }; return b; }
  function setup() {
    layer = mk("div", "kn-ed-layer");
    layer.addEventListener("pointerdown", down); layer.addEventListener("pointermove", move);
    layer.addEventListener("pointerup", up); layer.addEventListener("pointercancel", up);
    layer.addEventListener("dblclick", dbl);
    bar = { el: mk("div", "kn-ed-bar", document.body) };
    bar.where = mk("span", "kn-ed-where", bar.el);
    button("Slides", "The slide sorter: select, reorder, copy and paste slides  S", function () { openSorter(!sorter); });
    button("+ Slide", "Add an empty slide after this one", addSlide);
    button("− Slide", "Delete this slide (in the sorter: the selected slides; Delete does the same there). Undo brings it back", removeSlides);
    button("+ Text", "Add a text box", function () { if (!sorter) addText(); });
    button("Front", "Bring to front  }", function () { order("front"); });
    button("Forward", "Bring forward  ]", function () { order("forward"); });
    button("Backward", "Send backward  [", function () { order("backward"); });
    button("Back", "Send to back  {", function () { order("back"); });
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
    bar.live = mk("span", "kn-ed-livebar", bar.el);
    bar.status = mk("span", "kn-ed-status", bar.el);
    button("Done", "Back to presenting  E", toggle).className = "kn-ed-done";
    document.addEventListener("keydown", key, true);
    document.addEventListener("pointerdown", function (ev) {
      if (typing && !typing.node.contains(ev.target)) finish();
      if (using && !bar.el.contains(ev.target)) use(null);
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
      paste(readClip(ev.clipboardData.getData("text/plain")));
    });
    window.addEventListener("message", function (ev) {       // the answer to Capture step
      var d = ev.data;
      if (!asking || !d || d.type !== "deck:state" || ev.source !== asking.win) return;
      capture(d.state);
    });
    Reveal.on("slidechanged", function () { if (on && !sorter) { finish(); cropping = null; liveFor = null; sel = []; prepare(); draw(); } });
    window.addEventListener("resize", function () { if (on) setTimeout(draw, 50); });
    window.addEventListener("beforeunload", function () { if (timer) { clearTimeout(timer); save(); } });
  }
  function toggle() {
    if (!layer) setup();
    finish();
    if (cropping) crop(null);
    if (using) use(null);                  // Done while operating a figure: its view is kept, as with Finish
    if (sorter) openSorter(false);
    on = !on; RT.editing = on;
    document.body.classList.toggle("kn-editing", on);
    Reveal.layout();                       // the stage sits below the bar while editing
    Reveal.configure({ keyboard: on ? false : { 75: null } });
    sel = [];
    liveFor = null;
    if (on) { prepare(); draw(); status(blocked ? "deck.json was changed on disk. Reload the page." : "", blocked); }
    else {
      var fr = cur().sec.querySelector("iframe.kn-live");       // back to the step the talk is at
      if (fr && RT.liveLoaded) RT.liveLoaded(fr);
      layer.remove(); if (timer) { clearTimeout(timer); save(); } }
  }
  toggle();
})();
