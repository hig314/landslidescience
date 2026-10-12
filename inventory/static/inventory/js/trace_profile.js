/* trace_profile.js — the group profile: every trace in a group, sampled for
 * elevation and projected onto one vertical plane. Global `window.LSTraceProfile`.
 *
 * Built 2026-10-11 for Pleistocene moraine crests (Hig): lateral moraines on
 * both sides of a valley, projected onto one plane along it, are how a
 * former ice surface is read. Works for any group (a fault's scarps, a lake's
 * strandlines).
 *
 * THE PLANE is vertical, through the direction of greatest horizontal
 * extent: the line through the two vertices that are farthest apart (Hig:
 * "the one that spreads the members over the greatest horizontal
 * distance"). It runs down the middle of the band the members occupy and
 * spans exactly their projected extent, A to A'. Shown on the map while the
 * panel is open; Flip swaps A and A'. DRAG A OR A' to set the plane by
 * hand (Hig, 2026-10-11): the plane is then the line through the two ends
 * as placed, spanning them and every member's projection; *Auto plane* goes
 * back to the farthest pair. Re-placing the plane only re-projects the
 * samples already read -- no elevation is read again. Distances are in a local
 * equirectangular frame about the members' centre (metres; exact enough
 * over tens of km).
 *
 * A GROUP IS ONE SERIES: one colour, one line of the legend, one row of the
 * window table, however many traces it holds (Hig, 2026-10-11). The code
 * takes a list of groups so that several can later be projected onto one
 * plane in distinct colours; the plane is then built from all their members.
 * Today the page opens one. While the panel is open, a trace added to,
 * edited in or removed from an open group re-reads the profile (the host
 * calls notify() on every change to the traces; only the open groups'
 * members are compared).
 *
 * ELEVATIONS: each trace is sampled along its length and each sample is read
 * from the first surface that has data there, in this order: the surveys the
 * trace was traced on (traced_on), the surveys the host says are on screen,
 * any other catalogue survey covering it (finest first), then Mapterhorn.
 * Readers are dem_read.js's. The CSV names the surface of every sample.
 *
 * The panel, chart, hover, brushed window and downloads are
 * profile_chart.js's -- the same instrument as the ∿ line profile on
 * /inventory/.
 *
 *   LSTraceProfile.init({map, catalog, preferred, traces, groupById, onChange})
 *   LSTraceProfile.open(groupId | [ids]) / close() / notify() / state()
 *   LSTraceProfile.setState({groups, flip, locked, sel, plane: {A, B} | null})
 */
window.LSTraceProfile = (function () {
  'use strict';

  var PALETTE = ['#d81b60', '#1e88e5', '#43a047', '#fb8c00', '#8e24aa', '#00acc1',
                 '#6d4c41', '#3949ab', '#e53935', '#00897b'];
  var PLANE = 'tgp-plane', LINK = 'tgp-link', SRC = 'tgp-src', CURSOR = 'tgp-cursor';
  var map = null, opts = {}, chart = null;
  var groups = [], flip = false;
  var custom = null;            // {A, B} [lon, lat] when the plane's ends were dragged; null = automatic
  var sig = '', notifyTimer = null, dragRaf = null;
  var plane = null;             // {fr, p1, u, n, smin, smax, tmean, A, B, az, len}
  var result = null;            // {series: [{group, color, runs: [[point…]…]}], pending}
  var seq = 0;
  var markers = [];

  // ---- geometry ----------------------------------------------------------
  function frame(coords) {
    var la = 0, lo = 0;
    coords.forEach(function (c) { lo += c[0]; la += c[1]; });
    lo /= coords.length; la /= coords.length;
    return { lon0: lo, lat0: la, kx: 111320 * Math.cos(la * Math.PI / 180), ky: 110574 };
  }
  function toXY(f, c) { return [(c[0] - f.lon0) * f.kx, (c[1] - f.lat0) * f.ky]; }
  function toLL(f, p) { return [f.lon0 + p[0] / f.kx, f.lat0 + p[1] / f.ky]; }
  function hull(pts) {                       // monotone chain
    var P = pts.slice().sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
    if (P.length < 3) return P;
    function cross(o, a, b) { return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]); }
    var lo = [], up = [];
    P.forEach(function (p) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); });
    for (var i = P.length - 1; i >= 0; i--) {
      var p = P[i];
      while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop();
      up.push(p);
    }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }
  function partsOf(g) { return g.type === 'MultiLineString' ? g.coordinates : [g.coordinates]; }
  function memberXY(members) {
    var all = [];
    members.forEach(function (f) { partsOf(f.geometry).forEach(function (p) { all = all.concat(p); }); });
    if (all.length < 2) return null;
    var fr = frame(all);
    return { fr: fr, xy: all.map(function (c) { return toXY(fr, c); }) };
  }
  // The automatic plane: through the farthest pair of vertices, down the
  // middle of the band the members occupy. With `through` ({A, B}, dragged
  // by hand) the plane is the line through those two points instead, and
  // spans them as well as the members.
  function buildPlane(members, through) {
    var mx = memberXY(members);
    if (!mx) return null;
    var fr = mx.fr, xy = mx.xy, a, u, L;
    if (through) {
      a = toXY(fr, through.A);
      var b = toXY(fr, through.B);
      L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (L < 1) return null;
      u = [(b[0] - a[0]) / L, (b[1] - a[1]) / L];
    } else {
      var H = hull(xy), best = -1, p = null, q = null;
      for (var i = 0; i < H.length; i++) for (var j = i + 1; j < H.length; j++) {
        var dx = H[j][0] - H[i][0], dy = H[j][1] - H[i][1], d = dx * dx + dy * dy;
        if (d > best) { best = d; p = H[i]; q = H[j]; }
      }
      if (!p || best <= 0) return null;
      L = Math.sqrt(best);
      a = p; u = [(q[0] - p[0]) / L, (q[1] - p[1]) / L];
      if (flip) u = [-u[0], -u[1]];
    }
    var n = [-u[1], u[0]];
    var smin = Infinity, smax = -Infinity, tmin = Infinity, tmax = -Infinity;
    xy.forEach(function (pt) {
      var s = (pt[0] - a[0]) * u[0] + (pt[1] - a[1]) * u[1];
      var t = (pt[0] - a[0]) * n[0] + (pt[1] - a[1]) * n[1];
      if (s < smin) smin = s; if (s > smax) smax = s;
      if (t < tmin) tmin = t; if (t > tmax) tmax = t;
    });
    var tmean;
    if (through) {
      // Through the dragged ends exactly, spanning them and the members.
      smin = Math.min(smin, 0); smax = Math.max(smax, L); tmean = 0;
    } else {
      // Down the middle of the band the traces occupy. (A mean of vertex
      // offsets was tried first: it leans toward whichever trace happens to
      // have more vertices, which says nothing about the ground.)
      tmean = (tmin + tmax) / 2;
    }
    function at(s) { return toLL(fr, [a[0] + u[0] * s + n[0] * tmean, a[1] + u[1] * s + n[1] * tmean]); }
    var az = (Math.atan2(u[0], u[1]) * 180 / Math.PI + 360) % 360;
    return { fr: fr, p1: a, u: u, n: n, smin: smin, smax: smax, tmean: tmean,
             A: at(smin), B: at(smax), az: az, len: smax - smin, custom: !!through };
  }
  function project(pl, c) {
    var p = toXY(pl.fr, c);
    var s = (p[0] - pl.p1[0]) * pl.u[0] + (p[1] - pl.p1[1]) * pl.u[1];
    var t = (p[0] - pl.p1[0]) * pl.n[0] + (p[1] - pl.p1[1]) * pl.n[1];
    return { s: s - pl.smin, t: t - pl.tmean };
  }
  // Point in a catalogue footprint (Polygon / MultiPolygon, holes respected).
  function inRing(pt, ring) {
    var x = pt[0], y = pt[1], inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }
  function inFootprint(pt, g) {
    var polys = g.type === 'MultiPolygon' ? g.coordinates : g.type === 'Polygon' ? [g.coordinates] : [];
    return polys.some(function (poly) {
      if (!inRing(pt, poly[0])) return false;
      for (var k = 1; k < poly.length; k++) if (inRing(pt, poly[k])) return false;
      return true;
    });
  }

  // ---- sampling ----------------------------------------------------------
  function densify(pl, part, step) {
    var out = [];
    for (var i = 0; i < part.length; i++) {
      if (i === 0) { out.push(part[0]); continue; }
      var a = toXY(pl.fr, part[i - 1]), b = toXY(pl.fr, part[i]);
      var d = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.ceil(d / step));
      for (var k = 1; k <= n; k++) out.push(toLL(pl.fr, [a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n]));
    }
    return out;
  }
  function lengthOf(pl, f) {
    var L = 0;
    partsOf(f.geometry).forEach(function (part) {
      for (var i = 1; i < part.length; i++) {
        var a = toXY(pl.fr, part[i - 1]), b = toXY(pl.fr, part[i]);
        L += Math.hypot(b[0] - a[0], b[1] - a[1]);
      }
    });
    return L;
  }
  function candidates(f) {
    var cat = opts.catalog() || { features: [] }, b = bboxOf(f);
    var want = ((f.properties.traced_on && f.properties.traced_on.surveys) || []).concat(opts.preferred() || []);
    var c = cat.features.filter(function (x) {
      var bb = x.properties.bounds;
      return x.properties.pmtiles_url && bb && !(bb[0] > b[2] || bb[2] < b[0] || bb[1] > b[3] || bb[3] < b[1]);
    });
    c.sort(function (x, y) {
      var ix = want.indexOf(x.properties.id), iy = want.indexOf(y.properties.id);
      ix = ix < 0 ? 999 : ix; iy = iy < 0 ? 999 : iy;
      return ix - iy || (x.properties.native_res_m || 99) - (y.properties.native_res_m || 99);
    });
    return c;
  }
  function bboxOf(f) {
    var b = [180, 90, -180, -90];
    partsOf(f.geometry).forEach(function (p) { p.forEach(function (c) {
      if (c[0] < b[0]) b[0] = c[0]; if (c[1] < b[1]) b[1] = c[1];
      if (c[0] > b[2]) b[2] = c[0]; if (c[1] > b[3]) b[3] = c[1];
    }); });
    return b;
  }
  // First surface with data at the point, in candidate order, else Mapterhorn.
  function readPoint(cands, c, step) {
    var i = 0;
    function next() {
      while (i < cands.length && !inFootprint(c, cands[i].f.geometry)) i++;
      if (i >= cands.length) {
        var M = LSDemRead.MAPTERHORN;
        return LSDemRead.readAt(M, LSDemRead.zoomFor(M, c[1], step), c[0], c[1])
          .then(function (z) { return { z: z, dem: isNaN(z) ? '' : M.id }; });
      }
      var s = cands[i++].src;
      return LSDemRead.readAt(s, LSDemRead.zoomFor(s, c[1], step), c[0], c[1])
        .then(function (z) { return isNaN(z) ? next() : { z: z, dem: s.id }; });
    }
    return next();
  }
  function traceLabel(f) {
    var p = f.properties;
    return '#' + p.id + ' ' + (p.kind || 'scarp') + (p.notes ? ' — ' + p.notes.split('\n')[0].slice(0, 40) : '');
  }
  // One series per group; a run per trace part, its samples in trace order.
  function sample(sets, mySeq) {
    var series = [], reads = [];
    sets.forEach(function (gs, gi) {
      var se = { group: gs.group, color: PALETTE[gi % PALETTE.length], runs: [], traces: gs.members.length };
      series.push(se);
      gs.members.forEach(function (f) {
        var L = lengthOf(plane, f);
        var step = Math.max(1, Math.min(20, L / 300));
        var cands = candidates(f).map(function (x) { return { f: x, src: LSDemRead.surveySource(x.properties) }; });
        partsOf(f.geometry).forEach(function (part, pi) {
          var run = [];
          densify(plane, part, step).forEach(function (c) {
            var pr = project(plane, c);
            var q = { d: pr.s, t: pr.t, lon: c[0], lat: c[1], z: NaN, dem: '', part: pi, trace: f };
            run.push(q);
            reads.push(readPoint(cands, c, step).then(function (r) { q.z = r.z; q.dem = r.dem; }));
          });
          se.runs.push(run);
        });
      });
    });
    result = { series: series, pending: true };
    draw();
    chart.setStatus('Reading elevations for ' + reads.length + ' samples…');
    Promise.all(reads).then(function () {
      if (mySeq !== seq) return;
      result.pending = false;
      draw();
    });
  }

  // ---- map ---------------------------------------------------------------
  function fc(feats) { return { type: 'FeatureCollection', features: feats || [] }; }
  function ensureLayers() {
    if (map.getSource(SRC)) return;
    map.addSource(SRC, { type: 'geojson', data: fc() });
    map.addSource(CURSOR, { type: 'geojson', data: fc() });
    map.addLayer({ id: PLANE + '-case', type: 'line', source: SRC, filter: ['==', ['get', 'role'], 'plane'],
                   paint: { 'line-color': '#fff', 'line-width': 5, 'line-opacity': 0.85 } });
    map.addLayer({ id: PLANE, type: 'line', source: SRC, filter: ['==', ['get', 'role'], 'plane'],
                   paint: { 'line-color': '#111', 'line-width': 2, 'line-dasharray': [4, 2] } });
    map.addLayer({ id: LINK, type: 'line', source: CURSOR, filter: ['==', ['geometry-type'], 'LineString'],
                   paint: { 'line-color': '#111', 'line-width': 1.2, 'line-dasharray': [1, 1.5] } });
    // The hover marker: the same yellow dot as the inventory's line profile.
    map.addLayer({ id: CURSOR + '-pt', type: 'circle', source: CURSOR, filter: ['==', ['geometry-type'], 'Point'],
                   paint: LSProfileChart.CURSOR_PAINT });
  }
  function setPlane() {
    ensureLayers();
    map.getSource(SRC).setData(fc(plane ? [{ type: 'Feature', properties: { role: 'plane' },
                                            geometry: { type: 'LineString', coordinates: [plane.A, plane.B] } }] : []));
  }
  function mk(text) {
    var d = document.createElement('div');
    d.className = 'tgp-mark';
    d.textContent = text;
    return d;
  }
  // A and A' are draggable: dragging re-places the plane live (the samples
  // are re-projected, not re-read), and on release both ends settle where
  // the new plane's span puts them.
  function placeMarkers() {
    markers.forEach(function (m) { m.remove(); });
    markers = [];
    if (!plane) return;
    [['A', plane.A], ['A′', plane.B]].forEach(function (e, i) {
      var m = new maplibregl.Marker({ element: mk(e[0]), draggable: true }).setLngLat(e[1]).addTo(map);
      m.getElement().title = 'Drag to set the plane by hand';
      m.on('drag', function () {
        if (dragRaf) return;
        dragRaf = requestAnimationFrame(function () { dragRaf = null; dragTo(i, m.getLngLat()); });
      });
      m.on('dragend', function () {
        if (dragRaf) { cancelAnimationFrame(dragRaf); dragRaf = null; }
        dragTo(i, m.getLngLat());
        placeMarkers();
        opts.onChange();
      });
      markers.push(m);
    });
  }
  function dragTo(i, ll) {
    var A = markers[0].getLngLat(), B = markers[1].getLngLat();
    custom = { A: [A.lng, A.lat], B: [B.lng, B.lat] };
    custom[i ? 'B' : 'A'] = [ll.lng, ll.lat];
    replane();
  }
  // Rebuild the plane (automatic or through the dragged ends) and project the
  // samples already read onto it: no elevation is read again.
  function replane() {
    var pl = buildPlane(members(), custom);
    if (!pl) return;
    plane = pl;
    if (result) result.series.forEach(function (se) { se.runs.forEach(function (run) { run.forEach(function (q) {
      var pr = project(plane, [q.lon, q.lat]); q.d = pr.s; q.t = pr.t;
    }); }); });
    setPlane();
    autoBtn();
    draw();
  }
  function autoBtn() {
    var b = chart && chart.el.title.parentNode.querySelector('.tgp-auto');
    if (b) b.style.display = custom ? '' : 'none';
  }
  function members() {
    var all = (opts.traces().features || []);
    return all.filter(function (f) { return groups.some(function (g) { return g.id === f.properties.group_id; }); });
  }
  // A fingerprint of the open groups' members, to tell a real change (a line
  // added, moved, deleted, regrouped) from the redraws a selection makes.
  function signature() {
    return JSON.stringify(members().map(function (f) { return [f.properties.id, f.properties.group_id, f.geometry.coordinates]; }));
  }
  // The hovered sample, and a dotted line to its foot on the plane: what the
  // chart's x is measured from.
  function setCursor(q) {
    if (!map.getSource(CURSOR)) return;
    if (!q || !plane) { map.getSource(CURSOR).setData(fc()); return; }
    var s = q.d + plane.smin;
    var foot = toLL(plane.fr, [plane.p1[0] + plane.u[0] * s + plane.n[0] * plane.tmean,
                               plane.p1[1] + plane.u[1] * s + plane.n[1] * plane.tmean]);
    map.getSource(CURSOR).setData(fc([
      { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[q.lon, q.lat], foot] } },
      { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [q.lon, q.lat] } }]));
  }

  // ---- chart -------------------------------------------------------------
  var fmtD = LSProfileChart.fmtD, esc = LSProfileChart.esc;
  function seriesLabel(se) {
    return se.group.name + ' (' + se.traces + ' trace' + (se.traces === 1 ? '' : 's') + ')';
  }
  function draw() {
    if (!result || !plane) return;
    chart.setData({ length: plane.len, series: result.series.map(function (se) {
      return { id: 'g' + se.group.id, label: seriesLabel(se), short: se.group.name, color: se.color, runs: se.runs };
    }) });
    var used = {}, n = 0, traces = 0;
    result.series.forEach(function (se) {
      traces += se.traces;
      se.runs.forEach(function (run) { run.forEach(function (q) { if (q.dem) { used[q.dem] = (used[q.dem] || 0) + 1; n++; } }); });
    });
    var dems = Object.keys(used).sort(function (a, b) { return used[b] - used[a]; })
      .map(function (k) { return k + ' ' + Math.round(100 * used[k] / n) + '%'; }).join(', ');
    chart.setStatus('A→A′ ' + Math.round(plane.az) + '°, ' + fmtD(plane.len) + (plane.custom ? ' (set by hand)' : '') + ' · ' +
                    traces + ' trace' + (traces === 1 ? '' : 's') +
                    (result.pending ? ' · reading elevations…' : (dems ? ' · surfaces: ' + dems : ' · no elevations')));
  }
  function tip(hit) {
    var q = hit.point, se = hit.series;
    return {
      read: fmtD(q.d) + ' from A · ' + q.z.toFixed(1) + ' m · ' + traceLabel(q.trace) + ' · ' + q.dem,
      html: '<div class="profile-tip-h"><span class="profile-tip-sw" style="background:' + se.color + '"></span>' + esc(se.short) + '</div>' +
            '<div class="profile-tip-d">' + esc(traceLabel(q.trace)) + '</div>' +
            '<div>' + fmtD(q.d) + ' from A<b>' + q.z.toFixed(1) + ' m</b></div>' +
            '<div>' + fmtD(Math.abs(q.t)) + ' ' + (q.t >= 0 ? 'left' : 'right') + ' of the plane<b>' + esc(q.dem) + '</b></div>'
    };
  }
  function slug(s) { return String(s || 'group').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
  function fileStem() { return 'group_profile_' + groups.map(function (g) { return slug(g.name); }).join('+'); }
  function csv() {
    if (!result || !plane) return null;
    var rows = ['group,trace_id,kind,part,dist_along_plane_m,offset_from_plane_m,lon,lat,z_m,surface'];
    result.series.forEach(function (se) {
      se.runs.forEach(function (run) { run.forEach(function (q) {
        var p = q.trace.properties;
        rows.push(['"' + se.group.name.replace(/"/g, '""') + '"', p.id, p.kind || 'scarp', q.part, q.d.toFixed(2), q.t.toFixed(2),
                   q.lon.toFixed(7), q.lat.toFixed(7), isNaN(q.z) ? '' : q.z.toFixed(2), q.dem].join(','));
      }); });
    });
    rows.push('# plane A ' + plane.A[1].toFixed(6) + ',' + plane.A[0].toFixed(6) + ' -> A′ ' + plane.B[1].toFixed(6) + ',' +
              plane.B[0].toFixed(6) + '; azimuth ' + plane.az.toFixed(1) + ' deg; length ' + plane.len.toFixed(1) +
              ' m; offset positive = left looking A->A′');
    var w = chart.window();
    if (w) {
      rows.push('# window ' + w.d0.toFixed(1) + '-' + w.d1.toFixed(1) + ' m along the plane: group, n, mean_m, slope_deg, slope_pct, curvature_per_km (least-squares line / parabola)');
      chart.data().series.forEach(function (s) {
        var st = chart.stats(s);
        if (st && st.n >= 2) rows.push('# ' + [s.short, st.n, st.mean.toFixed(2), st.slopeDeg.toFixed(2), st.slopePct.toFixed(2), isNaN(st.curvature) ? '' : st.curvature.toFixed(3)].join(', '));
      });
    }
    rows.push('# elevations in metres in each surface\'s own vertical datum (see /lidar/methods/#heights)');
    return { name: fileStem() + '.csv', text: rows.join('\n') + '\n' };
  }
  function ensureChart() {
    if (chart) return;
    var p = document.createElement('div');
    p.className = 'float-panel hidden';
    p.id = 'tgp-panel';
    (map.getContainer().parentNode || document.body).appendChild(p);
    chart = LSProfileChart.create({
      panel: p, makePanel: LSFloat.make, hover: 'nearest', ends: ['A', 'A′'], statsName: 'group',
      buttons: [
        { label: 'Flip', title: 'Swap A and A′', onClick: function () {
            if (custom) custom = { A: custom.B, B: custom.A }; else flip = !flip;
            replane(); placeMarkers(); opts.onChange(); } },
        { cls: 'tgp-auto', label: 'Auto plane', title: 'Back to the automatic plane (through the two points farthest apart)',
          onClick: function () { custom = null; replane(); placeMarkers(); opts.onChange(); } },
        { label: '↻', title: 'Re-read the group (after editing its traces)', onClick: function () { run(); } }
      ],
      tip: tip, onHover: function (hit) { setCursor(hit && hit.point); },
      onChange: function () { opts.onChange(); }, csv: csv,
      svgName: function () { return fileStem() + '.svg'; },
      onClose: function () { teardown(); opts.onChange(); }
    });
    chart.setLocked(false);
    autoBtn();
  }

  // ---- open / close ------------------------------------------------------
  function run() {
    if (!groups.length) return;
    var mySeq = ++seq;
    var all = (opts.traces().features || []);
    var sets = groups.map(function (g) {
      return { group: g, members: all.filter(function (f) { return f.properties.group_id === g.id; }) };
    });
    var mem = [];
    sets.forEach(function (gs) { mem = mem.concat(gs.members); });
    ensureChart();
    chart.open();
    chart.setTitle('Group profile — ' + groups.map(function (g) { return g.name; }).join(' + '));
    plane = buildPlane(mem, custom);
    if (!plane && custom) { custom = null; plane = buildPlane(mem); }
    sig = signature();
    setPlane(); placeMarkers(); setCursor(null); autoBtn();
    if (!plane) {
      result = null; chart.setData({ length: 1, series: [] });
      chart.setStatus(mem.length ? 'The group’s traces are all at one point.' : 'This group has no traces yet.');
      return;
    }
    sample(sets, mySeq);
  }
  function open(ids, keepPlane) {
    var gs = [].concat(ids).map(function (id) { return opts.groupById(+id); }).filter(Boolean);
    if (!gs.length) return false;
    groups = gs;
    if (!keepPlane) custom = null;
    run();
    opts.onChange();
    return true;
  }
  function teardown() {
    seq++;
    groups = []; plane = null; result = null; custom = null; sig = '';
    clearTimeout(notifyTimer);
    if (map && map.getSource(SRC)) map.getSource(SRC).setData(fc());
    setCursor(null);
    markers.forEach(function (m) { m.remove(); }); markers = [];
  }
  function close() {
    teardown();
    if (chart) chart.close();
    opts.onChange();
  }
  function init(o) {
    map = o.map; opts = o;
    opts.preferred = o.preferred || function () { return []; };
    opts.onChange = o.onChange || function () {};
  }
  return {
    init: init, open: open, close: close,
    isOpen: function () { return groups.length > 0; },
    // The host calls this whenever the traces change; the profile re-reads
    // (after a pause, so a vertex drag is one re-read) only when an open
    // group's members did.
    notify: function () {
      if (!groups.length) return;
      clearTimeout(notifyTimer);
      notifyTimer = setTimeout(function () {
        if (!groups.length) return;
        groups = groups.map(function (g) { return opts.groupById(g.id) || g; });
        if (signature() !== sig) run();
      }, 600);
    },
    _test: { buildPlane: buildPlane, project: project },   // pure geometry, for checks
    // {groups: [id…], flip, locked, sel: {d0, d1} | null} or null when closed.
    state: function () {
      return groups.length ? { groups: groups.map(function (g) { return g.id; }), flip: flip,
                               locked: chart ? chart.locked() : false, sel: chart ? chart.window() : null,
                               plane: custom ? { A: custom.A.slice(), B: custom.B.slice() } : null } : null;
    },
    setState: function (st) {
      if (!st) return;
      flip = !!st.flip;
      ensureChart();
      chart.setLocked(!!st.locked);
      custom = st.plane && st.plane.A && st.plane.B ? { A: st.plane.A, B: st.plane.B } : null;
      var ids = st.groups || (st.group != null ? [st.group] : []);
      if (ids.length) open(ids, true);
      if (st.sel) chart.setWindow(st.sel);
    }
  };
})();
