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

  // PostGIS stores these in a MULTIPOLYGON column -- all 1825 of them, every
  // one with a single part -- while Terra Draw handles Point, LineString and
  // Polygon only. So unwrap on the way in. Nothing is needed on the way out:
  // the save endpoint already runs ST_Multi(ST_CollectionExtract(...)) and
  // promotes a plain Polygon back to the column's type.
  //
  // The two shapes Terra Draw cannot represent are refused HERE, by name,
  // rather than left to its generic "Feature is not Point, LineString or
  // Polygon". Editing part one of a multi-part outline would silently discard
  // the rest, and it rejects holes outright ("Feature has holes") -- one row
  // in the inventory has an interior ring.
  function editableGeometry(g) {
    if (!g) return { why: 'has no geometry' };
    if (g.type === 'MultiPolygon') {
      if (g.coordinates.length !== 1) {
        return { why: 'is in ' + g.coordinates.length + ' separate parts' };
      }
      g = { type: 'Polygon', coordinates: g.coordinates[0] };
    }
    if (g.type !== 'Polygon') return { why: 'is a ' + g.type };
    if (g.coordinates.length > 1) {
      return { why: 'has ' + (g.coordinates.length - 1) + ' hole(s)' };
    }
    return { geometry: g };
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

    if (opts.id == null || isNaN(+opts.id)) {
      return Promise.reject(new Error('no landslide id was given'));
    }
    var url = api + 'api/landslide/' + opts.id + '/polygons/';
    return fetch(url)
      .then(function (r) {
        // Anything that is not JSON here is a page, not an answer: a login
        // redirect, a 404, an error template. Parsing it blows up on the '<'
        // of <!DOCTYPE and reports a JSON syntax error, which says nothing
        // about what actually happened -- say what came back instead.
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') === -1) {
          throw new Error('server returned ' + r.status + ' ' +
                          (ct.split(';')[0] || 'an unknown type') +
                          ' for ' + url +
                          (r.status === 404 ? ' — no such record?'
                           : r.redirected ? ' — signed out?' : ''));
        }
        if (r.status === 403) throw new Error('not permitted');
        return r.json();
      })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || 'could not load polygons');
        var feats = d.polygons.features || [];
        if (!feats.length) throw new Error('this record has no polygons to revise');

        active = { id: opts.id, name: d.unique_name, original: {}, roles: {} };
        deleted = {};
        var editable = [], skipped = [];
        feats.forEach(function (f) {
          var r = editableGeometry(f.geometry);
          if (r.why) {
            skipped.push((f.properties.role || 'polygon') + ' ' + r.why);
            return;      // left out of `original`, so a save can never touch it
          }
          active.original[f.properties.db_id] = r.geometry;
          active.roles[f.properties.db_id] = f.properties.role;
          editable.push({ type: 'Feature', geometry: r.geometry,
                          properties: { db_id: f.properties.db_id,
                                        role: f.properties.role } });
        });
        if (!editable.length) {
          throw new Error('none of this record\'s polygons can be edited here — ' +
                          skipped.join('; '));
        }

        td = new TD.TerraDraw({
          adapter: new ADAPT.TerraDrawMapLibreGLAdapter({ map: map }),
          // BOTH modes are registered, and only 'select' is ever active.
          //
          // Terra Draw validates every added feature against the instantiated
          // modes: a feature whose properties.mode is 'polygon' is rejected
          // outright -- "polygon mode is not in the list of instantiated
          // modes" -- when only select is registered. Rejected features do not
          // render and cannot be selected, so the map keeps its own handlers
          // and the tool looks like it never opened, apart from a flash as the
          // features are added and dropped. Registering polygon mode makes
          // them valid; never switching to it keeps this from becoming a
          // second way to draw.
          modes: [
            new TD.TerraDrawPolygonMode(),
            new TD.TerraDrawSelectMode({
              flags: {
                polygon: {
                  feature: {
                    // Dragging a whole outline is almost never what you want
                    // here and is easy to do by accident while reaching for a
                    // vertex -- it would silently move a mapped feature off
                    // the ground it was mapped from. Vertices only.
                    draggable: false,
                    rotateable: false,
                    scaleable: false,
                    coordinates: {
                      midpoints: true,   // click a midpoint to add a vertex
                      draggable: true,
                      deletable: true    // right-click removes one
                    }
                  }
                }
              }
            })
          ]
        });
        td.start();
        td.setMode('select');
        // addFeatures reports per-feature validity rather than throwing. Say
        // so loudly: a silent rejection here is exactly the failure that looks
        // like the tool simply not working.
        var res = td.addFeatures(toTDFeatures({ features: editable })) || [];
        var bad = res.filter(function (r) { return r && r.valid === false; });
        if (bad.length) {
          throw new Error('Terra Draw rejected ' + bad.length + ' of ' +
                          editable.length + ' polygons: ' +
                          (bad[0].reason || 'no reason given'));
        }
        map.doubleClickZoom.disable();
        return { name: d.unique_name, count: editable.length,
                 skipped: skipped, roles: active.roles };
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
