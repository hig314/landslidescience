/* Terra Draw polygon editing for the review/edit preview map.
 *
 * Activated only on the manage edit/review forms, behind an "Edit geometry"
 * button. Lets an editor reshape existing polygons (drag / add / delete
 * vertices), add a new polygon (with a role), and delete a polygon, then Save
 * to `manage_polygons_save`. Reads two globals set by the page:
 *
 *   window.LSPolyMap  = { map, polygons, basemapSelectEl, showPreview(bool) }
 *                       (exposed by _polygon_map.html)
 *   window.LS_POLY_EDIT = { enabled, saveUrl, landslideType, csrftoken }
 *                       (set by manage_edit.html / manage_review.html)
 *
 * Round-trip safety: only polygons the editor actually changed are sent as
 * updates (tracked via Terra Draw 'change' events); untouched rows are never
 * resubmitted, and the server additionally skips no-op rewrites via ST_Equals.
 */
(function () {
  var cfg = window.LS_POLY_EDIT;
  var host = window.LSPolyMap;
  var CREATE = !!(cfg && cfg.mode === 'create');   // draw a brand-new landslide
  if (!cfg || (!cfg.enabled && !CREATE) || !host || !host.map) return;
  if (typeof terraDraw === 'undefined' || typeof terraDrawMaplibreGlAdapter === 'undefined') {
    console.error('terra_draw.js: Terra Draw library not loaded.');
    return;
  }

  var TD  = terraDraw;
  var ADP = terraDrawMaplibreGlAdapter;
  var map = host.map;
  // Default role for a freshly drawn polygon, from the landslide type. In edit
  // mode the type is fixed (cfg.landslideType); in create mode it follows the
  // type radio the editor picks.
  var createType = 'slow';
  function primaryRoleFor(type) { return type === 'catastrophic' ? 'source' : 'body'; }
  var primaryRole = primaryRoleFor(CREATE ? createType : cfg.landslideType);

  // ---- state (reset each time edit mode is entered) ----
  var draw, editing = false, selectedId = null;
  var uuidToDbId, featureRole, originalDbIds, dirtyDbIds;
  var _editShadow = [];   // basemap-switch-proof copy of the edited polygons

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  // MultiPolygon → array of Polygon geometries. A Polygon with >1 ring has a
  // hole. Our data is max 1 part / (almost) no holes; callers handle holes.
  function toPolygons(geom) {
    if (!geom) return [];
    if (geom.type === 'Polygon') return [geom];
    if (geom.type === 'MultiPolygon')
      return geom.coordinates.map(function (c) { return { type: 'Polygon', coordinates: c }; });
    return [];
  }
  function hasHole(geom) {
    return toPolygons(geom).some(function (p) { return p.coordinates.length > 1; });
  }

  // Terra Draw's select mode adds the draggable vertex + midpoint HANDLES to the
  // store as Point features (so getSnapshot() returns them alongside the real
  // polygons). Keep only the actual polygon features, or those handles get
  // mistaken for new polygons (the "long list" + lost edits on save).
  function realPolys(snap) {
    return (snap || []).filter(function (f) {
      var g = f && f.geometry;
      if (!g || (g.type !== 'Polygon' && g.type !== 'MultiPolygon')) return false;
      var p = f.properties || {};
      return !p.selectionPoint && !p.midPoint;
    });
  }

  // ---------------------------------------------------------------- toolbar
  var bar = document.createElement('div');
  bar.className = 'ls-poly-edit-bar';
  var mapEl = document.getElementById('ls-poly-map');
  if (mapEl && mapEl.parentNode) mapEl.parentNode.insertBefore(bar, mapEl.nextSibling);

  function btn(label, title) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = label; if (title) b.title = title;
    b.className = 'ls-poly-btn';
    return b;
  }
  function msg(text, kind) {
    var m = document.createElement('span');
    m.className = 'ls-poly-msg ls-poly-msg-' + (kind || 'info');
    m.textContent = text;
    return m;
  }

  function renderIdleBar() {
    bar.innerHTML = '';
    var edit = btn('✎ Edit geometry', 'Reshape, add, or delete polygons');
    edit.classList.add('ls-poly-btn-primary');
    edit.addEventListener('click', enterEdit);
    bar.appendChild(edit);
  }

  function renderEditBar() {
    bar.innerHTML = '';
    var add = btn('+ Add polygon', 'Draw a new polygon');
    add.addEventListener('click', function () { draw.setMode('polygon'); flashMode('Drawing — click to add vertices, double-click to finish'); });
    var sel = btn('✋ Select / reshape', 'Select a polygon to drag/add/delete its vertices');
    sel.addEventListener('click', function () { draw.setMode('select'); flashMode('Select a polygon, then drag vertices; click a midpoint to add, right-click a vertex to delete'); });
    var del = btn('🗑 Delete selected', 'Delete the selected polygon');
    del.addEventListener('click', deleteSelected);
    var save = btn('Save geometry', 'Persist changes'); save.classList.add('ls-poly-btn-primary');
    save.addEventListener('click', save_);
    var cancel = btn('Cancel', 'Discard changes');
    cancel.addEventListener('click', cancelEdit);
    [add, sel, del, save, cancel].forEach(function (b) { bar.appendChild(b); });
    var roles = document.createElement('div'); roles.className = 'ls-poly-roles'; roles.id = 'ls-poly-roles';
    bar.appendChild(roles);
    var status = document.createElement('div'); status.className = 'ls-poly-status'; status.id = 'ls-poly-status';
    bar.appendChild(status);
    renderNewPolyRoles();
  }

  function flashMode(text) {
    var s = document.getElementById('ls-poly-status');
    if (s) s.textContent = text;
  }

  // New (un-saved) polygons get a role <select>; defaults from landslide type.
  function renderNewPolyRoles() {
    var box = document.getElementById('ls-poly-roles');
    if (!box || !draw) return;
    box.innerHTML = '';
    var snap = realPolys(draw.getSnapshot());
    var n = 0;
    snap.forEach(function (f) {
      if (uuidToDbId[f.id] != null) return;       // existing row, role already known
      n += 1;
      if (featureRole[f.id] == null) featureRole[f.id] = primaryRole;
      var row = document.createElement('label'); row.className = 'ls-poly-role-row';
      row.appendChild(document.createTextNode('New polygon ' + n + ': '));
      var sel = document.createElement('select');
      ['source', 'body', 'deposit'].forEach(function (r) {
        var o = document.createElement('option'); o.value = r; o.textContent = r;
        if (r === featureRole[f.id]) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', function () { featureRole[f.id] = sel.value; });
      row.appendChild(sel);
      box.appendChild(row);
    });
    if (n === 0) box.appendChild(msg('Tip: pick imagery (incl. AHAP) before editing — the basemap is locked while editing.', 'info'));
  }

  // ---------------------------------------------------------------- edit mode
  function enterEdit() {
    // Refuse records containing a hole-bearing polygon (Terra Draw can't safely
    // round-trip interior rings); these are rare (1 in the DB).
    var holed = (host.polygons.features || []).some(function (f) { return hasHole(f.geometry); });
    if (holed) {
      bar.innerHTML = '';
      bar.appendChild(msg('This record has a polygon with a hole — geometry editing is disabled here; edit it via upload.', 'warn'));
      return;
    }

    editing = true;
    uuidToDbId = {}; featureRole = {}; dirtyDbIds = {}; originalDbIds = []; _editShadow = [];
    host.showPreview(false);

    makeDraw();
    loadFeatures();
    draw.setMode('select');
    snapshotEditState();
    // Switching basemaps mid-edit is allowed now — Terra Draw is rebuilt on the
    // new style (style.load) and the edited polygons restored from the shadow.
    map.on('style.load', reloadEditAfterStyle);
    renderEditBar();
  }

  function snapshotEditState() {
    if (!draw) return;
    _editShadow = realPolys(draw.getSnapshot()).map(function (f) {
      return { geometry: f.geometry,
               db_id: (uuidToDbId[f.id] != null ? uuidToDbId[f.id] : null),
               role: featureRole[f.id] || null };
    });
  }

  // A basemap switch does setStyle({diff:false}), wiping Terra Draw's layers.
  // Rebuild the session on the new style and restore the edited polygons from
  // the shadow (kept current on every change), preserving the db-id mapping +
  // dirty state so Save still diffs correctly.
  function reloadEditAfterStyle() {
    if (!editing) return;
    var dirty = dirtyDbIds, orig = originalDbIds;
    try { if (draw) draw.stop(); } catch (e) {}
    draw = null;
    makeDraw();
    uuidToDbId = {}; featureRole = {};
    var feats = [];
    _editShadow.forEach(function (m) {
      var id = uuid();
      feats.push({ id: id, type: 'Feature', geometry: m.geometry, properties: { mode: 'polygon' } });
      if (m.db_id != null) uuidToDbId[id] = m.db_id;
      if (m.role != null) featureRole[id] = m.role;
    });
    if (feats.length) draw.addFeatures(feats);
    dirtyDbIds = dirty; originalDbIds = orig;
    draw.setMode('select');
    // The switch re-adds the read-only preview layers (visible) on idle — hide
    // them again so they don't double-render under the editable copy.
    map.once('idle', function () { host.showPreview(false); });
  }

  // Build + start a Terra Draw session (select + polygon modes) on the map and
  // wire the shared change/select handlers. Used by both edit and create.
  function makeDraw() {
    draw = new TD.TerraDraw({
      adapter: new ADP.TerraDrawMapLibreGLAdapter({ map: map }),
      modes: [
        new TD.TerraDrawSelectMode({
          flags: { polygon: { feature: {
            draggable: true,
            coordinates: { midpoints: true, draggable: true, deletable: true }
          } } }
        }),
        new TD.TerraDrawPolygonMode({
          pointerDistance: 8,
          keyEvents: { finish: 'Enter', cancel: 'Escape' },
          validation: function (feature, ctx) {
            if (TD.ValidateNotSelfIntersecting &&
                (ctx.updateType === 'finish' || ctx.updateType === 'commit')) {
              return TD.ValidateNotSelfIntersecting(feature);
            }
            return { valid: true };
          }
        })
      ]
    });
    draw.start();
    draw.on('change', onChange);
    draw.on('finish', onFinish);
    draw.on('select', function (id) { selectedId = id; });
    draw.on('deselect', function () { selectedId = null; });
  }

  function loadFeatures() {
    var feats = [];
    (host.polygons.features || []).forEach(function (f) {
      var dbId = f.properties ? f.properties.db_id : null;
      var role = f.properties ? f.properties.role : null;
      // Our data is single-part; take the first part defensively.
      var polys = toPolygons(f.geometry);
      if (!polys.length) return;
      var id = uuid();
      feats.push({ id: id, type: 'Feature', geometry: polys[0], properties: { mode: 'polygon' } });
      if (dbId != null) { uuidToDbId[id] = dbId; originalDbIds.push(dbId); }
      if (role != null) featureRole[id] = role;
    });
    if (feats.length) draw.addFeatures(feats);
  }

  function onChange(ids, type) {
    if (type === 'styling') return;
    (ids || []).forEach(function (id) {
      if (uuidToDbId[id] != null) dirtyDbIds[uuidToDbId[id]] = true;
    });
    snapshotEditState();   // keep the basemap-switch shadow current
    if (type === 'delete') renderNewPolyRoles();
  }

  // `change`/'create' fires on the first click; a polygon is only complete on
  // `finish` (double-click / closing the ring). For a freshly drawn polygon,
  // drop back to select so the editor can reshape/assign a role; leave the mode
  // alone when finishing an edit of an existing polygon.
  function onFinish(id) {
    if (uuidToDbId[id] == null) draw.setMode('select');
    renderNewPolyRoles();
  }

  function deleteSelected() {
    if (!selectedId) { flashMode('Select a polygon first (Select / reshape), then Delete.'); return; }
    draw.removeFeatures([selectedId]);
    selectedId = null;
    renderNewPolyRoles();
  }

  function teardown() {
    try { map.off('style.load', reloadEditAfterStyle); } catch (e) {}
    if (draw) { try { draw.stop(); } catch (e) {} draw = null; }
    editing = false; selectedId = null;
    host.showPreview(true);
  }

  function cancelEdit() {
    teardown();
    renderIdleBar();
  }

  function save_() {
    if (!draw) return;
    var snap = realPolys(draw.getSnapshot());
    var present = {}, updates = [], inserts = [];
    snap.forEach(function (f) {
      var dbId = uuidToDbId[f.id];
      if (dbId != null) {
        present[dbId] = true;
        if (dirtyDbIds[dbId]) updates.push({ db_id: dbId, geometry: f.geometry });
      } else {
        inserts.push({ role: featureRole[f.id] || primaryRole, geometry: f.geometry });
      }
    });
    var deletes = originalDbIds.filter(function (d) { return !present[d]; });

    if (!updates.length && !inserts.length && !deletes.length) {
      flashMode('No geometry changes to save.');
      return;
    }
    flashMode('Saving…');
    fetch(cfg.saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrftoken },
      body: JSON.stringify({ updates: updates, inserts: inserts, deletes: deletes })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) {
          // Update geometry-derived fields + reconcile the edit session in place
          // — NO page reload, so unsaved field edits (e.g. description) survive.
          applyDerivedToForm(res.j.derived);
          reloadFromServer(res.j.polygons);
          flashMode('Geometry saved ✓ — areas/centroid updated. Remember to Save the form to keep field edits.');
        } else {
          flashMode('Save failed: ' + (res.j && res.j.error ? res.j.error : 'unknown error'));
        }
      }).catch(function (e) { flashMode('Save failed: ' + e); });
  }

  // Push the server-recomputed derived columns into their form inputs, so the
  // form reflects the new geometry without a reload (and a later form Save
  // writes the fresh values rather than stale ones).
  function applyDerivedToForm(derived) {
    if (!derived) return;
    Object.keys(derived).forEach(function (k) {
      var el = document.querySelector('[name="' + k + '"]');
      if (!el) return;
      var v = derived[k];
      if (el.type === 'checkbox') el.checked = !!v;
      else el.value = (v == null ? '' : v);
    });
  }

  // Re-sync the edit session (and the read-only preview) with the saved polygons
  // returned by the server: fresh db-ids, cleared dirty/inserts/deletes.
  function reloadFromServer(polysFC) {
    if (!polysFC) return;
    host.polygons = polysFC;
    if (map.getSource('lspoly')) map.getSource('lspoly').setData(polysFC);
    if (!draw) return;
    try { draw.clear(); } catch (e) {}
    uuidToDbId = {}; featureRole = {}; dirtyDbIds = {}; originalDbIds = [];
    loadFeatures();
    snapshotEditState();
    draw.setMode('select');
    renderNewPolyRoles();
  }

  // ---------------------------------------------------------------- create mode
  // Draw a brand-new landslide: name + type + one or more polygons (each with a
  // role), POSTed to manage_new. Terra Draw isn't started until the first "Add
  // polygon" click, so the editor can choose imagery (AHAP) and zoom to the
  // site first — the basemap locks only once drawing begins (a setStyle would
  // otherwise wipe the in-progress geometry).
  var nameInput;

  function ensureDrawing() {
    if (!draw) {
      uuidToDbId = uuidToDbId || {}; featureRole = featureRole || {};
      makeDraw();
      if (host.basemapSelectEl) host.basemapSelectEl.disabled = true;
    }
  }

  function renderCreateBar() {
    bar.innerHTML = '';
    uuidToDbId = {}; featureRole = {}; dirtyDbIds = {}; originalDbIds = [];

    var nameWrap = document.createElement('label'); nameWrap.className = 'ls-poly-role-row';
    nameWrap.appendChild(document.createTextNode('Name: '));
    nameInput = document.createElement('input');
    nameInput.type = 'text'; nameInput.placeholder = 'unique_name'; nameInput.style.fontSize = '12px';
    nameWrap.appendChild(nameInput);

    var typeWrap = document.createElement('span'); typeWrap.className = 'ls-poly-role-row';
    typeWrap.appendChild(document.createTextNode('Type: '));
    ['slow', 'catastrophic'].forEach(function (t) {
      var lbl = document.createElement('label'); lbl.style.marginRight = '8px';
      var rb = document.createElement('input'); rb.type = 'radio'; rb.name = 'ls-new-type'; rb.value = t;
      if (t === createType) rb.checked = true;
      rb.addEventListener('change', function () {
        if (rb.checked) { createType = t; primaryRole = primaryRoleFor(t); }
      });
      lbl.appendChild(rb); lbl.appendChild(document.createTextNode(' ' + t));
      typeWrap.appendChild(lbl);
    });

    var add = btn('+ Add polygon', 'Draw a new polygon');
    add.addEventListener('click', function () { ensureDrawing(); draw.setMode('polygon'); flashMode('Drawing — click to add vertices, double-click to finish'); });
    var sel = btn('✋ Select / reshape', 'Select a polygon to drag/add/delete its vertices');
    sel.addEventListener('click', function () { if (draw) { draw.setMode('select'); flashMode('Drag vertices; click a midpoint to add, right-click a vertex to delete'); } });
    var del = btn('🗑 Delete selected', 'Delete the selected polygon');
    del.addEventListener('click', deleteSelected);
    var create = btn('Create landslide', 'Insert as a pending record and open review');
    create.classList.add('ls-poly-btn-primary');
    create.addEventListener('click', saveCreate);

    [nameWrap, typeWrap, add, sel, del, create].forEach(function (el) { bar.appendChild(el); });
    var roles = document.createElement('div'); roles.className = 'ls-poly-roles'; roles.id = 'ls-poly-roles';
    bar.appendChild(roles);
    var status = document.createElement('div'); status.className = 'ls-poly-status'; status.id = 'ls-poly-status';
    bar.appendChild(status);
    flashMode('Pick imagery + zoom to the site, then "+ Add polygon" to start drawing.');
  }

  function saveCreate() {
    var name = (nameInput && nameInput.value || '').trim();
    if (!name) { flashMode('Enter a unique name.'); return; }
    if (!draw) { flashMode('Draw at least one polygon first.'); return; }
    var snap = realPolys(draw.getSnapshot());
    if (!snap.length) { flashMode('Draw at least one polygon first.'); return; }
    var polygons = snap.map(function (f) {
      return { role: featureRole[f.id] || primaryRole, geometry: f.geometry };
    });
    flashMode('Creating…');
    fetch(cfg.createUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrftoken },
      body: JSON.stringify({ unique_name: name, landslide_type: createType, polygons: polygons })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) {
          window.location.href = res.j.redirect;
        } else {
          flashMode('Create failed: ' + (res.j && res.j.error ? res.j.error : 'unknown error'));
        }
      }).catch(function (e) { flashMode('Create failed: ' + e); });
  }

  if (CREATE) { renderCreateBar(); } else { renderIdleBar(); }
})();
