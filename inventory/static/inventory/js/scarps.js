/* scarps.js — landform traces (scarps, moraine crests, trimlines, ...).
 *
 * GENERALISED 2026-10-11 (Hig): one table and one tool for every hand-traced
 * line feature. A moraine crest and a scarp are the same thing as a tool --
 * a line read off a surface, a note, who drew it -- so each trace carries a
 * `kind`, and the vocabulary (label, colour, dash) comes from the server
 * with the data (inventory/scarps.py TRACE_KINDS). Each new trace also
 * records `traced_on`, the surfaces it was read from, which the host supplies
 * through startDraw's `context`. Hosts: /lidar/ (the main tracing page: sun
 * angle, slope, 3D) and the inventory map's pencil (scarps). The name of the
 * module, the global and the API paths stay "scarps".
 *
 * Since 2026-11-16 also the line editor of /glaciers/ (flow lines and ice
 * faces, api = '/glaciers/lines/'), which runs the session in EXPLICIT mode
 * (see explicitDraw). On every host: click a vertex to make it the active
 * one, and Delete / Backspace removes it.
 *
 * The scarp-only history follows.
 *
 * WHY THIS EXISTS
 * ---------------
 * Mapping landslides on the lidar surfaces turns up scarps that are not
 * landslide headwalls: straight breaks that cut across drainage, uphill-facing
 * steps, lineaments that look tectonic — and plenty of ambiguous or
 * multi-cause origin, which is why the word is "scarp", not "fault". Until now
 * there was nowhere to put the observation at the moment it was made — which
 * in practice means it was lost.
 *
 * So this is deliberately small: draw a line, write a note, it records who
 * drew it. Everything else can be added later (see the migration command for
 * why the schema is thin on purpose).
 *
 * WHAT IT IS NOT
 * --------------
 * Not a fault map. Reads are public since 2026-09-20 (Hig's call: the traces
 * are shown to everyone by default, labelled as working observations); writes
 * are editor-only on the server. Tracing is started by map.js's pencil
 * (DrawModeControl, kind 'scarp'), so it and the polygon tool are one mode
 * and cannot both be open.
 *
 * RELATIONSHIP TO revise.js
 * -------------------------
 * Same Terra Draw plumbing and the same two hard-won lessons: BOTH the drawing
 * mode and select mode must be registered or added features are rejected
 * against the instantiated-modes list, and coordinates must be rounded to the
 * adapter's precision or every feature comes back "invalid coordinates".
 */
window.LSScarps = (function () {
  'use strict';

  var map = null, td = null, api = '/inventory/', csrf = null;
  var mode = null;               // null | 'draw' | 'select'
  var onChange = null, flash = null, onPick = null;
  var features = { type: 'FeatureCollection', features: [] };
  var selectedId = null;

  var SRC = 'scarps-src', HIT = 'scarps-hit', SEL = 'scarps-sel', CASE = 'scarps-case';
  // One line layer per dash style (line-dasharray cannot be data-driven on
  // MapLibre 5.5, the inventory map's version); colour is by kind.
  var STYLES = { solid: 'scarps-line', dash: 'scarps-line-dash', dot: 'scarps-line-dot' };
  var DASH = { solid: null, dash: [3, 1.5], dot: [0.6, 1.6] };
  var LINES = [STYLES.solid, STYLES.dash, STYLES.dot];
  // Edit handles for the selected trace while a session is open: vertices
  // and segment midpoints (drag one to add a vertex there).
  var EDIT_SRC = 'scarps-edit', MID = 'scarps-mid', VTX = 'scarps-vtx';
  var ALL = [CASE, HIT].concat(LINES, [SEL, MID, VTX]);
  // Until the first load answers: enough to draw the rows that existed
  // before kinds did.
  var kinds = [{ id: 'scarp', label: 'Scarp', color: '#7b3fa0', dash: 'dash' }];
  var hiddenKinds = {};           // kind id -> true while filtered off
  var drawKind = 'scarp';         // kind given to the next finished line
  var drawContext = null;         // () -> traced_on, from the host
  var drawGroup = null;           // group id given to the next finished line
  var groups = [];                // [{id, name, notes, created_by, n}]
  var groupFilter = null;         // show only this group id, or null
  // Terra Draw's MapLibre adapter counts decimals and rejects anything finer
  // than its precision (9). The API serves ST_AsGeoJSON(geom, 6), so this is
  // headroom rather than a constraint — but rounding here keeps an untouched
  // line comparing equal after a round trip.
  var COORD_PRECISION = 9;
  // EXPLICIT DRAWING (2026-11-16, for /glaciers/). By default a session draws
  // on any open-ground click, which suits tracing many lines in a row (the
  // /lidar/ and inventory hosts). Where a session is mostly EDITING, that
  // turns every missed click into the start of a new line: a click a few
  // pixels off a handle, then another, then one near the last, and a stray
  // line has been saved. A host that passes startDraw({explicit: true}) gets
  // a session that only selects and edits until it calls armDraw(true), which
  // lets ONE line be drawn (and lets it start on top of an existing line,
  // which is how a feeder or an extension begins).
  var explicitDraw = false, armed = true;
  var extendId = null;            // the drawn line is appended to this trace instead of saved as new
  var extendAt = 'end';           // which end of it: 'end' (append) or 'start' (prepend)
  // The vertex last clicked on the selected trace: Delete / Backspace removes
  // it, and the next one takes its place, so a run of vertices goes with
  // repeated presses. Right-click still removes one directly.
  var activeVtx = null;           // {id, p, i}
  // SNAPPING (startDraw({snapKinds: [...]}), for /glaciers/ flow lines). The
  // END of a line of one of these kinds, dragged or drawn to within SNAP_PX
  // of another such line, takes that line's coordinates exactly: a vertex if
  // one is near, else the nearest point on a segment. Two lines that share a
  // coordinate are joined; that is the host's whole notion of topology, so
  // it has to be exact and it has to be visible while the hand is on it.
  var snapKinds = null, SNAP_PX = 12;
  function snapsKind(k) { return !!snapKinds && snapKinds.indexOf(k) >= 0; }
  function snapLL(ll, selfId) {
    var out = [ll.lng, ll.lat];
    if (!snapKinds) return out;
    var p = map.project(ll), bestV = null, bestS = null;
    features.features.forEach(function (f) {
      if (f.properties.id === selfId || !snapsKind(f.properties.kind)) return;
      partsOf(f.geometry).forEach(function (part) {
        var prev = null;
        part.forEach(function (c) {
          var q = map.project(c);
          var d = Math.hypot(q.x - p.x, q.y - p.y);
          if (d <= SNAP_PX && (!bestV || d < bestV.d)) bestV = { d: d, c: c };
          if (prev) {
            var dx = q.x - prev.x, dy = q.y - prev.y, L2 = dx * dx + dy * dy;
            var t = L2 ? Math.max(0, Math.min(1, ((p.x - prev.x) * dx + (p.y - prev.y) * dy) / L2)) : 0;
            var sx = prev.x + t * dx, sy = prev.y + t * dy;
            var ds = Math.hypot(sx - p.x, sy - p.y);
            if (ds <= SNAP_PX && (!bestS || ds < bestS.d)) bestS = { d: ds, x: sx, y: sy };
          }
          prev = q;
        });
      });
    });
    if (bestV) return bestV.c.slice(0, 2);
    if (bestS) { var u = map.unproject([bestS.x, bestS.y]); return [u.lng, u.lat]; }
    return out;
  }

  function round(g) {
    return JSON.parse(JSON.stringify(g, function (k, v) {
      return typeof v === 'number' ? +v.toFixed(COORD_PRECISION) : v;
    }));
  }

  function req(url, opts) {
    opts = opts || {};
    var h = { 'Content-Type': 'application/json' };
    if (opts.method && opts.method !== 'GET') h['X-CSRFToken'] = csrf();
    return fetch(url, { method: opts.method || 'GET', headers: h,
                        body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        // Anything that is not JSON here is a page, not an answer — a login
        // redirect most likely. Say what came back rather than letting
        // JSON.parse report a syntax error about '<'.
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') === -1) {
          throw new Error('server returned ' + r.status + ' ' +
                          (ct.split(';')[0] || 'an unknown type') + ' for ' + url +
                          (r.redirected ? ' — signed out?' : ''));
        }
        return r.json().then(function (j) {
          if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
          return j;
        });
      });
  }

  var wired = false;
  function ensureLayers() {
    if (map.getSource(SRC)) return;
    addLayers();
    if (!visible) setVisible(false);
  }
  function addLayers() {
    // A basemap switch (setStyle) drops the source and layers; they are put
    // back on style.load (init below). The layer-scoped click handlers are
    // kept by the map across styles, so they are wired once, not per style.
    map.addSource(SRC, { type: 'geojson', data: features });
    // A white casing under every trace, so a coloured line reads on grey
    // hillshade, a slope ramp and imagery alike.
    map.addLayer({ id: CASE, type: 'line', source: SRC,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#fff', 'line-opacity': 0.8, 'line-width': 4.4 } });
    // A wide transparent line under the visible one: traces are hairlines and
    // clicking a 2 px target is not usable.
    map.addLayer({ id: HIT, type: 'line', source: SRC,
      paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 14 } });
    Object.keys(STYLES).forEach(function (st) {
      var paint = { 'line-color': '#7b3fa0', 'line-width': 2.2 };
      if (DASH[st]) paint['line-dasharray'] = DASH[st];
      map.addLayer({ id: STYLES[st], type: 'line', source: SRC,
        layout: { 'line-cap': st === 'dot' ? 'round' : 'butt', 'line-join': 'round' },
        paint: paint });
    });
    applyStyle();
    map.addLayer({ id: SEL, type: 'line', source: SRC,
      filter: ['==', ['get', 'id'], -1],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffb300', 'line-width': 4 } });
    map.addSource(EDIT_SRC, { type: 'geojson', data: handlesFC() });
    map.addLayer({ id: MID, type: 'circle', source: EDIT_SRC, filter: ['==', ['get', 'role'], 'm'],
      paint: { 'circle-radius': 3.5, 'circle-color': '#fff', 'circle-opacity': 0.85,
               'circle-stroke-color': '#555', 'circle-stroke-width': 1 } });
    map.addLayer({ id: VTX, type: 'circle', source: EDIT_SRC, filter: ['==', ['get', 'role'], 'v'],
      paint: { 'circle-radius': ['case', ['==', ['get', 'a'], true], 6.5, 5],
               'circle-color': ['case', ['==', ['get', 'a'], true], '#e65100', '#fff'],
               'circle-stroke-color': ['case', ['==', ['get', 'a'], true], '#fff', '#e65100'],
               'circle-stroke-width': 2 } });
    if (wired) return;
    wired = true;
    // Hand back OUR feature (full notes, not the map's flattened copy) and
    // let the host decide what to do with it.
    function picked(hit, lngLat) {
      if (!hit) return;
      var f = features.features.filter(function (x) {
        return x.properties.id === hit.properties.id;
      })[0];
      if (!f) return;
      if (onPick) onPick(f, lngLat); else select(f.properties.id);
    }
    if (window.LSTools && LSTools.clicks) {
      // A REFERENCE entry on the host's dispatcher: highest priority of the
      // reference layers, so a scarp under the cursor wins over a fault or a
      // mirror, and it is blocked whenever any tool holds the map -- mid-trace
      // clicks belong to Terra Draw, and a scarp popup during a measure or an
      // InSAR sample was never wanted either.
      LSTools.clicks.register({ id: 'scarps', kind: 'reference', priority: 40,
                                layers: [HIT],
                                handler: function (f, e) { picked(f, e.lngLat); } });
    } else {
      // Standalone (no LSTools on the page): the old direct handler.
      map.on('click', HIT, function (e) {
        if (mode === 'draw') return;
        picked(e.features && e.features[0], e.lngLat);
      });
    }
    // Through the cursor owner model, so hovering a trace mid-measure can no
    // longer replace the tool's crosshair with a pointer and then clear it to
    // nothing on leave -- the one path that used to stomp an active tool.
    // typeof-guarded so this module still loads on a page without LSTools.
    map.on('mouseenter', HIT, function () {
      if (window.LSTools) LSTools.cursor.set('hover', 'pointer');
      else map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', HIT, function () {
      if (window.LSTools) LSTools.cursor.clear('hover');
      else map.getCanvas().style.cursor = '';
    });
  }

  // Colour and the per-style filters from the kinds vocabulary, plus the
  // host's kind filter. Kinds the vocabulary lacks draw as plain scarps
  // rather than vanishing.
  function kindInfo(id) {
    for (var i = 0; i < kinds.length; i++) if (kinds[i].id === id) return kinds[i];
    return { id: id, label: id, color: '#7b3fa0', dash: 'dash' };
  }
  function applyStyle() {
    if (!map || !map.getLayer(STYLES.solid)) return;
    var color = ['match', ['get', 'kind']];
    var byStyle = { solid: [], dash: [], dot: [] };
    kinds.forEach(function (k) {
      color.push(k.id, k.color);
      if (!hiddenKinds[k.id]) byStyle[k.dash || 'solid'].push(k.id);
    });
    color.push('#7b3fa0');
    var shown = byStyle.solid.concat(byStyle.dash, byStyle.dot);
    function f(list) {
      var k = ['in', ['get', 'kind'], ['literal', list]];
      return groupFilter == null ? k : ['all', k, ['==', ['get', 'group_id'], groupFilter]];
    }
    Object.keys(STYLES).forEach(function (st) {
      map.setPaintProperty(STYLES[st], 'line-color', color);
      map.setFilter(STYLES[st], f(byStyle[st]));
    });
    [CASE, HIT].forEach(function (id) { map.setFilter(id, f(shown)); });
  }

  function redraw() {
    if (map.getSource(SRC)) map.getSource(SRC).setData(features);
    if (map.getSource(EDIT_SRC)) map.getSource(EDIT_SRC).setData(handlesFC());
    applyStyle();
    if (map.getLayer(SEL)) {
      map.setFilter(SEL, ['==', ['get', 'id'], selectedId == null ? -1 : selectedId]);
    }
    syncPlus();
    onChange();
  }

  function load() {
    var at = editSeq;
    return req(api + 'api/scarps/').then(function (fc) {
      // A list fetched before the latest local edit, or while a save or a
      // drag is still under way, would draw old geometry over new. Drop it:
      // the save that follows asks again if the host wants a reload.
      if (at !== editSeq || savesInFlight() || (gesture && gesture.type === 'drag')) return features;
      if (fc.kinds && fc.kinds.length) kinds = fc.kinds;
      if (fc.groups) groups = fc.groups;
      features = fc; ensureLayers(); redraw(); return fc;
    });
  }

  function select(id) { if (id !== selectedId) activeVtx = null; selectedId = id; redraw(); }
  function selected() {
    if (selectedId == null) return null;
    return features.features.filter(function (f) {
      return f.properties.id === selectedId;
    })[0] || null;
  }

  function upsert(fc) {
    (fc && fc.features || []).forEach(function (nf) {
      var i = -1;
      features.features.forEach(function (f, k) {
        if (f.properties.id === nf.properties.id) i = k;
      });
      if (i >= 0) features.features[i] = nf; else features.features.push(nf);
    });
  }

  // Save a newly finished line as the current draw kind and group, with
  // traced_on from the host. Every way a line ends comes here: Terra Draw's
  // own finish (Enter, double-click, a click back on the last vertex) and
  // the editor's secondary click (finishSecondary).
  function saveNew(geom) {
    // One line per arming; and never switch Terra Draw's mode from inside
    // its own finish event (see the note where finish is handled).
    if (explicitDraw) setTimeout(function () { armDraw(false); }, 0);
    if (extendId != null) { var xid = extendId; extendId = null; return appendTo(xid, geom); }
    if (snapsKind(drawKind) && geom.coordinates.length >= 2) {
      var gc = geom.coordinates, n = gc.length - 1;
      gc[0] = snapLL({ lng: gc[0][0], lat: gc[0][1] }, null);
      gc[n] = snapLL({ lng: gc[n][0], lat: gc[n][1] }, null);
    }
    var ctx = null;
    try { ctx = drawContext ? drawContext() : null; } catch (e) { ctx = null; }
    var k = drawKind, g = drawGroup;
    // A kind filtered off would save a line the tracer then cannot see.
    if (hiddenKinds[k]) { delete hiddenKinds[k]; }
    return req(api + 'api/scarps/create/', { method: 'POST',
        body: { geometry: geom, notes: '', kind: k, traced_on: ctx, group_id: g } })
      .then(function (res) {
        // `select`: the row the new line now is (a host may have fused it
        // into another); `reload`: other rows changed with it.
        upsert(res.scarps); selectedId = res.select != null ? res.select : res.id;
        if (g != null) bumpGroupCounts();
        redraw();
        flash(kindInfo(k).label + ' saved — add a note.');
        if (res.reload) return load();
      })
      .catch(function (e) { flash('Could not save: ' + e.message, true); });
  }

  /** Start tracing. Each finished line is saved immediately, as
   *  opts.kind (default: the last kind set) with traced_on = opts.context(). */
  function startDraw(opts) {
    var TD = opts.terraDraw, ADAPT = opts.adapter;
    if (opts.kind) drawKind = opts.kind;
    if (opts.context) drawContext = opts.context;
    if ('group' in opts) drawGroup = opts.group;
    explicitDraw = !!opts.explicit;
    snapKinds = opts.snapKinds || null;
    armed = !explicitDraw; extendId = null; activeVtx = null;
    stopDraw();
    td = new TD.TerraDraw({
      adapter: new ADAPT.TerraDrawMapLibreGLAdapter({ map: map }),
      // Select mode is registered but never switched to, for the same reason
      // as in revise.js: Terra Draw validates added features against the
      // instantiated modes, and a mode that is not registered makes its
      // features invalid rather than merely unselectable.
      modes: [
        (lineMode = new TD.TerraDrawLineStringMode({
          // pointerDistance defaults to 40 px, and in line mode that means a
          // click within 40 px of the previous vertex FINISHES the line. At
          // normal tracing spacing almost every click lands inside that, so
          // you get a string of single segments instead of a path — which is
          // exactly what happened here, and the same trap the polygon tool
          // already carries a comment about (premature close, dropped points).
          // 8 px means only a deliberate click back on the last point ends it.
          pointerDistance: 8,
          keyEvents: { finish: 'Enter', cancel: 'Escape' }
        })),
        new TD.TerraDrawSelectMode()
      ]
    });
    td.start();
    // 'select' with no flags is Terra Draw doing nothing: the idle state of
    // an explicit session.
    td.setMode(armed ? 'linestring' : 'select');
    mode = 'draw';
    // Double-click finishes a line; without this it also zooms the map, which
    // throws the view off the feature you just traced.
    map.doubleClickZoom.disable();
    finished = {};
    td.on('finish', function (id) {
      finished[id] = true;
      var snap = td.getSnapshot().filter(function (f) { return f.id === id; })[0];
      if (!snap) return;
      var geom = round(snap.geometry);
      // Drop Terra Draw's copy on the next tick rather than inside its own
      // finish handler — the saved scarp is redrawn from our source, and
      // mutating its feature list mid-event leaves the next trace in a bad
      // state.
      setTimeout(function () { try { td && td.removeFeatures([id]); } catch (e) {} }, 0);
      saveNew(geom);
    });
    attachEditor();
    onChange();
  }

  function stopDraw() {
    detachEditor();
    if (td) { try { td.stop(); } catch (e) {} td = null; }
    if (map && map.doubleClickZoom) map.doubleClickZoom.enable();
    mode = null; lineMode = null;
    if (map) setCursor('');
    clearPlus();
    if (map && map.getSource(EDIT_SRC)) map.getSource(EDIT_SRC).setData(handlesFC());
    onChange();
  }

  // ---- editing in the session (2026-10-11, Hig) ---------------------------
  // With no line in progress, a click decides what it is for: on an existing
  // trace it selects that trace for editing; on open ground it starts a new
  // line. The selected trace shows its vertices (drag to move, right-click to
  // delete) and midpoints (drag to add a vertex); every change saves on
  // release, and Ctrl/Cmd-Z undoes the last one. Terra Draw only ever draws
  // new lines: it listens for pointer events on the map CANVAS and takes a
  // pointerup without a drag as a click, so these listeners sit on the map
  // CONTAINER in the capture phase and keep a gesture away from it -- and
  // from MapLibre's pan and rotate -- by stopping it there. preventDefault
  // on pointerdown also suppresses the compatibility mouse events MapLibre
  // pans on. Open-ground gestures pass straight through.
  var finished = {};             // Terra Draw ids already finished (removed a tick later)
  var gesture = null;            // {type: 'drag'|'pick', ...}
  var undoStack = [];            // [{id, geometry}] before each edit, newest last
  var UNDO_MAX = 50, HANDLE_PX = 7, GRAB_PX = 14;
  var lineMode = null;           // the Terra Draw line mode (seeded when extending)

  function partsOf(g) {
    return g.type === 'MultiLineString' ? g.coordinates : [g.coordinates];
  }
  function handlesFC() {
    var out = { type: 'FeatureCollection', features: [] };
    var f = mode === 'draw' ? selected() : null;
    if (!f) return out;
    var av = (activeVtx && activeVtx.id === f.properties.id) ? activeVtx : null;
    partsOf(f.geometry).forEach(function (part, p) {
      part.forEach(function (c, i) {
        out.features.push({ type: 'Feature',
                            properties: { role: 'v', p: p, i: i, a: !!(av && av.p === p && av.i === i) },
                            geometry: { type: 'Point', coordinates: c } });
        if (i > 0) {
          var a = part[i - 1];
          out.features.push({ type: 'Feature', properties: { role: 'm', p: p, i: i },
                              geometry: { type: 'Point', coordinates: [(a[0] + c[0]) / 2, (a[1] + c[1]) / 2] } });
        }
      });
    });
    return out;
  }
  function lineInProgress() {
    if (!td) return false;
    try {
      return td.getSnapshot().some(function (f) {
        return f.geometry.type === 'LineString' && !finished[f.id];
      });
    } catch (e) { return false; }
  }
  function canvasPoint(e) {
    var r = map.getCanvas().getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  // The handle of the selected trace nearest the pointer, within GRAB_PX.
  // Worked out from the geometry, not queryRenderedFeatures: a rendered query
  // returns nothing for a moment after every setData (each redraw), which
  // made the hover cursor flicker and a grab miss (Hig, 2026-11-17). The
  // radius is generous; where handles crowd, the nearest wins, a vertex
  // counting as a little nearer than a midpoint.
  function handleAt(pt) {
    var f = selected();
    if (!f) return null;
    var best = null, bestD = GRAB_PX;
    function test(c, role, p, i) {
      var q = map.project(c);
      var d = Math.hypot(q.x - pt[0], q.y - pt[1]) * (role === 'v' ? 0.7 : 1);
      if (d <= bestD) {
        bestD = d;
        best = { properties: { role: role, p: p, i: i }, geometry: { type: 'Point', coordinates: c } };
      }
    }
    partsOf(f.geometry).forEach(function (part, p) {
      part.forEach(function (c, i) {
        test(c, 'v', p, i);
        if (i > 0) { var a = part[i - 1]; test([(a[0] + c[0]) / 2, (a[1] + c[1]) / 2], 'm', p, i); }
      });
    });
    return best;
  }
  function traceAt(pt) {
    if (!map.getLayer(HIT)) return null;
    var h = map.queryRenderedFeatures(pt, { layers: [HIT] })[0];
    return h ? h.properties.id : null;
  }
  function take(e) { e.stopPropagation(); e.preventDefault(); }
  // Route the rest of a taken gesture to the container, so a drag released
  // off the map still ends (and never reaches Terra Draw's canvas).
  function capture(e) {
    try { map.getContainer().setPointerCapture(e.pointerId); } catch (err) {}
  }
  // Terra Draw writes the canvas's inline cursor on every pointer move, so a
  // cursor set the ordinary way (LSTools, inline style) is overwritten a
  // moment later and flickers. The session's hover cues therefore go through
  // a stylesheet rule with !important, keyed on an attribute of the map
  // container; with no cue the attribute is dropped and the cursor is
  // whatever the host and Terra Draw make it.
  var cursorCss = false;
  function setCursor(c) {
    var el = map.getContainer();
    if (!cursorCss) {
      cursorCss = true;
      var st = document.createElement('style');
      st.textContent = ['move', 'pointer', 'crosshair'].map(function (k) {
        return '[data-ls-edit-cursor="' + k + '"] canvas{cursor:' + k + ' !important}';
      }).join('');
      document.head.appendChild(st);
    }
    if (c) el.setAttribute('data-ls-edit-cursor', c);
    else el.removeAttribute('data-ls-edit-cursor');
  }
  function cloneGeom(g) { return JSON.parse(JSON.stringify(g)); }
  function setGeom(f, parts) {
    f.geometry = { type: 'MultiLineString', coordinates: parts };
  }
  function pushUndo(f, before) {
    undoStack.push({ id: f.properties.id, geometry: before });
    if (undoStack.length > UNDO_MAX) undoStack.shift();
  }
  // SAVES ARE SERIALISED PER TRACE, LATEST WINS (2026-11-16). Every edit used
  // to POST at once and apply whatever came back. Deleting a run of vertices
  // quickly then showed them vanish and return: the answer to the first
  // delete (the line minus ONE vertex) arrived after three more had been
  // removed locally and was drawn over them, and the reload a host may ask
  // for did the same again. Now one request per trace is in flight at a
  // time; an edit made meanwhile is sent when it returns, and an answer that
  // is already out of date is never drawn.
  var saving = {};               // id -> {seq, inflight, promise}
  var editSeq = 0;               // bumped on every local geometry edit
  function savesInFlight() {
    return Object.keys(saving).some(function (k) { return saving[k].inflight; });
  }
  function saveGeom(f, before) {
    var id = f.properties.id;
    var st = saving[id] || (saving[id] = { seq: 0, inflight: false, promise: null });
    st.seq++; editSeq++;
    if (st.inflight) return st.promise;      // picked up when the request in flight returns
    function send() {
      var cur = byId(id), mine = st.seq;
      if (!cur) { st.inflight = false; return Promise.resolve(); }
      st.inflight = true;
      return req(api + 'api/scarps/' + id + '/', { method: 'POST', body: { geometry: round(cur.geometry) } })
        .then(function (res) {
          if (st.seq !== mine) return send();          // edited again meanwhile: this answer is stale
          st.inflight = false;
          upsert(res.scarps);
          if (res.select != null && res.select !== id) {      // fused into another row
            features.features = features.features.filter(function (x) { return x.properties.id !== id; });
            if (selectedId === id) selectedId = res.select;
            activeVtx = null;
          }
          redraw(); flash('Trace updated.');
          if (res.reload) return load();
        })
        .catch(function (err) {
          st.inflight = false;
          // Several edits may be riding on this request, so there is no
          // single "before" to put back: show what the server holds.
          flash('Could not save the edit: ' + err.message, true);
          return load();
        });
    }
    return (st.promise = send());
  }
  // Remove vertex i of part p. False (with a message) if the part would be
  // left with fewer than two.
  function removeVertex(f, p, i) {
    var parts = partsOf(f.geometry).map(function (part) { return part.slice(); });
    if (!parts[p] || parts[p].length <= 2) {
      flash('A line needs two vertices — delete the whole line instead.', true);
      return false;
    }
    var before = cloneGeom(f.geometry);
    parts[p].splice(i, 1);
    setGeom(f, parts); pushUndo(f, before); redraw(); saveGeom(f, before);
    return true;
  }
  // Explicit sessions: allow (or stop allowing) one new line to be drawn.
  function armDraw(on) {
    armed = !!on || !explicitDraw;
    if (!on) extendId = null;
    if (td && mode === 'draw') {
      try { td.setMode(armed ? 'linestring' : 'select'); } catch (e) {}
    }
    if (armed && extendId == null && selectedId != null) select(null);
    else { syncPlus(); onChange(); }
  }
  // Draw on from the END of the selected trace: the next line drawn is
  // appended to it instead of being saved as a new trace.
  function extendSelected(at) {
    var f = selected();
    if (!f) return false;
    extendId = f.properties.id;
    extendAt = at === 'start' ? 'start' : 'end';
    armDraw(true);
    // Start Terra Draw's line ON the old end, so the rubber band runs from
    // it to the pointer at once, as in QGIS. (Without this nothing showed
    // until the first click, and the join to the old end only on finishing:
    // it read as the "+" not working.) If the seed fails the old behaviour
    // remains: the first click starts the line.
    try {
      var parts = partsOf(f.geometry);
      var c = extendAt === 'start' ? parts[0][0] : parts[parts.length - 1][parts[parts.length - 1].length - 1];
      var q = map.project(c);
      lineMode.onClick({ lng: c[0], lat: c[1], containerX: q.x, containerY: q.y,
                         button: 'left', heldKeys: [], isContextMenu: false });
    } catch (err) {}
    syncPlus();
    flash('Extending from the ' + (extendAt === 'start' ? 'start' : 'end') +
          ' of the line: click onward from it; right-click or Enter finishes, Esc cancels.');
    return true;
  }
  // The "+" at each end of the selected trace (as in QGIS): click it to draw
  // on from that end. HTML markers, not a map layer: the raster basemap
  // styles carry no glyphs, so a text symbol would not draw; and a marker's
  // click never reaches the canvas listeners below.
  var plusMarkers = [], plusKey = '';
  function clearPlus() {
    plusMarkers.forEach(function (m) { try { m.remove(); } catch (e) {} });
    plusMarkers = []; plusKey = '';
  }
  function syncPlus() {
    var f = mode === 'draw' ? selected() : null;
    var show = f && !lineInProgress() && extendId == null && !(explicitDraw && armed) &&
               !(gesture && gesture.type === 'drag') && typeof maplibregl !== 'undefined';
    if (!show) { if (plusMarkers.length) clearPlus(); return; }
    var parts = partsOf(f.geometry), first = parts[0], last = parts[parts.length - 1];
    var ends = [['start', first[0], first[1]], ['end', last[last.length - 1], last[last.length - 2]]];
    var key = f.properties.id + '|' + JSON.stringify(ends) + '|' + map.getZoom().toFixed(2);
    if (key === plusKey) return;
    clearPlus(); plusKey = key;
    ends.forEach(function (e) {
      if (!e[1] || !e[2]) return;
      // 22 px beyond the end, along the line's own direction there
      var a = map.project(e[1]), b = map.project(e[2]);
      var dx = a.x - b.x, dy = a.y - b.y, L = Math.hypot(dx, dy) || 1;
      var el = document.createElement('div');
      el.textContent = '+';
      el.title = 'Extend the line from this end';
      el.style.cssText = 'width:22px;height:22px;text-align:center;border-radius:50%;box-sizing:border-box;' +
        'background:#fff;border:2px solid #e65100;color:#e65100;font:700 17px/18px sans-serif;' +
        'cursor:pointer;box-shadow:0 0 3px rgba(0,0,0,.5);user-select:none;';
      el.addEventListener('pointerdown', function (ev) { ev.stopPropagation(); });
      el.addEventListener('click', function (ev) { ev.stopPropagation(); extendSelected(e[0]); });
      plusMarkers.push(new maplibregl.Marker({ element: el, offset: [dx / L * 22, dy / L * 22] })
        .setLngLat(e[1]).addTo(map));
    });
  }
  function appendTo(id, geom) {
    var f = byId(id);
    if (!f) { flash('That line no longer exists.', true); return Promise.resolve(); }
    var parts = partsOf(f.geometry).map(function (part) { return part.slice(); });
    var atStart = extendAt === 'start';
    var last = atStart ? parts[0] : parts[parts.length - 1], add = geom.coordinates.slice();
    var old = atStart ? last[0] : last[last.length - 1];
    // A first click made on the old end is the old end, not a new vertex.
    var a = map.project(old), b = map.project(add[0]);
    if (Math.abs(a.x - b.x) <= HANDLE_PX + 3 && Math.abs(a.y - b.y) <= HANDLE_PX + 3) add.shift();
    if (!add.length) return Promise.resolve();
    if (!atStart && snapsKind(f.properties.kind)) {
      var e = add[add.length - 1];
      add[add.length - 1] = snapLL({ lng: e[0], lat: e[1] }, id);
    }
    var before = cloneGeom(f.geometry);
    // Drawn outward from the start, so reversed it runs INTO the old start
    // and the line keeps its direction.
    if (atStart) parts[0] = add.reverse().concat(last);
    else parts[parts.length - 1] = last.concat(add);
    setGeom(f, parts); pushUndo(f, before); selectedId = id; redraw();
    return saveGeom(f, before);
  }

  // A secondary click: the right button, or Ctrl-click on a Mac (which some
  // browsers report as button 0 with ctrlKey, then a contextmenu).
  var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || '');
  function secondary(e) { return e.button === 2 || (IS_MAC && e.button === 0 && e.ctrlKey); }
  function onDown(e) {
    if (!e.isPrimary || (e.button !== 0 && e.button !== 2)) return;
    // Only the map itself: a marker, popup or control over it (the group
    // profile's draggable plane ends, say) keeps its own pointer events.
    if (e.target !== map.getCanvas()) return;
    var sec = secondary(e);
    // Mid-line a primary click is Terra Draw's (a vertex, or a finish back on
    // the last one). A secondary click ends the line here and saves it: the
    // point clicked becomes the last vertex, or, on a vertex already drawn,
    // the line ends as it is (Hig, 2026-10-11). Terra Draw itself only ends
    // a line on its last vertex, and on a Mac a Ctrl-click may not reach it
    // as a click at all.
    if (lineInProgress()) {
      if (sec) { take(e); gesture = { type: 'rclick' }; finishSecondary(e); }
      return;
    }
    // Armed in an explicit session: this click starts the one line, wherever
    // it lands -- on open ground, on another line, on a handle.
    if (explicitDraw && armed && !sec) {
      if (selectedId != null && extendId == null) select(null);
      return;
    }
    var pt = canvasPoint(e), f = selected();
    var h = f ? handleAt(pt) : null;
    if (h) {
      take(e);
      var parts = partsOf(f.geometry).map(function (part) { return part.slice(); });
      var before = cloneGeom(f.geometry), p = h.properties.p, i = h.properties.i;
      if (sec && h.properties.role !== 'v') { gesture = { type: 'rclick' }; return; }
      if (sec) {
        gesture = { type: 'rclick' };
        if (removeVertex(f, p, i)) activeVtx = null;
        return;
      }
      if (h.properties.role === 'm') parts[p].splice(i, 0, h.geometry.coordinates.slice());
      setGeom(f, parts); redraw();
      gesture = { type: 'drag', f: f, p: p, i: i, before: before, moved: h.properties.role === 'm',
                  role: h.properties.role, x: e.clientX, y: e.clientY };
      capture(e);
      return;
    }
    if (sec) {
      // No line, no handle: keep it from Terra Draw (which would start a line
      // with any button) and let the contextmenu through to the host, which
      // copies the coordinates as it does outside the tool.
      take(e);
      gesture = { type: 'rclick' };
      return;
    }
    var id = traceAt(pt);
    if (id != null) {
      take(e);
      gesture = { type: 'pick', id: id };
      capture(e);
      return;
    }
    // Open ground: put down the selection and let the click through, where
    // Terra Draw starts a new line with it.
    if (selectedId != null) select(null);
  }
  function onMove(e) {
    if (!gesture) {
      // Hover cue: a handle or another trace is editable, open ground draws.
      if (mode !== 'draw') return;
      if (lineInProgress()) { setCursor('crosshair'); return; }
      var pt = canvasPoint(e), f = selected();
      var over = (f && handleAt(pt)) ? 'move' : (traceAt(pt) != null ? 'pointer' : '');
      if (explicitDraw && armed) over = '';       // the next click draws, whatever is under it
      setCursor(over || ((explicitDraw && !armed) ? '' : 'crosshair'));
      return;
    }
    take(e);
    if (gesture.type !== 'drag') return;
    if (Math.abs(e.clientX - gesture.x) + Math.abs(e.clientY - gesture.y) > 2) gesture.moved = true;
    var ll = map.unproject(canvasPoint(e));
    var parts = partsOf(gesture.f.geometry).map(function (part) { return part.slice(); });
    var atEnd = gesture.i === 0 || gesture.i === parts[gesture.p].length - 1;
    parts[gesture.p][gesture.i] = (atEnd && snapsKind(gesture.f.properties.kind))
      ? snapLL(ll, gesture.f.properties.id) : [ll.lng, ll.lat];
    setGeom(gesture.f, parts);
    redraw();
  }
  function onUp(e) {
    if (!gesture) return;
    take(e);
    var g = gesture; gesture = null;
    if (g.type === 'pick') { select(g.id); return; }
    if (g.type === 'rclick') return;
    // Either way this vertex is now the active one (Delete removes it).
    activeVtx = { id: g.f.properties.id, p: g.p, i: g.i };
    if (g.moved) { pushUndo(g.f, g.before); redraw(); saveGeom(g.f, g.before); }
    else { g.f.geometry = g.before; redraw(); }
  }
  // Capture lost without a pointerup (the window lost focus, a touch was
  // cancelled): put a half-dragged vertex back.
  function onLost() {
    if (!gesture) return;
    if (gesture.type === 'drag') { gesture.f.geometry = gesture.before; redraw(); }
    gesture = null;
  }
  var swallowMenu = 0;          // time a secondary click ended a line: its contextmenu is not a copy
  function finishSecondary(e) {
    var line = null;
    try {
      line = td.getSnapshot().filter(function (f) {
        return f.geometry.type === 'LineString' && !finished[f.id];
      })[0];
    } catch (err) { line = null; }
    if (!line) return;
    // Terra Draw's in-progress line ends with the point following the cursor;
    // the vertices actually placed are the rest.
    var placed = line.geometry.coordinates.slice(0, -1);
    var pt = canvasPoint(e), ll = map.unproject(pt);
    var onVertex = placed.some(function (c) {
      var q = map.project(c);
      return Math.abs(q.x - pt[0]) <= HANDLE_PX + 3 && Math.abs(q.y - pt[1]) <= HANDLE_PX + 3;
    });
    var coords = onVertex ? placed : placed.concat([[ll.lng, ll.lat]]);
    swallowMenu = Date.now();
    if (coords.length < 2) { flash('A line needs two points.', true); return; }
    // End Terra Draw's line without its finish event (switching modes clears
    // the drawing), then save ours.
    finished[line.id] = true;
    try { td.setMode('select'); td.setMode('linestring'); } catch (err) {}
    saveNew(round({ type: 'LineString', coordinates: coords }));
  }
  function onContext(e) {
    if (mode !== 'draw') return;
    if (Date.now() - swallowMenu < 1500) { swallowMenu = 0; take(e); return; }
    // Mid-line the secondary click added a vertex (or finished the line), and
    // on a handle it edited: neither is a request for the coordinates or the
    // browser's menu. Anywhere else it passes, and the host copies "lat, lon".
    if (lineInProgress() || (selected() && handleAt(canvasPoint(e)))) take(e);
  }
  function onKey(e) {
    if (mode !== 'draw') return;
    var t = e.target, tag = t && t.tagName ? t.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (t && t.isContentEditable)) return;
    if (lineInProgress()) {                     // Terra Draw's Enter / Escape
      // Esc drops the line; an extension is over with it (otherwise the next
      // line drawn would still be appended to the old one).
      if (e.key === 'Escape' && explicitDraw) setTimeout(function () { armDraw(false); }, 0);
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault();
      var u = undoStack.pop();
      if (!u) { flash('Nothing to undo.'); return; }
      var f = byId(u.id);
      if (!f) { flash('That trace no longer exists.', true); return; }
      var before = cloneGeom(f.geometry);
      activeVtx = null;
      f.geometry = u.geometry; selectedId = u.id; redraw();
      saveGeom(f, before);
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && activeVtx) {
      var af = byId(activeVtx.id);
      if (!af || af.properties.id !== selectedId) { activeVtx = null; return; }
      e.preventDefault();
      var av = activeVtx;
      if (removeVertex(af, av.p, av.i)) {
        // the next vertex slides into this index; stay on it (or on the new last)
        var n = partsOf(af.geometry)[av.p].length;
        activeVtx = { id: av.id, p: av.p, i: Math.min(av.i, n - 1) };
        redraw();
      }
    } else if (e.key === 'Escape' && explicitDraw && armed) {
      armDraw(false);
    } else if (e.key === 'Escape' && selectedId != null) {
      select(null);
    }
  }
  var editorOn = false;
  function attachEditor() {
    if (editorOn) return;
    editorOn = true;
    var el = map.getContainer();
    el.addEventListener('pointerdown', onDown, true);
    el.addEventListener('pointermove', onMove, true);
    el.addEventListener('pointerup', onUp, true);
    el.addEventListener('contextmenu', onContext, true);
    el.addEventListener('pointercancel', onLost, true);
    el.addEventListener('lostpointercapture', onLost, true);
    document.addEventListener('keydown', onKey);
  }
  function detachEditor() {
    if (!editorOn) return;
    editorOn = false;
    var el = map.getContainer();
    el.removeEventListener('pointerdown', onDown, true);
    el.removeEventListener('pointermove', onMove, true);
    el.removeEventListener('pointerup', onUp, true);
    el.removeEventListener('contextmenu', onContext, true);
    el.removeEventListener('pointercancel', onLost, true);
    el.removeEventListener('lostpointercapture', onLost, true);
    document.removeEventListener('keydown', onKey);
    if (gesture && gesture.type === 'drag') { gesture.f.geometry = gesture.before; }
    gesture = null;
    undoStack = [];
  }

  // Save fields ({notes, kind, group_id}) on the selected trace, or on `id`.
  function save(fields, id) {
    var f = id != null ? byId(id) : selected();
    if (!f) return Promise.reject(new Error('nothing selected'));
    return req(api + 'api/scarps/' + f.properties.id + '/',
               { method: 'POST', body: fields })
      .then(function (res) {
        upsert(res.scarps);
        if ('group_id' in fields) bumpGroupCounts();
        redraw();
        if (res.reload) return load().then(function () { return res; });
        return res;
      });
  }
  // Group counts live on the group records; recount from the features we
  // hold rather than refetching everything after each regroup.
  function bumpGroupCounts() {
    groups.forEach(function (g) {
      g.n = features.features.filter(function (f) { return f.properties.group_id === g.id; }).length;
    });
  }
  function groupById(id) {
    return groups.filter(function (g) { return g.id === id; })[0] || null;
  }
  // Group writes answer with the whole group list; traces carry the group's
  // name for display, so a rename or delete refreshes them too.
  function takeGroups(res) {
    groups = res.groups || groups;
    features.features.forEach(function (f) {
      var g = groupById(f.properties.group_id);
      if (!g) f.properties.group_id = null;
      f.properties.group = g ? g.name : null;
    });
    if (groupFilter != null && !groupById(groupFilter)) groupFilter = null;
    if (drawGroup != null && !groupById(drawGroup)) drawGroup = null;
    redraw();
    return res;
  }
  function createGroup(name, notes) {
    return req(api + 'api/scarps/groups/create/', { method: 'POST', body: { name: name, notes: notes || '' } })
      .then(takeGroups);
  }
  function updateGroup(id, fields) {
    return req(api + 'api/scarps/groups/' + id + '/', { method: 'POST', body: fields })
      .then(takeGroups);
  }
  function saveNotes(text) { return save({ notes: text }); }
  function byId(id) {
    return features.features.filter(function (f) { return f.properties.id === id; })[0] || null;
  }

  function remove(id) {
    var f = id != null ? byId(id) : selected();
    if (!f) return Promise.reject(new Error('nothing selected'));
    var id = f.properties.id;
    return req(api + 'api/scarps/' + id + '/', { method: 'POST', body: { delete: true } })
      .then(function (res) {
        features.features = features.features.filter(function (x) {
          return x.properties.id !== id;
        });
        undoStack = undoStack.filter(function (u) { return u.id !== id; });
        selectedId = null; bumpGroupCounts(); redraw(); return res;
      });
  }

  // Remembered, not just applied: a basemap switch rebuilds the layers, and
  // the host may set this before init has a map at all.
  var visible = true;
  function setVisible(on) {
    visible = !!on;
    if (!map) return;
    ALL.forEach(function (id) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    });
  }

  function init(opts) {
    map = opts.map; api = opts.api || api; csrf = opts.csrf;
    onChange = opts.onChange || function () {};
    flash = opts.flash || function () {};
    onPick = opts.onPick || null;
    map.on('style.load', function () { ensureLayers(); redraw(); });
    return load();
  }

  // Move the trace layers to the top of the stack: a host that adds layers
  // after init (/lidar/ adds shading per survey) calls this so a trace is
  // never drawn under a hillshade.
  function raise() {
    if (!map) return;
    ALL.forEach(function (id) { if (map.getLayer(id)) map.moveLayer(id); });
  }

  return {
    init: init, load: load, startDraw: startDraw, stopDraw: stopDraw,
    select: select, selected: selected, saveNotes: saveNotes, save: save,
    remove: remove, byId: byId, raise: raise,
    setVisible: setVisible,
    kinds: function () { return kinds; },
    kindInfo: kindInfo,
    setDrawKind: function (k) { drawKind = k; },
    groups: function () { return groups; },
    groupById: groupById,
    createGroup: createGroup,
    updateGroup: updateGroup,
    setDrawGroup: function (g) { drawGroup = g == null || g === '' ? null : +g; },
    drawGroup: function () { return drawGroup; },
    // Show only one group's traces (id), or null for all.
    setGroupFilter: function (g) { groupFilter = g == null || g === '' ? null : +g; applyStyle(); },
    groupFilter: function () { return groupFilter; },
    // The GeoJSON download for one group (or all traces), for GIS.
    downloadUrl: function (g) {
      return api + 'api/scarps/?download=1' + (g != null ? '&group=' + g : '');
    },
    drawKind: function () { return drawKind; },
    // The kinds filtered off the map (ids), for the page's filter UI and URL.
    hiddenKinds: function () { return Object.keys(hiddenKinds); },
    // Filter kinds off the map (ids), or null for all.
    setHiddenKinds: function (ids) {
      hiddenKinds = {};
      (ids || []).forEach(function (k) { hiddenKinds[k] = true; });
      applyStyle();
    },
    isDrawing: function () { return mode === 'draw'; },
    // Explicit sessions (startDraw({explicit: true})): draw one line / stop.
    armDraw: armDraw,
    drawArmed: function () { return mode === 'draw' && armed; },
    extending: function () { return extendId; },
    extendSelected: extendSelected,
    activeVertex: function () { return activeVtx; },
    lineInProgress: lineInProgress,
    all: function () { return features; }
  };
})();
