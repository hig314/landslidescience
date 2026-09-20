/* scarps.js — trace scarps on the live map.
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

  var SRC = 'scarps-src', LINE = 'scarps-line', HIT = 'scarps-hit', SEL = 'scarps-sel';
  // Terra Draw's MapLibre adapter counts decimals and rejects anything finer
  // than its precision (9). The API serves ST_AsGeoJSON(geom, 6), so this is
  // headroom rather than a constraint — but rounding here keeps an untouched
  // line comparing equal after a round trip.
  var COORD_PRECISION = 9;

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
    // A basemap switch (setStyle) drops the source and layers; they are put
    // back on style.load (init below). The layer-scoped click handlers are
    // kept by the map across styles, so they are wired once, not per style.
    map.addSource(SRC, { type: 'geojson', data: features });
    // A wide transparent line under the visible one: scarps are hairlines and
    // clicking a 2 px target is not usable.
    map.addLayer({ id: HIT, type: 'line', source: SRC,
      paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 14 } });
    map.addLayer({ id: LINE, type: 'line', source: SRC,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#7b3fa0', 'line-width': 2.2,
               'line-dasharray': [3, 1.5] } });
    map.addLayer({ id: SEL, type: 'line', source: SRC,
      filter: ['==', ['get', 'id'], -1],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffb300', 'line-width': 4 } });
    if (wired) return;
    wired = true;
    map.on('click', HIT, function (e) {
      if (mode === 'draw') return;      // mid-trace clicks belong to Terra Draw
      var hit = e.features && e.features[0];
      if (!hit) return;
      // Hand back OUR feature (full notes, not the map's flattened copy) and
      // let the host decide: select for editing, or show a popup.
      var f = features.features.filter(function (x) {
        return x.properties.id === hit.properties.id;
      })[0];
      if (!f) return;
      if (onPick) onPick(f, e.lngLat); else select(f.properties.id);
    });
    map.on('mouseenter', HIT, function () { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', HIT, function () { map.getCanvas().style.cursor = ''; });
  }

  function redraw() {
    if (map.getSource(SRC)) map.getSource(SRC).setData(features);
    if (map.getLayer(SEL)) {
      map.setFilter(SEL, ['==', ['get', 'id'], selectedId == null ? -1 : selectedId]);
    }
    onChange();
  }

  function load() {
    return req(api + 'api/scarps/').then(function (fc) {
      features = fc; ensureLayers(); redraw(); return fc;
    });
  }

  function select(id) { selectedId = id; redraw(); }
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

  /** Start tracing. Each finished line is saved immediately. */
  function startDraw(opts) {
    var TD = opts.terraDraw, ADAPT = opts.adapter;
    stopDraw();
    td = new TD.TerraDraw({
      adapter: new ADAPT.TerraDrawMapLibreGLAdapter({ map: map }),
      // Select mode is registered but never switched to, for the same reason
      // as in revise.js: Terra Draw validates added features against the
      // instantiated modes, and a mode that is not registered makes its
      // features invalid rather than merely unselectable.
      modes: [
        new TD.TerraDrawLineStringMode({
          // pointerDistance defaults to 40 px, and in line mode that means a
          // click within 40 px of the previous vertex FINISHES the line. At
          // normal tracing spacing almost every click lands inside that, so
          // you get a string of single segments instead of a path — which is
          // exactly what happened here, and the same trap the polygon tool
          // already carries a comment about (premature close, dropped points).
          // 8 px means only a deliberate click back on the last point ends it.
          pointerDistance: 8,
          keyEvents: { finish: 'Enter', cancel: 'Escape' }
        }),
        new TD.TerraDrawSelectMode()
      ]
    });
    td.start();
    td.setMode('linestring');
    mode = 'draw';
    // Double-click finishes a line; without this it also zooms the map, which
    // throws the view off the feature you just traced.
    map.doubleClickZoom.disable();
    td.on('finish', function (id) {
      var snap = td.getSnapshot().filter(function (f) { return f.id === id; })[0];
      if (!snap) return;
      var geom = round(snap.geometry);
      // Drop Terra Draw's copy on the next tick rather than inside its own
      // finish handler — the saved scarp is redrawn from our source, and
      // mutating its feature list mid-event leaves the next trace in a bad
      // state.
      setTimeout(function () { try { td && td.removeFeatures([id]); } catch (e) {} }, 0);
      req(api + 'api/scarps/create/', { method: 'POST',
          body: { geometry: geom, notes: '' } })
        .then(function (res) {
          upsert(res.scarps); selectedId = res.id; redraw();
          flash('Scarp saved — add a note.');
        })
        .catch(function (e) { flash('Could not save: ' + e.message, true); });
    });
    onChange();
  }

  function stopDraw() {
    if (td) { try { td.stop(); } catch (e) {} td = null; }
    if (map && map.doubleClickZoom) map.doubleClickZoom.enable();
    mode = null;
    onChange();
  }

  function saveNotes(text) {
    var f = selected();
    if (!f) return Promise.reject(new Error('nothing selected'));
    return req(api + 'api/scarps/' + f.properties.id + '/',
               { method: 'POST', body: { notes: text } })
      .then(function (res) { upsert(res.scarps); redraw(); return res; });
  }

  function remove() {
    var f = selected();
    if (!f) return Promise.reject(new Error('nothing selected'));
    var id = f.properties.id;
    return req(api + 'api/scarps/' + id + '/', { method: 'POST', body: { delete: true } })
      .then(function (res) {
        features.features = features.features.filter(function (x) {
          return x.properties.id !== id;
        });
        selectedId = null; redraw(); return res;
      });
  }

  function setVisible(on) {
    [LINE, HIT, SEL].forEach(function (id) {
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

  return {
    init: init, load: load, startDraw: startDraw, stopDraw: stopDraw,
    select: select, selected: selected, saveNotes: saveNotes, remove: remove,
    setVisible: setVisible,
    isDrawing: function () { return mode === 'draw'; },
    all: function () { return features; }
  };
})();
