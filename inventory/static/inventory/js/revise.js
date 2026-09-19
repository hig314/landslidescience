/* revise.js — edit an existing landslide's polygons on the LIVE map.
 *
 * WHY THIS EXISTS
 * ---------------
 * Outlines could only be revised in a 360 px mini-map inside the edit form.
 * Everything that makes an outline decidable lives on the live map instead:
 * the basemap switcher, the wiper, lidar hillshade and slope, the Sentinel-2
 * scene the feature was spotted in, QMS layers. Drawing a NEW polygon already
 * happens out here; revising an existing one had to happen in the small box.
 *
 * WHAT IT DOES NOT DO, ON PURPOSE
 * -------------------------------
 * Adding a component is still the draw tool's job. Drawing stages server-side
 * and asks for a name and role so components can be grouped into a landslide;
 * duplicating that here would mean two ways to create the same thing, and the
 * second one would be the one that drifts. This edits what is already there:
 * geometry, role, and deletion.
 *
 * HOW IT SAVES
 * ------------
 * Through the same manage/<id>/polygons/ endpoint the form uses, which already
 * snapshots to history, runs the whole write in one transaction, recomputes
 * area/centroid/size/class through the rule cascade, audits the edit and
 * invalidates caches. Nothing auto-saves: you press Save, or you press Revert
 * and the map reloads what the database holds.
 */
window.LSRevise = (function () {
  'use strict';

  var map = null, td = null, api = '/inventory/', csrf = null;
  var active = null;      // {id, name, original: {db_id: geometryJSON}, roles: {db_id: role}}
  var onExit = null, flash = null;
  var deleted = {};       // db_id -> true

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    });
  }

  // Terra Draw identifies features by its own id; we need the db id. Keep the
  // mapping on the feature's properties, which survive a round trip through
  // getSnapshot().
  function toTDFeatures(fc) {
    return (fc.features || []).map(function (f) {
      return {
        id: undefined,
        type: 'Feature',
        geometry: f.geometry,
        properties: { mode: 'polygon', ls_db_id: f.properties.db_id,
                      ls_role: f.properties.role }
      };
    });
  }

  function sameGeometry(a, b) {
    // Coordinate-wise compare after rounding: Terra Draw round-trips floats,
    // and an untouched polygon must not be written back as an "update" --
    // that would snapshot a history row and re-run the rule cascade for a
    // no-op edit on every save.
    try {
      return JSON.stringify(round(a)) === JSON.stringify(round(b));
    } catch (e) { return false; }
  }
  function round(g) {
    return JSON.parse(JSON.stringify(g, function (k, v) {
      return typeof v === 'number' ? +v.toFixed(9) : v;
    }));
  }

  function snapshotByDbId() {
    var out = {};
    (td ? td.getSnapshot() : []).forEach(function (f) {
      var id = f.properties && f.properties.ls_db_id;
      if (id != null) out[id] = f;
    });
    return out;
  }

  /** What would be written, without writing it. */
  function diff() {
    var now = snapshotByDbId(), updates = [], deletes = [];
    Object.keys(active.original).forEach(function (dbId) {
      if (deleted[dbId]) { deletes.push(+dbId); return; }
      var f = now[dbId];
      if (!f) return;                      // still loaded, just not in the snapshot
      if (!sameGeometry(f.geometry, active.original[dbId])) {
        updates.push({ db_id: +dbId, geometry: f.geometry });
      }
    });
    return { updates: updates, deletes: deletes };
  }

  function start(opts) {
    map = opts.map; api = opts.api || api; csrf = opts.csrf;
    onExit = opts.onExit || function () {};
    flash = opts.flash || function () {};
    var TD = opts.terraDraw, ADAPT = opts.adapter;

    return fetch(api + 'api/landslide/' + opts.id + '/polygons/')
      .then(function (r) {
        if (r.status === 403 || r.status === 302) throw new Error('not signed in');
        return r.json();
      })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || 'could not load polygons');
        var feats = d.polygons.features || [];
        if (!feats.length) throw new Error('this record has no polygons to revise');

        active = { id: opts.id, name: d.unique_name, original: {}, roles: {} };
        deleted = {};
        feats.forEach(function (f) {
          active.original[f.properties.db_id] = f.geometry;
          active.roles[f.properties.db_id] = f.properties.role;
        });

        td = new TD.TerraDraw({
          adapter: new ADAPT.TerraDrawMapLibreGLAdapter({ map: map }),
          // Select mode only. No polygon mode here: creating a component is
          // the draw tool's job, and offering it twice invites the two paths
          // to diverge.
          modes: [new TD.TerraDrawSelectMode({
            flags: {
              polygon: {
                feature: {
                  draggable: true,
                  rotateable: false,
                  scaleable: false,
                  coordinates: {
                    midpoints: true,     // click a midpoint to add a vertex
                    draggable: true,
                    deletable: true      // right-click / delete removes one
                  }
                }
              }
            }
          })]
        });
        td.start();
        td.setMode('select');
        td.addFeatures(toTDFeatures(d.polygons));
        map.doubleClickZoom.disable();
        return { name: d.unique_name, count: feats.length,
                 roles: active.roles };
      });
  }

  function markDeleted(dbId, yes) { deleted[dbId] = !!yes; }
  function isDeleted(dbId) { return !!deleted[dbId]; }

  function save() {
    if (!active) return Promise.reject(new Error('nothing being revised'));
    var d = diff();
    if (!d.updates.length && !d.deletes.length) {
      return Promise.resolve({ nothing: true });
    }
    return post(api + 'manage/' + active.id + '/polygons/', d).then(function (res) {
      if (!(res.ok && res.j && res.j.ok)) {
        throw new Error((res.j && res.j.error) || 'save failed');
      }
      // The saved geometry is the new baseline, so a second Save with no
      // further edits is correctly a no-op rather than a repeat write.
      var now = snapshotByDbId();
      d.updates.forEach(function (u) {
        if (now[u.db_id]) active.original[u.db_id] = now[u.db_id].geometry;
      });
      d.deletes.forEach(function (id) { delete active.original[id]; });
      deleted = {};
      return res.j;
    });
  }

  function stop() {
    if (td) { try { td.stop(); } catch (e) {} td = null; }
    if (map) map.doubleClickZoom.enable();
    active = null; deleted = {};
    onExit();
  }

  return {
    start: start, stop: stop, save: save, diff: diff,
    markDeleted: markDeleted, isDeleted: isDeleted,
    current: function () { return active; },
    isActive: function () { return !!active; }
  };
})();
