/* sounding_points.js — hand-placed sounding points on /lidar/ (editors). Global `window.LSSoundPts`.
 *
 * Hig, 2026-11-17: "a simple interface to add new estimated sounding points. I'd like to be able to click
 * to add a point, with a type that is 'Estimated' by default, and new types can be created with a '+'
 * button ... make sure I can have both the profile and the create point tool active at the same time."
 *
 * An LSTools mode ('spoint'). While it holds the map, a click on open ground places a new point there
 * (a purple marker) and a click on an existing hand-placed point opens it for editing; the floating
 * panel #sp-panel holds the form. The water level is read from the surface under the click (the host's
 * `surface(lon, lat)`: over a lake, the lidar's water surface) and can be overwritten; the bed elevation
 * is what the editor estimates; depth = level - bed is shown and stored, so the bed is derived like
 * every other sounding's (inventory/soundings.py, HAND-PLACED POINTS).
 *
 * The profile: the profile tool (profile.js) keeps its line and chart after its own mode is left, so a
 * profile drawn first stays on screen, hoverable, while this tool places points; and while this tool is
 * on, the profile's chart cursor is mirrored here -- a click on the map near the profile's hover marker
 * is no different from any other click, so place the point where the chart says.
 *
 *   LSSoundPts.init({map, api, csrf, surface, panel})
 */
window.LSSoundPts = (function () {
  'use strict';

  var map = null, api = '/inventory/', csrf = function () { return ''; }, surface = null;
  var panel = null, fp = null, on = false, marker = null, types = [];
  var cur = null;              // {id|null, lon, lat}: the point in the form
  var HAND = 'hand_placed';

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function say(msg, bad) {
    var m = $('sp-msg'); if (!m) return;
    m.textContent = msg || ''; m.style.color = bad ? '#b3261e' : '#1b7a3d';
    m.style.fontWeight = bad ? '700' : '';
    m.style.background = bad ? '#fdecea' : ''; m.style.padding = bad ? '3px 5px' : '';   // a refusal must not be missed (2026-11-17)
    if (msg && !bad) setTimeout(function () { if (m.textContent === msg) m.textContent = ''; }, 4000);
  }
  function post(url, body) {
    return fetch(api + url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().catch(function () { return { error: 'HTTP ' + r.status + (r.redirected ? ' (signed out?)' : '') }; })
          .then(function (j) { if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
      });
  }

  // ---- types -----------------------------------------------------------------------------------
  function loadTypes() {
    return fetch(api + 'api/soundings/types/', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      .then(function (j) { types = j.types || []; fillTypes(); });
  }
  function fillTypes(pick) {
    var sel = $('sp-type'), v = pick || sel.value || 'Estimated';
    sel.innerHTML = types.map(function (t) {
      return '<option value="' + esc(t.name) + '" title="' + esc(t.notes || '') + '">' + esc(t.name) + '</option>';
    }).join('');
    sel.value = types.some(function (t) { return t.name === v; }) ? v : (types[0] ? types[0].name : '');
  }
  function newType() {
    var box = $('sp-newtype'); box.style.display = box.style.display === 'none' ? '' : 'none';
    if (box.style.display === '') $('sp-newname').focus();
  }
  function addType() {
    var name = $('sp-newname').value.trim();
    if (!name) return;
    post('api/soundings/types/create/', { name: name }).then(function (j) {
      types = j.types; fillTypes(name); $('sp-newname').value = ''; $('sp-newtype').style.display = 'none';
      say('Type "' + name + '" added.');
    }).catch(function (e) { say(e.message, true); });
  }

  // ---- the point in the form -------------------------------------------------------------------
  function num(id) { var v = $(id).value.trim(); return v === '' ? NaN : +v; }
  // Bed elevation OR depth (2026-11-17, Hig: "working from profiles bed elevation is a bit easier"): the one typed
  // last is kept, the other follows it -- and follows a change of the water level too.  The server takes the bed.
  var lead = 'bed';
  function depthText(from) {
    if (from === 'bed' || from === 'depth') lead = from;
    var lv = num('sp-level');
    if (lead === 'bed') {
      var bd = num('sp-bed'); $('sp-depthin').value = isFinite(lv) && isFinite(bd) ? (lv - bd).toFixed(2) : '';
    } else {
      var dp = num('sp-depthin'); $('sp-bed').value = isFinite(lv) && isFinite(dp) ? (lv - dp).toFixed(2) : '';
    }
    var b2 = num('sp-bed');
    $('sp-depth').textContent = isFinite(lv) && isFinite(b2) && b2 > lv ? 'the bed is above the water!'
      : 'kept: ' + (lead === 'bed' ? 'bed elevation' : 'depth') + ' (type either; the other follows)';
  }
  function setMarker(lon, lat) {
    if (!marker) {
      var el = document.createElement('div');
      el.style.cssText = 'width:14px;height:14px;border-radius:50%;background:rgba(123,31,162,.35);border:3px solid #7b1fa2;box-shadow:0 0 0 2px #fff';
      marker = new maplibregl.Marker({ element: el, draggable: true });
      marker.on('dragend', function () { var ll = marker.getLngLat(); place(ll.lng, ll.lat, true); });
    }
    marker.setLngLat([lon, lat]).addTo(map);
  }
  function clearMarker() { if (marker) marker.remove(); }
  function showForm(editing) {
    $('sp-where').textContent = cur ? cur.lat.toFixed(5) + ', ' + cur.lon.toFixed(5) : 'click the map';
    $('sp-save').textContent = editing ? 'Save changes' : 'Add point';
    $('sp-del').style.display = editing ? '' : 'none';
    $('sp-title').textContent = editing ? 'Edit point #' + cur.id : 'New point';
    depthText();
  }
  // A new location (a click on open ground, or the marker dragged): the water level is read again.
  function place(lon, lat, keepId) {
    cur = { id: keepId && cur ? cur.id : null, lon: lon, lat: lat };
    setMarker(lon, lat);
    if (!keepId) { $('sp-bed').value = ''; $('sp-depthin').value = ''; $('sp-notes').value = ''; }
    $('sp-levelsrc').textContent = 'reading the surface…';
    if (surface) surface(lon, lat).then(function (r) {
      if (!cur || cur.lon !== lon || cur.lat !== lat) return;
      if (r && isFinite(r.z)) { $('sp-level').value = r.z.toFixed(2); $('sp-levelsrc').textContent = 'from ' + r.src; }
      else $('sp-levelsrc').textContent = 'no surface here: type the water level';
      depthText();
    }).catch(function () { $('sp-levelsrc').textContent = 'surface read failed: type the water level'; });
    showForm(!!(keepId && cur.id));
    $('sp-bed').focus();
  }
  // An existing hand-placed point: its own values, nothing re-read.
  function edit(f) {
    var p = f.properties, lv = window.LSSoundings && LSSoundings.level(p.level_id);
    var c = f.geometry.coordinates;
    cur = { id: p.id, lon: c[0], lat: c[1] };
    setMarker(c[0], c[1]);
    $('sp-level').value = lv && lv.level_m != null ? Number(lv.level_m).toFixed(2) : '';
    $('sp-levelsrc').textContent = lv ? lv.source : '';
    $('sp-bed').value = p.z != null ? Number(p.z).toFixed(2) : ''; lead = 'bed';
    $('sp-sigma').value = p.sigma_m != null ? p.sigma_m : 1;
    $('sp-notes').value = p.notes || '';
    if (p.point_type) fillTypes(p.point_type);
    showForm(true);
  }
  function body() {
    return { lon: cur.lon, lat: cur.lat, bed_z: $('sp-bed').value, level_m: $('sp-level').value,
             sigma_m: $('sp-sigma').value, point_type: $('sp-type').value, notes: $('sp-notes').value,
             level_source: $('sp-levelsrc').textContent.replace(/^from /, 'read from ') };
  }
  function save() {
    if (!cur) { say('Click the map to place a point first.', true); return; }
    var req = cur.id ? post('api/soundings/points/' + cur.id + '/', body()) : post('api/soundings/points/create/', body());
    req.then(function (j) {
      say(cur.id ? 'Saved.' : 'Point #' + j.id + ' added.');
      cur = null; clearMarker(); showForm(false);
      if (window.LSSoundings) LSSoundings.reload();
      if (opts_.onSaved) opts_.onSaved();
    }).catch(function (e) { say(e.message, true); });
  }
  function del() {
    if (!cur || !cur.id || !window.confirm('Delete point #' + cur.id + '?')) return;
    post('api/soundings/points/' + cur.id + '/', { delete: true }).then(function () {
      say('Deleted.'); cur = null; clearMarker(); showForm(false);
      if (window.LSSoundings) LSSoundings.reload();
    }).catch(function (e) { say(e.message, true); });
  }

  // ---- depth labels beside the hand-placed points, while the tool is on (2026-11-17, Hig: "while the editing tool
  // is active the depth for the points should show next to them") -- HTML markers, so they need no glyphs in the
  // style and a click on one opens its point.
  var labels = [];
  function clearLabels() { labels.forEach(function (m) { m.remove(); }); labels = []; }
  function handFeatures() {
    var all = window.LSSoundings && LSSoundings.all();
    if (!all) return [];
    return all.features.filter(function (f) {
      var p = f.properties, sv = LSSoundings.survey(p.survey_id);
      return p.kind === 'sounding' && sv && sv.slug === HAND;
    });
  }
  function drawLabels() {
    clearLabels();
    if (!on) return;
    handFeatures().forEach(function (f) {
      var p = f.properties, el = document.createElement('div');
      el.style.cssText = 'font:600 11px sans-serif;color:#4a148c;background:rgba(255,255,255,.88);border:1px solid #7b1fa2;' +
                         'border-radius:3px;padding:0 3px;cursor:pointer;white-space:nowrap';
      el.textContent = Number(p.depth_m).toFixed(1) + ' m';
      el.title = (p.point_type || 'point') + ' #' + p.id + ': bed ' + (p.z != null ? Number(p.z).toFixed(2) : '?') +
                 ' m, depth ' + Number(p.depth_m).toFixed(2) + ' m' + (p.notes ? ' — ' + p.notes : '') + ' (click to edit)';
      el.addEventListener('click', function (ev) { ev.stopPropagation(); edit(f); });
      labels.push(new maplibregl.Marker({ element: el, anchor: 'left', offset: [9, 0] }).setLngLat(f.geometry.coordinates).addTo(map));
    });
  }

  // ---- the tool ----------------------------------------------------------------------------------
  function hitHand(e) {
    if (!window.LSSoundings || !map.getLayer('soundings-hit')) return null;
    var fs = map.queryRenderedFeatures([[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]], { layers: ['soundings-hit'] });
    for (var i = 0; i < fs.length; i++) {
      var sv = LSSoundings.survey(fs[i].properties.survey_id);
      if (sv && sv.slug === HAND && fs[i].properties.kind === 'sounding') {
        // queryRenderedFeatures flattens properties; take the full feature from the loaded data
        var all = LSSoundings.all(), id = fs[i].properties.id;
        var full = all && all.features.filter(function (f) { return f.properties.kind === 'sounding' && f.properties.id === id; })[0];
        return full || null;
      }
    }
    return null;
  }
  function onClick(_f, e) {
    if (!on) return;
    var h = hitHand(e);
    if (h) edit(h); else place(e.lngLat.lng, e.lngLat.lat, false);
  }
  function start() {
    if (!LSTools.claim('spoint')) return;
    on = true; LSTools.cursor.set('tool', 'crosshair');
    if (fp) fp.open(); else panel.classList.remove('hidden');
    // The soundings layer must exist for the points to show and be clicked: the host switches it on
    // (on /lidar/ it is not even loaded until its box is ticked -- why saved points did not appear).
    if (opts_.showSoundings) opts_.showSoundings(); else if (window.LSSoundings) LSSoundings.setVisible(true);
    drawLabels();
    if (!types.length) loadTypes();
    showForm(false);
  }
  function stop() {
    if (!on) return;
    on = false; LSTools.cursor.clear('tool'); LSTools.release('spoint');
    cur = null; clearMarker(); clearLabels();
    if (fp) fp.close(); else panel.classList.add('hidden');
  }

  var opts_ = {};
  function init(opts) {
    opts_ = opts; map = opts.map; api = opts.api || api; csrf = opts.csrf || csrf; surface = opts.surface || null;
    panel = opts.panel || $('sp-panel');
    if (!panel) return;
    if (window.LSFloat) fp = LSFloat.make(panel, { handle: panel.querySelector('.sp-head'), close: $('sp-x') });
    $('sp-x') && $('sp-x').addEventListener('click', function () { LSTools.release('spoint'); stop(); });
    $('sp-addtype').addEventListener('click', newType);
    $('sp-newok').addEventListener('click', addType);
    $('sp-newname').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); addType(); } });
    $('sp-level').addEventListener('input', function () { depthText(); });
    $('sp-bed').addEventListener('input', function () { depthText('bed'); });
    $('sp-depthin').addEventListener('input', function () { depthText('depth'); });
    ['sp-bed', 'sp-depthin'].forEach(function (id) {
      $(id).addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); save(); } });
    });
    $('sp-save').addEventListener('click', save);
    $('sp-del').addEventListener('click', del);
    $('sp-exit').addEventListener('click', function () { LSTools.release('spoint'); stop(); });
    LSTools.mode({
      id: 'spoint', order: 32, label: '⊕',
      title: 'Place estimated sounding points: click the map to place one, click a purple-ringed point to edit it (a profile drawn first stays open)',
      onSelect: start, onRelease: stop,
      cancel: function () { if (cur && !cur.id) { cur = null; clearMarker(); showForm(false); } else { LSTools.release('spoint'); stop(); } }
    });
    LSTools.clicks.register({ id: 'spoint', kind: 'action', holders: ['spoint'], handler: onClick });
    if (window.LSSoundings) LSSoundings.onChange(drawLabels);      // loaded, reloaded after a save or a delete
  }

  return { init: init, active: function () { return on; } };
})();
