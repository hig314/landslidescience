/* app.js: the Homer viewer. Wires the map, the side panel, the histogram and
 * the feature card. Everything it shows comes from files prep/ wrote:
 *   tiles/tiles.json            the layers, their units, ranges and methods
 *   tiles/<layer>.pmtiles       value tiles (value_tiles.js)
 *   inventory/<layer>.geojson   the DGGS inventory
 *   zonal/distributions.json    cell counts per bin: terrain, scars by age
 *   zonal/scars.json            one row of statistics per scar
 * The camera is in the URL hash (MapLibre's own); the panel's state is in the
 * query string, so a copied address reopens the same view.
 */
(function () {
  var C = window.PH_CONFIG, VT = PH.ValueTiles, $ = function (id) { return document.getElementById(id); };
  // An array, not an object: a key like '1964' would sort itself to the front of an object's keys.
  var AGES = [['pre1952', '#7b6fb0'], ['1952-1975', '#4c9bd6'], ['1964', '#111111'], ['1975-1986', '#3fae7a'],
              ['1986-2000', '#c9b227'], ['2000-2013', '#f08a2c'], ['2013-2016', '#d7263d']];
  var AGE_COLORS = {}, AGE_ORDER = AGES.map(function (a) { AGE_COLORS[a[0]] = a[1]; return a[0]; });
  var Q = new URLSearchParams(location.search);
  var S = {            // panel state
    stat: Q.get('s') || 'slope_14m', lo: Q.has('lo') ? +Q.get('lo') : null, hi: Q.has('hi') ? +Q.get('hi') : null,
    hl: Q.get('hl') ? Q.get('hl').split(',').map(Number) : null,
    ages: Q.get('ages') ? Q.get('ages').split(',') : null, scar: Q.get('scar')
  };
  var meta, dist, scarRows = {}, map, shade;

  function getJSON(path) {
    return fetch(C.data + path).then(function (r) {
      if (!r.ok) throw new Error(path + ': ' + r.status);
      return r.json();
    });
  }
  function num(v, units) {
    if (v == null || v !== v) return 'no value';
    var a = Math.abs(v), s = a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 0.1 ? v.toFixed(2) : v.toPrecision(2);
    return s + (units && units !== 'class' ? ' ' + units : '');
  }
  function layerEntry(name) { return meta.layers.find(function (L) { return L.name === name; }); }
  // A ramp defined over fixed values (the shared slope ramp, 0-90 degrees) opens on those values.
  function defaultRange(L) { return VT.rampRange(L.name) || L.display || [L.percentiles.p1, L.percentiles.p99]; }
  function pivots(L) { return L.kind === 'diverging' && S.lo < 0 && S.hi > 0; }
  function writeUrl() {
    var q = new URLSearchParams();
    q.set('s', S.stat);
    if (S.lo != null) { q.set('lo', S.lo); q.set('hi', S.hi); }
    if (!$('sym').checked) q.set('sym', 0);
    if (S.hl) q.set('hl', S.hl.map(function (v) { return +v.toPrecision(4); }).join(','));
    if (S.ages) q.set('ages', S.ages.join(','));
    if (S.scar) q.set('scar', S.scar);
    PH.Surface.writeUrl(q);
    if ($('sop').value !== $('sop').defaultValue) q.set('sop', $('sop').value);
    ['scars', 'scarps', 'deposits', 'runouts'].forEach(function (k) {
      if ($('l-' + k).checked !== $('l-' + k).defaultChecked) q.set(k, $('l-' + k).checked ? 1 : 0);
    });
    history.replaceState(null, '', location.pathname + '?' + q + location.hash);
  }

  /* ---- statistic drape ---- */
  function applyStat() {
    var L = layerEntry(S.stat);
    $('stat-ui').hidden = !L;
    map.setLayoutProperty('stat', 'visibility', L ? 'visible' : 'none');
    if (L) {
      var cls = L.kind === 'class';
      if (!cls && S.lo == null) { var r = defaultRange(L); S.lo = r[0]; S.hi = r[1]; }
      map.getSource('stat').setTiles([VT.tileUrl(L.name, cls ? {lo: 0, hi: 1} : {lo: S.lo, hi: S.hi, hl: S.hl})]);
      map.setPaintProperty('stat', 'raster-opacity', +$('sop').value / 100);
      $('sopv').textContent = (+$('sop').value / 100).toFixed(2);
      $('range-row').hidden = $('ramp').hidden = cls;
      if (!cls) {
        $('lo').value = +(+S.lo).toPrecision(4); $('hi').value = +(+S.hi).toPrecision(4);
        var T = track(L), a = T.pos(S.lo), b = T.pos(S.hi);
        $('ramp-bar').style.background = VT.rampCss(L.name, a, b, pivots(L) ? T.pos(0) : null);
        $('sym-row').hidden = L.kind !== 'diverging';
        $('h-lo').style.left = a * 100 + '%'; $('h-hi').style.left = b * 100 + '%';
        $('ramp-t0').textContent = num(T.t0); $('ramp-u').textContent = L.units; $('ramp-t1').textContent = num(T.t1);
      }
      $('legend').innerHTML = !cls ? '' : Object.keys(L.classes).map(function (k) {
        return '<div class="cls"><i class="sw" style="background:' + VT.classColor(L.name, k) + '"></i>' + L.classes[k] + '</div>';
      }).join('');
      $('method').textContent = L.method;
    }
    drawHist();
    if (S.scar) showCard(S.scar);
    writeUrl();
  }
  /* The colour-range slider's track: the layer's default range, widened to hold a typed range beyond it.
     Positions are fractions 0..1 along it, in log space for a log layer. */
  function track(L) {
    var r = defaultRange(L), lg = L.kind === 'log', f = lg ? Math.log10 : function (v) { return v; };
    var t0 = Math.min(r[0], S.lo), t1 = Math.max(r[1], S.hi), a = f(t0), w = f(t1) - a;
    return {t0: t0, t1: t1,
            pos: function (v) { return (f(v) - a) / w; },
            val: function (p) { var x = a + p * w; return +(lg ? Math.pow(10, x) : x).toPrecision(3); }};
  }
  function locked() { return layerEntry(S.stat).kind === 'diverging' && $('sym').checked; }
  function dragHandle(id, which) {
    var el = $(id), T = null, raf = 0;
    el.addEventListener('pointerdown', function (e) {
      T = track(layerEntry(S.stat));          // fixed for the drag, so the handle follows the pointer
      el.setPointerCapture(e.pointerId); e.preventDefault();
    });
    el.addEventListener('pointermove', function (e) {
      if (!T) return;
      var box = el.parentNode.getBoundingClientRect(), p = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
      var gap = 0.02;                         // the handles never meet: a ramp needs some width
      if (locked()) {                         // both handles the same distance from zero
        var z = T.pos(0), d = T.val(z + Math.max(gap / 2, which === 'lo' ? z - p : p - z));
        S.lo = -d; S.hi = d;
      } else if (which === 'lo') S.lo = T.val(Math.min(p, T.pos(S.hi) - gap)); else S.hi = T.val(Math.max(p, T.pos(S.lo) + gap));
      if (!raf) raf = requestAnimationFrame(function () { raf = 0; applyStat(); });
    });
    function end() { T = null; }
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  }
  function setStat(name) {
    S.stat = name; S.lo = S.hi = null; S.hl = null;
    applyStat();
  }

  /* ---- histogram: terrain against the scars of the ticked ages ---- */
  function activeAges() {
    return Array.prototype.slice.call(document.querySelectorAll('#ages input:checked')).map(function (i) { return i.value; });
  }
  function drawHist() {
    var D = dist.layers[S.stat], L = layerEntry(S.stat);
    $('hist').hidden = !D;
    if (!D) return;
    var ages = activeAges(), n = D.edges.length - 1, b = new Array(n).fill(0);
    ages.forEach(function (a) { var c = D.counts['scars:' + a]; for (var i = 0; i < n; i++) b[i] += c[i]; });
    var row = S.scar && scarRows[S.scar];
    $('hist-title').textContent = L.label;
    $('hist-b').textContent = 'scars (' + (ages.length === AGE_ORDER.length ? 'all ages' : ages.join(', ') || 'none') + ')';
    $('hist-sel').textContent = S.hl ? num(S.hl[0]) + ' to ' + num(S.hl[1], L.units) : '';
    PH.Hist.draw($('hist-plot'), {edges: D.edges, log: D.kind === 'log', units: L.units, a: D.counts.terrain, b: b,
                                  sel: S.hl, mark: row ? row[S.stat + '_p50'] : null}, function (range) {
      S.hl = range;
      applyStat();
    });
  }

  /* ---- inventory ---- */
  function applyInventory() {
    var ages = activeAges();
    S.ages = ages.length === AGE_ORDER.length ? null : ages;
    var f = ['in', ['get', 'age'], ['literal', ages]];
    ['scars-fill', 'scars-case', 'scars-line'].forEach(function (id) {
      map.setFilter(id, f);
      map.setLayoutProperty(id, 'visibility', $('l-scars').checked ? 'visible' : 'none');
    });
    map.setLayoutProperty('scarps', 'visibility', $('l-scarps').checked ? 'visible' : 'none');
    ['deposits', 'deposits-line'].forEach(function (id) { map.setLayoutProperty(id, 'visibility', $('l-deposits').checked ? 'visible' : 'none'); });
    ['runouts', 'runouts-line'].forEach(function (id) { map.setLayoutProperty(id, 'visibility', $('l-runouts').checked ? 'visible' : 'none'); });
    drawHist();
    writeUrl();
  }

  function showCard(id) {
    var r = scarRows[id];
    if (!r) { $('card').hidden = true; return; }
    S.scar = id;
    var rows = meta.layers.filter(function (L) { return L.kind !== 'class' && (L.name + '_p50') in r; }).map(function (L) {
      return '<tr class="' + (L.name === S.stat ? 'cur' : '') + '"><td>' + (L.name === 'surface' ? 'Elevation' : L.label) + ' <span class="u">' + L.units + '</span></td><td>' + num(r[L.name + '_p10']) + '</td><td>' +
        num(r[L.name + '_p50']) + '</td><td>' + num(r[L.name + '_p90']) + '</td><td>' + num(r['head_' + L.name + '_p50']) + '</td></tr>';
    }).join('');
    $('card').innerHTML = '<h3><span>' + id + ' · ' + r.age + '</span><button id="card-x" title="Close">×</button></h3>' +
      '<div>' + Math.round(r.area_m2).toLocaleString() + ' m², ' + r.cells + ' cells; ' + r.z_min + ' to ' + r.z_max + ' m (' + r.height_m +
      ' m high); faces ' + r.aspect_14m_dir + '°</div>' +
      '<table><tr><th></th><th>p10</th><th>median</th><th>p90</th><th>head</th></tr>' + rows + '</table>' +
      '<p class="note">Over the scar\'s cells at 2 m. Head = median over its highest fifth. The dashed line on the histogram is this scar\'s median.</p>';
    $('card').hidden = false;
    $('card-x').onclick = function () { S.scar = null; $('card').hidden = true; map.setFilter('scar-sel', ['==', ['get', 'id'], '']); drawHist(); writeUrl(); };
    map.setFilter('scar-sel', ['==', ['get', 'id'], id]);
    drawHist();
    writeUrl();
  }

  /* ---- boot ---- */
  function restoreInputs() {
    PH.Surface.restore(Q);     // before the map exists, so the first tiles asked for carry the link's shading
    if (Q.has('sop')) $('sop').value = Q.get('sop');
    if (Q.get('sym') === '0') $('sym').checked = false;
    ['scars', 'scarps', 'deposits', 'runouts'].forEach(function (k) { if (Q.has(k)) $('l-' + k).checked = Q.get(k) === '1'; });
  }

  function build(inv) {
    // What the hosting page says about itself (PH_CONFIG): its title, where it sits, and any notice.
    if (C.title) $('title').textContent = C.title;
    if (C.sub) $('sub').textContent = C.sub;
    if (C.home) { $('crumb').hidden = false; $('crumb').innerHTML = '<a href="' + C.home.href + '">' + C.home.label + '</a>'; }
    if (C.notice) { $('notice').hidden = false; $('notice').textContent = C.notice; }
    var statSel = $('stat');
    // The menu in the site's groups (site.json layer_groups); a layer in no group goes under "Other".
    var opt = function (L) { return '<option value="' + L.name + '">' + L.label + '</option>'; };
    var grouped = {}, groups = (meta.layer_groups || []).map(function (g) {
      g.layers.forEach(function (n) { grouped[n] = 1; });
      return {title: g.title, layers: g.layers.map(layerEntry).filter(Boolean)};
    });
    groups.push({title: 'Other', layers: meta.layers.filter(function (L) { return !grouped[L.name]; })});
    statSel.innerHTML = '<option value="none">None</option>' + groups.map(function (g) {
      return g.layers.length ? '<optgroup label="' + g.title + '">' + g.layers.map(opt).join('') + '</optgroup>' : '';
    }).join('');
    if (S.stat !== 'none' && !layerEntry(S.stat)) S.stat = 'none';
    statSel.value = S.stat;
    $('ages').innerHTML = AGE_ORDER.map(function (a) {
      var g = dist.groups['scars:' + a], n = inv.scars.features.filter(function (f) { return f.properties.age === a; }).length;
      return '<label class="chk"><input type="checkbox" value="' + a + '"' + (!S.ages || S.ages.indexOf(a) >= 0 ? ' checked' : '') +
        '><i class="sw" style="background:' + AGE_COLORS[a] + '"></i>' + (a === 'pre1952' ? 'before 1952' : a) +
        '<span class="n">' + n + (g ? ' · ' + (g.km2 * 100).toFixed(1) + ' ha' : '') + '</span></label>';
    }).join('');
    $('credits').innerHTML = 'Surface: ' + meta.dem.title + ', landslidescience.org, 2 m. Inventory: <a href="' + meta.inventory.url +
      '">Salisbury (2024), DGGS RI 2024-3</a>, shown unmodified apart from the changes listed in inventory.json. ' +
      'Statistics are computed on the native 2 m grid; the map shows them resampled to tiles.';

    var ageColor = ['match', ['get', 'age']];
    AGE_ORDER.forEach(function (a) { ageColor.push(a, AGE_COLORS[a]); });
    ageColor.push('#888');

    map.addSource('imagery', {type: 'raster', tileSize: 256, maxzoom: 19, attribution: '© Esri, Maxar, Earthstar Geographics',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}']});
    map.addLayer({id: 'imagery', type: 'raster', source: 'imagery', paint: {'raster-opacity': 0}});
    var demSrc = shade.rasterSource('dem');
    demSrc.tiles = [DemShade.url('dem', PH.Surface.readOpts())];
    map.addSource('shade', demSrc);
    map.addLayer({id: 'shade', type: 'raster', source: 'shade', paint: {'raster-fade-duration': 0}});
    map.addSource('terrain', shade.rasterDemSource('dem'));
    map.addSource('stat', {type: 'raster', tileSize: 256, minzoom: 10, maxzoom: 15, bounds: meta.bounds,
                           tiles: [VT.tileUrl('slope_14m', {lo: 0, hi: 60})]});
    map.addLayer({id: 'stat', type: 'raster', source: 'stat', layout: {visibility: 'none'},
                  paint: {'raster-fade-duration': 0, 'raster-resampling': 'nearest'}});
    ['runouts', 'deposits', 'scars', 'scarps'].forEach(function (k) { map.addSource(k, {type: 'geojson', data: inv[k]}); });
    map.addLayer({id: 'runouts', type: 'fill', source: 'runouts', paint: {'fill-color': '#00838f', 'fill-opacity': 0.25}});
    map.addLayer({id: 'runouts-line', type: 'line', source: 'runouts', paint: {'line-color': '#00838f', 'line-width': 1}});
    map.addLayer({id: 'deposits', type: 'fill', source: 'deposits', paint: {'fill-color': '#8d6e63', 'fill-opacity': 0.35}});
    map.addLayer({id: 'deposits-line', type: 'line', source: 'deposits', paint: {'line-color': '#5d4037', 'line-width': 1}});
    map.addLayer({id: 'scars-fill', type: 'fill', source: 'scars', paint: {'fill-color': ageColor, 'fill-opacity': 0.3}});
    // a pale casing under the outline: the age colours have to read over any ramp and over imagery
    map.addLayer({id: 'scars-case', type: 'line', source: 'scars', paint: {'line-color': '#fff', 'line-width': 4, 'line-opacity': 0.75}});
    map.addLayer({id: 'scars-line', type: 'line', source: 'scars', paint: {'line-color': ageColor, 'line-width': 2}});
    map.addLayer({id: 'scar-sel', type: 'line', source: 'scars', filter: ['==', ['get', 'id'], ''],
                  paint: {'line-color': '#fff', 'line-width': 4, 'line-gap-width': 1.6}});
    map.addLayer({id: 'scarps', type: 'line', source: 'scarps', paint: {'line-color': '#111', 'line-width': 1.4, 'line-dasharray': [3, 1.5]}});

    $('sop').addEventListener('input', applyStat);
    statSel.addEventListener('change', function () { setStat(statSel.value); });
    function rangeTyped(ev) {
      var lo = parseFloat($('lo').value), hi = parseFloat($('hi').value);
      if (locked()) { hi = Math.abs(ev.target.id === 'lo' ? lo : hi); lo = -hi; }
      if (isFinite(lo) && isFinite(hi) && hi > lo && !(layerEntry(S.stat).kind === 'log' && lo <= 0)) { S.lo = lo; S.hi = hi; applyStat(); }
    }
    dragHandle('h-lo', 'lo');
    dragHandle('h-hi', 'hi');
    $('lo').addEventListener('change', rangeTyped);
    $('hi').addEventListener('change', rangeTyped);
    $('sym').addEventListener('change', function () {
      if (locked()) { S.hi = Math.max(Math.abs(S.lo), Math.abs(S.hi)); S.lo = -S.hi; }
      applyStat();
    });
    $('range-reset').addEventListener('click', function () { S.lo = S.hi = null; applyStat(); });
    document.querySelectorAll('#ages input, #l-scars, #l-scarps, #l-deposits, #l-runouts').forEach(function (i) {
      i.addEventListener('change', applyInventory);
    });

    map.on('click', 'scars-fill', function (e) { showCard(e.features[0].properties.id); });
    map.on('mouseenter', 'scars-fill', function () { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'scars-fill', function () { map.getCanvas().style.cursor = ''; });
    var probing = 0;
    map.on('mousemove', function (e) {
      var L = layerEntry(S.stat), mine = ++probing;
      if (!L) return;
      VT.valueAt(L.name, e.lngLat.lng, e.lngLat.lat, map.getZoom()).then(function (v) {
        if (mine !== probing) return;
        $('probe').textContent = L.label + ': ' + (L.kind === 'class' ? (v ? L.classes[v] || v : 'none') : num(v, L.units));
      }).catch(function () {});
    });

    PH.Surface.init(map, {demId: 'dem', shadeSource: 'shade', terrainSource: 'terrain', imageryLayer: 'imagery', onChange: writeUrl});
    applyInventory();
    applyStat();
  }

  restoreInputs();
  map = new maplibregl.Map({
    container: 'map', hash: true, center: C.center, zoom: C.zoom, pitch: C.pitch, bearing: C.bearing, maxPitch: 80,
    style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': '#dfe6ea'}}]}
  });
  map.addControl(new maplibregl.NavigationControl({visualizePitch: true}), 'top-left');
  map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-left');
  window.PH_map = map;
  shade = DemShade.instance;      // the one instance dem_shade_bridge.js made

  Promise.all([
    getJSON('tiles/tiles.json'), getJSON('zonal/distributions.json'), getJSON('zonal/scars.json'),
    getJSON('inventory/scars.geojson'), getJSON('inventory/scarps.geojson'), getJSON('inventory/deposits.geojson'),
    getJSON('inventory/runouts.geojson'),
    shade.addSource('dem', {pmtiles: new URL(C.demUrl, location.href).href, encoding: 'mapbox'}),
    new Promise(function (ok) { map.on('load', ok); })
  ]).then(function (r) {
    meta = r[0]; dist = r[1];
    r[2].forEach(function (row) { scarRows[row.id] = row; });
    meta.layers.forEach(function (L) { VT.add(L, new URL(C.data + 'tiles/' + L.pmtiles, location.href).href); });
    build({scars: r[3], scarps: r[4], deposits: r[5], runouts: r[6]});
  }).catch(function (err) {
    console.error(err);
    $('side').insertAdjacentHTML('afterbegin', '<p style="color:#b00020"><b>Could not load:</b> ' + err.message + '</p>');
  });
})();
