/* /glaciers — Lagrangian glacier-flow visualization.
 *
 * A deliberately small sibling of the inventory map: basemaps come from the
 * shared LSBasemaps module, the ITS_LIVE/Hugonnet overlays from the shared
 * LSOverlays module (single source of truth with the inventory), and the new
 * code here is the tracer engine: particles advected through the
 * TIME-VARYING ITS_LIVE velocity field —
 *     v(x, t) = v_annual(x, yearblend(t)) + amp(x)·cos(2π(doy − phase(x))/365.25)
 * per component — so particle paths curve when the field changes; unlike
 * constant-velocity streakline animations (Windy-style), displacement is the
 * time-integral of the data. Artifacts in the data (mosaic seams, gap years)
 * are deliberately visible: this doubles as a data-inspection tool.
 *
 * Density-managed respawn (spec'd by Hig): min/max particles per management
 * cell — under-filled cells spawn, over-filled cells retire their oldest
 * particles by FADING over several steps (no instant kills). Particles that
 * exit the ice or hit NoData also fade out.
 *
 * Tracer bundles (annual vx/vy + seasonal amp/phase + landice mask on the
 * 120 m EPSG:3413 grid) are built by tools/build_glacier_tracers.py and
 * served at /glaciers/data/ (immutable; bump TRACER_DATA_V on rebuild).
 */
(function () {
    'use strict';

    var CFG = window.GL_CONFIG || {};
    var TRACER_DATA_V = '1';

    // EPSG:3413 <-> lon/lat come from the shared module (ls_proj.js),
    // also used by the image-pair viewer.
    var lonLatTo3413 = window.LSProj.fromLonLat;
    var ll3413ToLonLat = window.LSProj.toLonLat;

    // ---------------------------------------------------------------------
    // Map + shared layers
    // ---------------------------------------------------------------------
    var DEFAULT_BASEMAP = 'esri-img';
    var basemaps = window.LSBasemaps.DEFAULTS.slice();
    function findBasemap(id) {
        for (var i = 0; i < basemaps.length; i++) if (basemaps[i].id === id) return basemaps[i];
        return null;
    }
    if (window.LSBasemaps.registerProtocols) window.LSBasemaps.registerProtocols();

    // URL hash (shared codec, same grammar as /inventory/): view + basemap +
    // overlays restore on load; site= and t= are this app's extras.
    var _hash = window.LSHash ? window.LSHash.parse(location.hash) : { extras: {} };
    var _currentBasemap = (_hash.base && findBasemap(_hash.base)) ? _hash.base : DEFAULT_BASEMAP;
    var _hashHadView = _hash.zoom != null;
    var _hashT = _hash.extras.t != null ? parseFloat(_hash.extras.t) : null;

    var map = new maplibregl.Map({
        container: 'gl-map',
        style: window.LSBasemaps.styleFor(findBasemap(_currentBasemap)),
        center: _hashHadView ? [_hash.lon, _hash.lat]
                             : ((CFG.catalog[0] && CFG.catalog[0].center) || [-147.07, 61.15]),
        zoom: _hashHadView ? _hash.zoom
                           : ((CFG.catalog[0] && CFG.catalog[0].zoom) || 10),
        transformRequest: window.LSBasemaps.transformRequest,
        attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    // Globe projection — parity with the inventory map (re-assert per style).
    map.on('style.load', function () {
        if (typeof map.setProjection === 'function') {
            try { map.setProjection({ type: 'globe' }); } catch (e) {}
        }
    });

    // Overlays: the shared glacier set, one simple row each (checkbox +
    // opacity + the thinning variant toggle). No wiper here — the inventory
    // map is the comparison tool; this app spends its complexity on tracers.
    // Our robust fit of the RAW image pairs (tools/fit_pair_rasters.py),
    // tiled with the same ramps as the ITS_LIVE layers above so any visible
    // difference is a data difference. Defined here rather than in the
    // shared module because it is glaciers-only and experimental — the
    // inventory map has no business carrying it.
    var EXPERIMENTAL = !!CFG.experimental;
    var FIT_TILE_V = '1';
    function _fitSourceDef(key) {
        return {
            type: 'raster',
            tiles: ['/tiles/glacierfit/' + key + '/{z}/{x}/{y}.png?v=' + FIT_TILE_V],
            tileSize: 256, minzoom: 6, maxzoom: 13,
            attribution: 'Robust fit of ITS_LIVE image pairs (experimental) · NASA MEaSUREs'
        };
    }
    var FIT_OVERLAYS = [
        { id: 'fit-v0', layerId: 'ov-fit-v0', sourceId: 'ov-fit-v0-src',
          label: 'Fitted speed (ours)', sub: 'robust fit of raw pairs @2024.5 · Columbia box only',
          sourceDef: function () { return _fitSourceDef('v0'); }, defOpacity: 0.9 },
        { id: 'fit-amp', layerId: 'ov-fit-amp', sourceId: 'ov-fit-amp-src',
          label: 'Fitted seasonal amplitude (ours)', sub: 'same ramp as ITS_LIVE v_amp · Columbia box only',
          sourceDef: function () { return _fitSourceDef('amp'); }, defOpacity: 0.9 },
        { id: 'fit-trend', layerId: 'ov-fit-trend', sourceId: 'ov-fit-trend-src',
          label: 'Fitted speed trend (ours)', sub: 'same ramp as ITS_LIVE dv/dt · Columbia box only',
          sourceDef: function () { return _fitSourceDef('trend'); }, defOpacity: 0.9 },
    ];

    // The fitted-pair overlays are part of the /pairs/ experimental stack and
    // ship only where that data exists.
    var OVERLAYS = window.LSOverlays.glacierOverlays({})
        .concat(EXPERIMENTAL ? FIT_OVERLAYS : []);
    var _ovState = {};
    OVERLAYS.forEach(function (ov) {
        var s = _hash.ov && _hash.ov[ov.id];
        _ovState[ov.id] = { on: !!(s && s.left),
                            op: (s && s.opLeft != null) ? s.opLeft : ov.defOpacity };
        if (ov.variant && s) ov.variant.set(!!s.smooth);
    });

    function ensureOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getSource(ov.sourceId)) map.addSource(ov.sourceId, ov.sourceDef());
            if (!map.getLayer(ov.layerId)) {
                map.addLayer({
                    id: ov.layerId, type: 'raster', source: ov.sourceId,
                    layout: { 'visibility': 'none' },
                    paint: { 'raster-opacity': 1, 'raster-resampling': 'nearest' }
                });
            }
        });
        applyOverlays();
    }
    function applyOverlays() {
        OVERLAYS.forEach(function (ov) {
            if (!map.getLayer(ov.layerId)) return;
            var st = _ovState[ov.id];
            map.setLayoutProperty(ov.layerId, 'visibility', st.on ? 'visible' : 'none');
            map.setPaintProperty(ov.layerId, 'raster-opacity', st.op);
        });
    }
    function swapOverlaySource(ov) {
        if (!map.getLayer(ov.layerId)) return;
        map.removeLayer(ov.layerId);
        map.removeSource(ov.sourceId);
        ensureOverlays();
    }
    map.on('style.load', function () { ensureOverlays(); });

    // ---------------------------------------------------------------------
    // Minimal panel UI: site select, basemap select, overlay rows
    // ---------------------------------------------------------------------
    var siteSel = document.getElementById('gl-site');
    (CFG.catalog || []).forEach(function (c) {
        var o = document.createElement('option');
        o.value = c.slug; o.textContent = c.name;
        siteSel.appendChild(o);
    });

    var bmRow = document.getElementById('gl-basemap-row');
    var bmSel = document.createElement('select');
    basemaps.forEach(function (bm) {
        var o = document.createElement('option');
        o.value = bm.id; o.textContent = bm.label;
        if (bm.id === DEFAULT_BASEMAP) o.selected = true;
        bmSel.appendChild(o);
    });
    bmSel.value = _currentBasemap;
    bmSel.addEventListener('change', function () {
        var bm = findBasemap(bmSel.value);
        if (!bm) return;
        _currentBasemap = bm.id;
        // styleFor, not buildRasterStyle: style-carrying basemaps (Blank
        // White) need their prebuilt style; diff:false forces the swap.
        map.setStyle(window.LSBasemaps.styleFor(bm), { diff: false });
        // overlays re-added on style.load
        writeHash();
    });
    bmRow.appendChild(bmSel);

    var ovWrap = document.getElementById('gl-overlays');
    OVERLAYS.forEach(function (ov) {
        var st = _ovState[ov.id];
        var row = document.createElement('div');
        row.className = 'gl-ov-row';
        var lab = document.createElement('label');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = st.on;   // hash-restored state
        var span = document.createElement('span');
        span.textContent = ov.label;
        span.title = ov.sub;
        lab.appendChild(cb); lab.appendChild(span);
        row.appendChild(lab);
        var op = document.createElement('input');
        op.type = 'range'; op.min = '10'; op.max = '100';
        op.value = String(Math.round(st.op * 100));
        op.style.display = st.on ? '' : 'none';
        op.addEventListener('input', function () { st.op = (+op.value) / 100; applyOverlays(); });
        op.addEventListener('change', writeHash);
        row.appendChild(op);
        var vlab = null, vcb = null;
        if (ov.variant) {
            vlab = document.createElement('label');
            vlab.style.cssText = 'font-size:11px;color:#555;';
            if (ov.variant.title) vlab.title = ov.variant.title;
            vcb = document.createElement('input');
            vcb.type = 'checkbox';
            vcb.checked = ov.variant.get();
            vcb.addEventListener('change', function () {
                ov.variant.set(vcb.checked);
                swapOverlaySource(ov);
                writeHash();
            });
            vlab.appendChild(vcb);
            vlab.appendChild(document.createTextNode(ov.variant.label));
            vlab.style.display = st.on ? 'flex' : 'none';
            row.appendChild(vlab);
        }
        cb.addEventListener('change', function () {
            st.on = cb.checked;
            op.style.display = st.on ? '' : 'none';
            if (vlab) vlab.style.display = st.on ? 'flex' : 'none';
            ensureOverlays();
            writeHash();
        });
        ovWrap.appendChild(row);
    });

    // Tracer controls
    var trWrap = document.getElementById('gl-tracer-controls');
    trWrap.innerHTML = '';
    function ctlRow(labelText, input) {
        var row = document.createElement('div');
        row.className = 'gl-ov-row';
        var lab = document.createElement('label');
        lab.appendChild(input);
        lab.appendChild(document.createTextNode(labelText));
        row.appendChild(lab);
        trWrap.appendChild(row);
        return input;
    }
    // Velocity source: the standard annual composites, or our own robust fit
    // of the raw image pairs. Same tracer engine either way, so the two are
    // directly comparable — which is the point.
    var srcSel = document.createElement('select');
    // Field sources, in order of coverage. Region is the general case; the
    // per-site bundles exist only because they carry work the regional tiles
    // do not (observed quarterly composites 2024+), and the pair fit is the
    // Columbia experiment. The site MENU is a camera jump in every mode.
    ([['region', 'Field: ITS_LIVE annual — all Alaska'],
      ['annual', 'Field: ITS_LIVE + observed quarterly (this site only)']]
     .concat(EXPERIMENTAL ? [['fit', 'Field: robust pair fit (Columbia box only)']] : []))
     .forEach(function (o) {
        var e = document.createElement('option');
        e.value = o[0]; e.textContent = o[1];
        srcSel.appendChild(e);
    });
    srcSel.style.cssText = 'width:100%; font-size:11px; margin-bottom:4px;';
    srcSel.addEventListener('change', function () {
        particles = []; _kf.clear();
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (srcSel.value === 'fit' && !FITM) loadFitModel();
        if (srcSel.value === 'region') { loadRegionManifest(); ensureRegionTiles(); }
        manageDensity();
    });
    trWrap.appendChild(srcSel);

    var cbTracers = ctlRow('Flow tracers', document.createElement('input'));
    cbTracers.type = 'checkbox'; cbTracers.checked = true;
    var cbTrails = ctlRow('Trails', document.createElement('input'));
    cbTrails.type = 'checkbox'; cbTrails.checked = true;
    // ---- velocity window -------------------------------------------------
    // Purely a runtime test — no precomputation — so it can be interactive.
    // AUTO min is tied to on-screen legibility rather than to a fixed speed:
    // a tracer is worth drawing when it moves at least VIS_PX_PER_SEC pixels
    // per second of wall time, which depends on BOTH playback rate and zoom
    //     v_min = VIS_PX_PER_SEC * metres_per_pixel / (years per second)
    // so faster playback and deeper zoom both lower the threshold, exactly
    // where slow motion becomes legible.
    // Calibration: at 1.2 px/s uncapped this demanded 1,423 m/yr at z6 and
    // 356 m/yr at z8 — i.e. it hid essentially the entire field whenever you
    // zoomed out, which is the opposite of useful. Two corrections: a gentler
    // visibility target (trails make sub-pixel-per-second motion legible), and
    // a hard CAP so the automatic minimum can never hide the bulk of a
    // glacier no matter how far out you are. Auto now only ever suppresses
    // motion that is genuinely imperceptible, and relaxes toward zero as you
    // zoom in or speed up.
    var VIS_PX_PER_SEC = 0.35;
    var AUTO_VMIN_CAP = 25;      // m/yr — auto never demands more than this
    var AUTO_VMIN_FLOOR = 0.5;   // m/yr
    var vminAuto = true, vminManual = 0, vmaxManual = Infinity;
    var vwRow = document.createElement('div');
    vwRow.className = 'gl-ov-row';
    vwRow.innerHTML = '<span style="font-size:10px;color:#777;">speed window (m/yr)</span>';
    var vwAuto = document.createElement('label');
    vwAuto.style.cssText = 'display:flex;gap:5px;align-items:center;font-size:11px;';
    var vwCb = document.createElement('input');
    vwCb.type = 'checkbox'; vwCb.checked = true;
    vwCb.title = 'Derive the minimum from playback speed and zoom';
    vwAuto.appendChild(vwCb);
    vwAuto.appendChild(document.createTextNode('auto minimum'));
    var vwVal = document.createElement('span');
    vwVal.style.cssText = 'font-size:10px;color:#555;margin-left:auto;font-variant-numeric:tabular-nums;';
    vwAuto.appendChild(vwVal);
    vwRow.appendChild(vwAuto);
    var vwMin = document.createElement('input');
    vwMin.type = 'range'; vwMin.min = '0'; vwMin.max = '100'; vwMin.value = '0';
    vwMin.title = 'Minimum speed to show';
    var vwMax = document.createElement('input');
    vwMax.type = 'range'; vwMax.min = '0'; vwMax.max = '100'; vwMax.value = '100';
    vwMax.title = 'Maximum speed to show';
    [vwMin, vwMax].forEach(function (el) {
        el.style.cssText = 'width:100%;height:12px;';
        vwRow.appendChild(el);
    });
    // log scale 0.3 .. 20000 m/yr
    function sliderToV(p) { return Math.exp(Math.log(0.3) + (p / 100) * (Math.log(20000) - Math.log(0.3))); }
    function readWindow() {
        vminManual = (+vwMin.value <= 0) ? 0 : sliderToV(+vwMin.value);
        vmaxManual = (+vwMax.value >= 100) ? Infinity : sliderToV(+vwMax.value);
    }
    function autoVMin() {
        var c = map.getCenter();
        var mpp = 156543.03392 * Math.cos(c.lat * Math.PI / 180) / Math.pow(2, map.getZoom());
        var p = Math.max(0.05, parseFloat((speedSel && speedSel.value) || 1));
        var v = VIS_PX_PER_SEC * mpp / p;
        return Math.min(AUTO_VMIN_CAP, Math.max(AUTO_VMIN_FLOOR, v));
    }
    function vWindow() {
        var lo = vminAuto ? autoVMin() : vminManual;
        return [lo, vmaxManual];
    }
    function syncVLabel() {
        var wnd = vWindow();
        vwVal.textContent = wnd[0].toFixed(wnd[0] < 10 ? 1 : 0) + ' – ' +
            (wnd[1] === Infinity ? '∞' : wnd[1].toFixed(0));
        vwMin.disabled = vminAuto;
        vwMin.style.opacity = vminAuto ? 0.4 : 1;
    }
    vwCb.addEventListener('change', function () {
        vminAuto = vwCb.checked; syncVLabel();
        particles = []; ctx.clearRect(0, 0, canvas.width, canvas.height); manageDensity();
    });
    [vwMin, vwMax].forEach(function (el) {
        el.addEventListener('input', function () { readWindow(); syncVLabel(); });
        el.addEventListener('change', function () {
            particles = []; ctx.clearRect(0, 0, canvas.width, canvas.height); manageDensity();
        });
    });
    trWrap.appendChild(vwRow);

    // Mapped-ice restriction. OFF lets tracers run wherever there is a valid
    // velocity — which is where slow non-glacier motion lives (creeping
    // slopes, rock glaciers, landslides), the signal the ice mask discards.
    var iceRow = document.createElement('div');
    iceRow.className = 'gl-ov-row';
    var iceLab = document.createElement('label');
    iceLab.style.cssText = 'display:flex;gap:5px;align-items:center;font-size:11px;';
    var iceCb = document.createElement('input');
    iceCb.type = 'checkbox'; iceCb.checked = true;
    iceCb.title = 'Uncheck to include motion off the mapped ice — slow slopes, rock glaciers, landslides';
    iceLab.appendChild(iceCb);
    iceLab.appendChild(document.createTextNode('restrict to mapped ice'));
    iceRow.appendChild(iceLab);
    iceCb.addEventListener('change', function () {
        particles = []; ctx.clearRect(0, 0, canvas.width, canvas.height); manageDensity();
    });
    trWrap.appendChild(iceRow);
    readWindow();
    syncVLabel();

    var densSel = document.createElement('select');
    ['sparse', 'normal', 'dense'].forEach(function (d) {
        var o = document.createElement('option');
        o.value = d; o.textContent = 'Density: ' + d;
        if (d === 'normal') o.selected = true;
        densSel.appendChild(o);
    });
    var densRow = document.createElement('div');
    densRow.className = 'gl-ov-row';
    densRow.appendChild(densSel);
    trWrap.appendChild(densRow);

    // ---------------------------------------------------------------------
    // Tracer bundle loading
    // ---------------------------------------------------------------------
    var B = null;    // active bundle {hdr, vx, vy, vxAmp, vyAmp, vxPh, vyPh, ice}

    function loadBundle(slug) {
        return fetch(CFG.dataBase + slug + '.json?v=' + TRACER_DATA_V)
            .then(function (r) {
                if (!r.ok) throw new Error('bundle header HTTP ' + r.status);
                return r.json();
            })
            .then(function (hdr) {
                return fetch(CFG.dataBase + hdr.bin + '?v=' + TRACER_DATA_V)
                    .then(function (r) {
                        if (!r.ok) throw new Error('bundle bin HTTP ' + r.status);
                        return r.arrayBuffer();
                    })
                    .then(function (buf) {
                        // Bundles are int16 (1 m/yr resolution, nodata
                        // sentinel) — decode once into Float32 with NaN.
                        var nod = hdr.nodata != null ? hdr.nodata : -32768;
                        var scale = hdr.scale || 1;
                        function view(name) {
                            var o = hdr.offsets[name];
                            if (hdr.dtype !== 'int16') {
                                return new Float32Array(buf, o[0] * 4, o[1]);
                            }
                            var raw = new Int16Array(buf, o[0] * 2, o[1]);
                            var out = new Float32Array(o[1]);
                            for (var i = 0; i < o[1]; i++) {
                                out[i] = raw[i] === nod ? NaN : raw[i] * scale;
                            }
                            return out;
                        }
                        var bundle = {
                            hdr: hdr,
                            vx: view('vx'), vy: view('vy'),
                            vxAmp: view('vx_amp'), vyAmp: view('vy_amp'),
                            vxPh: view('vx_phase'), vyPh: view('vy_phase'),
                            ice: view('landice'),
                            recent: null
                        };
                        if (!hdr.recent) return bundle;
                        // Optional trailing-edge block: OBSERVED quarterly
                        // median fields from the image-pair datacubes —
                        // used instead of annual+climatology where they
                        // cover (see sampleVelocity).
                        return fetch(CFG.dataBase + hdr.recent.bin + '?v=' + TRACER_DATA_V)
                            .then(function (r) {
                                if (!r.ok) throw new Error('recent bin HTTP ' + r.status);
                                return r.arrayBuffer();
                            })
                            .then(function (rbuf) {
                                var rn = hdr.recent.nodata != null ? hdr.recent.nodata : -32768;
                                function rview(name) {
                                    var o = hdr.recent.offsets[name];
                                    var raw = new Int16Array(rbuf, o[0] * 2, o[1]);
                                    var out = new Float32Array(o[1]);
                                    for (var i = 0; i < o[1]; i++) {
                                        out[i] = raw[i] === rn ? NaN : raw[i];
                                    }
                                    return out;
                                }
                                bundle.recent = {
                                    quarters: hdr.recent.quarters,
                                    vx: rview('vx'), vy: rview('vy')
                                };
                                return bundle;
                            })
                            .catch(function (e) {
                                console.warn('recent block unavailable:', e);
                                return bundle;   // annual-only is fine
                            });
                    });
            });
    }

    // ---------------------------------------------------------------------
    // REGION TILE MANAGER — the whole Alaska record (108 tiles from
    // tools/build_glacier_region.py), loaded by viewport with an LRU cache
    // so a session downloads only what it looks at. Each tile is a small
    // self-contained bundle on the same 240 m EPSG:3413 grid as the site
    // bundles; the biggest (St Elias) are ~14 MB, most well under 1 MB.
    // ---------------------------------------------------------------------
    var REG = { list: null, loaded: new Map(), loading: new Set(), cap: 12 };

    function loadRegionManifest() {
        if (REG.list || REG.manifestPending) return;
        REG.manifestPending = true;
        fetch(CFG.dataBase + 'region_manifest.json?v=' + TRACER_DATA_V)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (m) {
                if (!m) throw new Error('no manifest');
                REG.list = m.tiles;
                ensureRegionTiles();
            })
            .catch(function (e) {
                console.warn('region manifest unavailable', e);
                srcSel.value = 'annual';
            });
    }

    function tileCovers(t, x, y) {
        var g = t.grid;
        return x >= g.x0 && x < g.x0 + g.nx * g.dx &&
               y <= g.y0_north && y > g.y0_north - g.ny * g.dx;
    }

    function ensureRegionTiles() {
        if (!REG.list || srcSel.value !== 'region') return;
        var b = map.getBounds();
        var xs = [], ys = [];
        [[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()],
         [b.getWest(), b.getNorth()], [b.getEast(), b.getNorth()]].forEach(function (p) {
            var xy = lonLatTo3413(p[0], p[1]);
            xs.push(xy[0]); ys.push(xy[1]);
        });
        var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
        var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
        var want = REG.list.filter(function (t) {
            var g = t.grid;
            return !(g.x0 > x1 || g.x0 + g.nx * g.dx < x0 ||
                     g.y0_north < y0 || g.y0_north - g.ny * g.dx > y1);
        }).slice(0, 6);   // cap concurrent interest; the rest load as you pan
        want.forEach(function (t) {
            if (REG.loaded.has(t.key) || REG.loading.has(t.key)) {
                if (REG.loaded.has(t.key)) {           // refresh LRU order
                    var v = REG.loaded.get(t.key);
                    REG.loaded.delete(t.key); REG.loaded.set(t.key, v);
                }
                return;
            }
            REG.loading.add(t.key);
            fetch(CFG.dataBase + t.key + '.bin?v=' + TRACER_DATA_V)
                .then(function (r) { if (!r.ok) throw new Error('tile ' + r.status); return r.arrayBuffer(); })
                .then(function (buf) {
                    var nod = t.nodata != null ? t.nodata : -32768;
                    function view(name) {
                        var o = t.offsets[name];
                        var raw = new Int16Array(buf, o[0] * 2, o[1]);
                        var out = new Float32Array(o[1]);
                        for (var i = 0; i < o[1]; i++) out[i] = raw[i] === nod ? NaN : raw[i];
                        return out;
                    }
                    REG.loaded.set(t.key, {
                        hdr: t, vx: view('vx'), vy: view('vy'),
                        vxAmp: view('vx_amp'), vyAmp: view('vy_amp'),
                        vxPh: view('vx_phase'), vyPh: view('vy_phase'),
                        ice: view('landice')
                    });
                    while (REG.loaded.size > REG.cap) {
                        REG.loaded.delete(REG.loaded.keys().next().value);
                    }
                    manageDensity();
                })
                .catch(function (e) { console.warn('tile ' + t.key, e); })
                .then(function () { REG.loading.delete(t.key); });
        });
    }

    // Sample whichever loaded tile contains the point — same annual-blend +
    // seasonal-superposition maths as the site bundle, per tile.
    function sampleRegion(x, y, t) {
        if (!REG.loaded.size) return null;
        var it = REG.loaded.values(), e;
        while (!(e = it.next()).done) {
            var T = e.value;
            if (!tileCovers(T.hdr, x, y)) continue;
            return sampleBundleLike(T, x, y, t);
        }
        return null;
    }
    function regionOnIce(x, y) {
        if (!REG.loaded.size) return false;
        var it = REG.loaded.values(), e;
        while (!(e = it.next()).done) {
            var T = e.value, g = T.hdr.grid;
            if (!tileCovers(T.hdr, x, y)) continue;
            var j = Math.round((x - g.x0) / g.dx - 0.5);
            var i = Math.round((g.y0_north - y) / g.dx - 0.5);
            if (i < 0 || j < 0 || i >= g.ny || j >= g.nx) return false;
            var v = T.ice[i * g.nx + j];
            return v === v && v > 0;
        }
        return false;
    }

    // Robust-fit model (tools/fit_pair_rasters.py): 8 coefficients per cell
    // plus a provenance tier. Loaded on demand — the annual-composite path
    // never needs it.
    var FITM = null;
    function loadFitModel() {
        if (!siteSel.value) { srcSel.value = 'annual'; return; }
        var b = siteSel.value + '_pairs';
        fetch(CFG.dataBase + b + '_model.json?v=' + TRACER_DATA_V)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (mh) {
                if (!mh) throw new Error('no model for this site');
                return fetch(CFG.dataBase + mh.bin + '?v=' + TRACER_DATA_V)
                    .then(function (r) { return r.arrayBuffer(); })
                    .then(function (buf) {
                        var supOff = mh.coef_count * 4 + mh.source_count;
                        FITM = {
                            grid: mh.grid, tRef: mh.t_ref,
                            tLo: mh.t_window ? mh.t_window[0] : 2015.0,
                            tHi: mh.t_window ? mh.t_window[1] : mh.t_ref + 1.0,
                            coef: new Float32Array(buf, 0, mh.coef_count),
                            source: new Uint8Array(buf, mh.coef_count * 4, mh.source_count),
                            // slice(): the support planes sit after a uint8
                            // block, so their byte offset may be unaligned.
                            tFirst: mh.support_count
                                ? new Float32Array(buf.slice(supOff, supOff + mh.support_count * 4))
                                : null,
                            tLast: mh.support_count
                                ? new Float32Array(buf.slice(supOff + mh.support_count * 4,
                                                             supOff + mh.support_count * 8))
                                : null
                        };
                    });
            })
            .catch(function (e) {
                console.warn('fit model unavailable:', e);
                srcSel.value = 'annual';
            });
    }

    // Velocity from the fitted model — with two guards the raw evaluation
    // lacked, both diagnosed from real swirl artifacts:
    //  * TIME CLAMP: the model is only valid inside its fit window. Evaluated
    //    at 2016 the unclamped trend REVERSED 32% of cells (59% at 1990) —
    //    slow cells flip sign under noise-level trends. Outside the window
    //    the model freezes at the nearest edge rather than extrapolating.
    //  * BILINEAR blend of the four surrounding cells' velocities — the
    //    nearest-cell lookup made every cell boundary a velocity step.
    function _fitCellV(i, j, t) {
        var g = FITM.grid;
        if (i < 0 || j < 0 || i >= g.ny || j >= g.nx) return null;
        var k = i * g.nx + j;
        var src = FITM.source[k];
        if (!src || src === 4) return null;
        // Per-cell temporal support: beyond the era its kept measurements
        // cover, a cell has NO state — the ice may be gone. Returning null
        // (not a frozen value) is what makes the front's retreat visible:
        // fjord cells expire as the timeline passes their last real data.
        if (FITM.tLast) {
            if (t > FITM.tLast[k] + 0.5) return null;
            if (t < FITM.tFirst[k] - 0.5) return null;
        }
        var o = (i * g.nx + j) * 8;
        var tp = 2 * Math.PI, dtr = t - FITM.tRef;
        var co = Math.cos(tp * t), si = Math.sin(tp * t);
        var vx = FITM.coef[o] + FITM.coef[o+1]*dtr + FITM.coef[o+2]*co + FITM.coef[o+3]*si;
        var vy = FITM.coef[o+4] + FITM.coef[o+5]*dtr + FITM.coef[o+6]*co + FITM.coef[o+7]*si;
        if (!isFinite(vx) || !isFinite(vy)) return null;
        return [vx, vy];
    }
    function sampleFit(x, y, t) {
        if (!FITM) return null;
        var g = FITM.grid;
        t = Math.min(FITM.tHi, Math.max(FITM.tLo, t));
        var fx = (x - g.x0) / g.dx - 0.5;
        var fy = (g.y0_north - y) / g.dx - 0.5;
        var j0 = Math.floor(fx), i0 = Math.floor(fy);
        var tx = fx - j0, ty = fy - i0;
        var sx = 0, sy = 0, wsum = 0;
        for (var di = 0; di <= 1; di++) {
            for (var dj = 0; dj <= 1; dj++) {
                var v = _fitCellV(i0 + di, j0 + dj, t);
                if (!v) continue;
                var w = (di ? ty : 1 - ty) * (dj ? tx : 1 - tx);
                sx += w * v[0]; sy += w * v[1]; wsum += w;
            }
        }
        if (wsum < 0.05) return null;
        sx /= wsum; sy /= wsum;
        if (Math.abs(sx) > 25000 || Math.abs(sy) > 25000) return null;
        return { vx: sx, vy: sy };
    }

    // ---------------------------------------------------------------------
    // Field sampling — bilinear in space (NaN-corner aware), linear in time
    // between annual fields, climatological seasonal cycle superposed.
    // ---------------------------------------------------------------------
    function sampleGrid2(arr, nx, ny, fx, fy) {
        // arr is a [ny][nx] plane starting at some base offset handled by the
        // caller passing a subarray view; fx/fy fractional cell coords.
        var j0 = Math.floor(fx), i0 = Math.floor(fy);
        var tx = fx - j0, ty = fy - i0;
        var sum = 0, wsum = 0;
        for (var di = 0; di <= 1; di++) {
            for (var dj = 0; dj <= 1; dj++) {
                var i = i0 + di, j = j0 + dj;
                if (i < 0 || i >= ny || j < 0 || j >= nx) continue;
                var v = arr[i * nx + j];
                if (v !== v) continue;   // NaN
                var w = (di ? ty : 1 - ty) * (dj ? tx : 1 - tx);
                sum += w * v; wsum += w;
            }
        }
        return wsum > 0.05 ? sum / wsum : NaN;
    }

    // ITS_LIVE's seasonal fit is climatological over the window stated in the
    // product metadata ("climatological [2014-2024] ..."). Outside it we taper
    // to zero over one year rather than cutting hard, so playback does not
    // show a discontinuity at the window edge.
    // Bundles built after 2026-08 carry season_window parsed from the cube
    // attribute; the constant is the window the current product states.
    var SEASON_WINDOW_DEFAULT = [2014, 2025];
    function seasonWeight(hdr, t) {
        var w = (hdr && hdr.season_window) || SEASON_WINDOW_DEFAULT;
        if (t >= w[0] && t <= w[1]) return 1;
        var d = (t < w[0]) ? w[0] - t : t - w[1];
        return Math.max(0, 1 - d);
    }

    // Shared annual-bundle sampler (site bundle or region tile — identical
    // array layout, so one implementation serves both).
    function sampleBundleLike(BB, x, y, t) {
        var g = BB.hdr.grid;
        var fx = (x - g.x0) / g.dx - 0.5;
        var fy = (g.y0_north - y) / g.dx - 0.5;
        if (fx < -1 || fy < -1 || fx > g.nx || fy > g.ny) return null;
        var years = BB.hdr.years, nY = years.length;
        var ty = t - (years[0] + 0.5);
        var y0 = Math.floor(ty), frac = ty - y0;
        if (y0 < 0) { y0 = 0; frac = 0; }
        if (y0 >= nY - 1) { y0 = nY - 1; frac = 0; }
        var plane = g.nx * g.ny;
        var vx = sampleGrid2(BB.vx.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        var vy = sampleGrid2(BB.vy.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        if (frac > 0 && y0 < nY - 1) {
            var vx1 = sampleGrid2(BB.vx.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            var vy1 = sampleGrid2(BB.vy.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            if (vx1 === vx1 && vy1 === vy1 && vx === vx && vy === vy) {
                vx += frac * (vx1 - vx); vy += frac * (vy1 - vy);
            }
        }
        if (vx !== vx || vy !== vy) return null;
        // Seasonal superposition, faithful to the published convention:
        // vx_amp is "climatological mean seasonal AMPLITUDE of sinusoidal fit
        // to vx" and vx_phase is "day of seasonal MAXIMUM", so A*cos(2pi(doy
        // - phase)/365.25) peaks on the stated day with the stated amplitude,
        // and the annual vx is itself "mean annual velocity OF the sinusoidal
        // fit" — a zero-mean term added to it double-counts nothing.
        //
        // But the fit is climatological over a STATED WINDOW (2014-2024 in
        // this product), and the tracer timeline runs back to 1984. Replaying
        // that one cycle across the 1980s-2000s would assert seasonality
        // ITS_LIVE never fitted there, so the term is faded out beyond the
        // window rather than extrapolated: pre-window years show annual means
        // only, which is what the published analysis actually supports.
        var seas = seasonWeight(BB.hdr, t);
        if (seas > 0) {
            var doy = (t - Math.floor(t)) * 365.25;
            var ax = sampleGrid2(BB.vxAmp, g.nx, g.ny, fx, fy);
            var ay = sampleGrid2(BB.vyAmp, g.nx, g.ny, fx, fy);
            var px = sampleGrid2(BB.vxPh, g.nx, g.ny, fx, fy);
            var py = sampleGrid2(BB.vyPh, g.nx, g.ny, fx, fy);
            if (ax === ax && px === px) vx += seas * ax * Math.cos(2 * Math.PI * (doy - px) / 365.25);
            if (ay === ay && py === py) vy += seas * ay * Math.cos(2 * Math.PI * (doy - py) / 365.25);
        }
        if (Math.abs(vx) > 25000 || Math.abs(vy) > 25000) return null;
        return { vx: vx, vy: vy };
    }

    function sampleVelocity(x, y, t) {
        // t in fractional years (e.g. 2017.53). Returns {vx, vy} m/yr or null.
        if (srcSel && srcSel.value === 'fit') return sampleFit(x, y, t);
        if (srcSel && srcSel.value === 'region') return sampleRegion(x, y, t);
        var g = B.hdr.grid;
        var fx = (x - g.x0) / g.dx - 0.5;
        var fy = (g.y0_north - y) / g.dx - 0.5;
        if (fx < -1 || fy < -1 || fx > g.nx || fy > g.ny) return null;

        // Observed quarterly fields (image-pair composites) take precedence
        // where they exist in time AND space — real sub-annual motion, no
        // climatological model. Coverage holes fall through to the annual
        // path below so particles never freeze on a gap pixel.
        if (B.recent) {
            var Q = B.recent.quarters;
            if (t >= Q[0] && t <= Q[Q.length - 1]) {
                var plane0 = g.nx * g.ny;
                var qi = Math.min(Q.length - 1, Math.floor((t - Q[0]) / 0.25));
                var qf = (t - Q[qi]) / 0.25;
                var rvx = sampleGrid2(B.recent.vx.subarray(qi * plane0, (qi + 1) * plane0), g.nx, g.ny, fx, fy);
                var rvy = sampleGrid2(B.recent.vy.subarray(qi * plane0, (qi + 1) * plane0), g.nx, g.ny, fx, fy);
                if (qf > 0 && qi < Q.length - 1 && rvx === rvx && rvy === rvy) {
                    var rvx1 = sampleGrid2(B.recent.vx.subarray((qi + 1) * plane0, (qi + 2) * plane0), g.nx, g.ny, fx, fy);
                    var rvy1 = sampleGrid2(B.recent.vy.subarray((qi + 1) * plane0, (qi + 2) * plane0), g.nx, g.ny, fx, fy);
                    if (rvx1 === rvx1 && rvy1 === rvy1) {
                        rvx += qf * (rvx1 - rvx);
                        rvy += qf * (rvy1 - rvy);
                    }
                }
                if (rvx === rvx && rvy === rvy &&
                    Math.abs(rvx) <= 25000 && Math.abs(rvy) <= 25000) {
                    return { vx: rvx, vy: rvy };
                }
            }
        }

        var years = B.hdr.years, nY = years.length;
        // Annual composites are means CENTERED mid-year: field k represents
        // years[k] + 0.5. Blending between mid-years keeps the interpolation
        // honest; the timeline (tRange) ends at the last mid-year, so motion
        // terminates where the data does instead of running steady-state.
        var ty = t - (years[0] + 0.5);
        var y0 = Math.floor(ty), frac = ty - y0;
        if (y0 < 0) { y0 = 0; frac = 0; }
        if (y0 >= nY - 1) { y0 = nY - 1; frac = 0; }
        var plane = g.nx * g.ny;
        var vx0 = sampleGrid2(B.vx.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        var vy0 = sampleGrid2(B.vy.subarray(y0 * plane, (y0 + 1) * plane), g.nx, g.ny, fx, fy);
        var vx = vx0, vy = vy0;
        if (frac > 0) {
            var vx1 = sampleGrid2(B.vx.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            var vy1 = sampleGrid2(B.vy.subarray((y0 + 1) * plane, (y0 + 2) * plane), g.nx, g.ny, fx, fy);
            if (vx1 === vx1 && vy1 === vy1 && vx0 === vx0 && vy0 === vy0) {
                vx = vx0 + frac * (vx1 - vx0);
                vy = vy0 + frac * (vy1 - vy0);
            }
        }
        if (vx !== vx || vy !== vy) return null;
        // Sanity clamp: nothing on Earth flows 25 km/yr — a value like this
        // is an unmasked fill or corrupt cell; treat as no-data.
        if (Math.abs(vx) > 25000 || Math.abs(vy) > 25000) return null;
        // Seasonal superposition (climatological amp + day-of-peak phase),
        // faded outside the fit's climatology window — see seasonWeight.
        var seas = seasonWeight(B.hdr, t);
        if (seas > 0) {
            var doy = (t - Math.floor(t)) * 365.25;
            var ax = sampleGrid2(B.vxAmp, g.nx, g.ny, fx, fy);
            var ay = sampleGrid2(B.vyAmp, g.nx, g.ny, fx, fy);
            var px = sampleGrid2(B.vxPh, g.nx, g.ny, fx, fy);
            var py = sampleGrid2(B.vyPh, g.nx, g.ny, fx, fy);
            if (ax === ax && px === px) vx += seas * ax * Math.cos(2 * Math.PI * (doy - px) / 365.25);
            if (ay === ay && py === py) vy += seas * ay * Math.cos(2 * Math.PI * (doy - py) / 365.25);
        }
        return { vx: vx, vy: vy };
    }

    function onIce(x, y) {
        if (srcSel && srcSel.value === 'fit') return !!sampleFit(x, y, simT);
        if (srcSel && srcSel.value === 'region') return regionOnIce(x, y);
        var g = B.hdr.grid;
        var j = Math.round((x - g.x0) / g.dx - 0.5);
        var i = Math.round((g.y0_north - y) / g.dx - 0.5);
        if (i < 0 || i >= g.ny || j < 0 || j >= g.nx) return false;
        var v = B.ice[i * g.nx + j];
        return v === v && v > 0;
    }

    // ---------------------------------------------------------------------
    // Particles + density-managed respawn
    // ---------------------------------------------------------------------
    // Density presets are SCREEN-spacing targets (px between tracers): the
    // management-cell size follows meters-per-pixel, so ground density rises
    // as you zoom in and the screen reads roughly constant. Spawning is
    // restricted to the viewport (+margin); far-off-screen particles retire
    // to reclaim quota.
    // Screen-spacing targets in px. With the clamp fixed these now hold at
    // every zoom, so the particle count is set by viewport area / spacing^2
    // rather than by how far in you happen to be: roughly 4k / 10k / 27k
    // particles for a 1200x800 view.
    var DENSITY = {
        sparse: { px: 16, min: 1, max: 2 },
        normal: { px: 10, min: 1, max: 2 },
        dense:  { px: 6,  min: 1, max: 3 }
    };
    var GLOBAL_CAP = 150000;
    var FADE_STEPS = 30;                 // fade-out length (steps)
    var particles = [];
    var simT = null;                     // current sim time (fractional years)
    var playing = false;

    function densityCfg() { return DENSITY[densSel.value] || DENSITY.normal; }

    function manageDensity() {
        if (!B) return;
        var cfg = densityCfg();
        var g = B.hdr.grid;
        var c = map.getCenter();
        var mpp = 156543.03392 * Math.cos(c.lat * Math.PI / 180) /
                  Math.pow(2, map.getZoom());
        // Screen spacing must be constant with zoom. The old floor of g.dx
        // (the 240 m data cell) broke that above ~z11: the cell could not
        // shrink further, so spacing GREW from 4 px at z10 to 52 px at z14 —
        // "dense" got sparser the closer you looked, which is backwards.
        // The reasoning behind that floor was wrong anyway: tracers are not
        // samples of the grid, they are particles advected through a
        // bilinearly interpolated continuous field, so several per data cell
        // trace genuinely different streamlines. Subdivision is still bounded
        // (g.dx/6 = 36 particles per data cell) to keep extreme zoom sane.
        var cell = Math.min(4000, Math.max(g.dx / 6, cfg.px * mpp));

        // Viewport (+40%) in 3413, clipped to the AOI.
        var b = map.getBounds();
        var mLon = (b.getEast() - b.getWest()) * 0.2;
        var mLat = (b.getNorth() - b.getSouth()) * 0.2;
        var xs = [], ys = [];
        [[b.getWest() - mLon, b.getSouth() - mLat],
         [b.getEast() + mLon, b.getSouth() - mLat],
         [b.getWest() - mLon, b.getNorth() + mLat],
         [b.getEast() + mLon, b.getNorth() + mLat]].forEach(function (p) {
            var xy = lonLatTo3413(p[0], p[1]);
            xs.push(xy[0]); ys.push(xy[1]);
        });
        // Clip spawning to the DATA's extent — but in region mode the data is
        // the whole tile set, not this site's box. Clipping to B's AOI there
        // meant nothing could spawn outside Columbia, which is exactly how
        // "all Alaska" appeared unwired.
        var regionMode = srcSel && srcSel.value === 'region';
        var ex0, ex1, ey0, ey1;
        if (regionMode) {
            ex0 = -Infinity; ex1 = Infinity; ey0 = -Infinity; ey1 = Infinity;
        } else {
            ex0 = g.x0; ex1 = g.x0 + g.nx * g.dx;
            ey1 = g.y0_north; ey0 = g.y0_north - g.ny * g.dx;
        }
        var vx0 = Math.max(ex0, Math.min.apply(null, xs));
        var vx1 = Math.min(ex1, Math.max.apply(null, xs));
        var vy1 = Math.min(ey1, Math.max.apply(null, ys));
        var vy0 = Math.max(ey0, Math.min.apply(null, ys));
        if (vx1 <= vx0 || vy1 <= vy0) return;   // data fully off-screen

        var cx0 = Math.floor((vx0 - g.x0) / cell), cx1 = Math.floor((vx1 - g.x0) / cell);
        var cy0 = Math.floor((g.y0_north - vy1) / cell), cy1 = Math.floor((g.y0_north - vy0) / cell);
        var ncx = cx1 - cx0 + 1, ncy = cy1 - cy0 + 1;
        var counts = new Int16Array(ncx * ncy);
        var byCell = {};
        particles.forEach(function (p) {
            if (p.fade < 1) return;      // dying particles don't hold territory
            var cx = Math.floor((p.x - g.x0) / cell), cy = Math.floor((g.y0_north - p.y) / cell);
            if (cx < cx0 - 2 || cx > cx1 + 2 || cy < cy0 - 2 || cy > cy1 + 2) {
                // Far off-screen: retire so the cap serves what's visible.
                p.fade = Math.min(p.fade, 1 - 1 / FADE_STEPS);
                return;
            }
            if (cx < cx0 || cx > cx1 || cy < cy0 || cy > cy1) return;
            var k = (cy - cy0) * ncx + (cx - cx0);
            counts[k]++;
            (byCell[k] = byCell[k] || []).push(p);
        });
        Object.keys(byCell).forEach(function (k) {
            var list = byCell[k];
            // Hysteresis (max+1 before any retirement) stops the spawn/kill
            // churn that read as "points resetting" in slow areas — and
            // retire the NEWEST surplus, never the oldest: long-lived
            // particles carry the longest, most informative paths.
            if (list.length > cfg.max + 1) {
                list.sort(function (a, b) { return a.age - b.age; });
                for (var i = 0; i < list.length - cfg.max; i++) list[i].fade -= 1 / FADE_STEPS;
            }
        });
        if (particles.length >= GLOBAL_CAP) return;
        for (var cy = cy0; cy <= cy1; cy++) {
            for (var cx = cx0; cx <= cx1; cx++) {
                if (counts[(cy - cy0) * ncx + (cx - cx0)] >= cfg.min) continue;
                for (var attempt = 0; attempt < 3; attempt++) {
                    var x = g.x0 + cx * cell + Math.random() * cell;
                    var y = g.y0_north - cy * cell - Math.random() * cell;
                    if (iceCb.checked && !onIce(x, y)) continue;
                    var v0 = sampleVelocity(x, y, simT);
                    if (!v0) continue;
                    var sp0 = Math.hypot(v0.vx, v0.vy);
                    var wnd = vWindow();
                    if (sp0 < wnd[0] || sp0 > wnd[1]) continue;
                    // Seed speed from the sampled field — without it a fresh
                    // spawn drew at ~0 m/yr (near-black) until its first
                    // step: phantom "slow" dots amid fast flow, exactly
                    // where churn respawns most.
                    // Trail seeded with the birth position — without it a new
                    // tracer moves bare for up to TRAIL_DM (very visible when
                    // zoomed in) before its first vertex records.
                    particles.push({ x: x, y: y, age: 0, fade: 1,
                                     speed: Math.hypot(v0.vx, v0.vy),
                                     trail: [x, y, simT, 0],
                                     dAcc: 0, pathLen: 0,
                                     dirX: v0.vx, dirY: v0.vy });
                    break;
                }
                if (particles.length >= GLOBAL_CAP) return;
            }
        }
    }

    // Max displacement per RK2 step, meters. A global time step moves a
    // km/yr-class trunk particle several grid cells at once — integration
    // through shear margins shatters ("disintegrates") the paths. Fast
    // particles sub-step so no single step exceeds ~half a cell; slow ones
    // pay nothing.
    var DMAX_M = 130;

    // Trail model: each particle records a path vertex every TRAIL_DM meters
    // of INTEGRATED motion (honest history — a 20 m/yr particle records one
    // vertex per ~6 years, a 5 km/yr trunk one per ~9 days). Rendering fades
    // by distance-from-head (D0) times a slow age term (T0, years), so fast
    // ice keeps its long tails while slow tails persist for years instead
    // of vanishing with a frame-clock. Reverse scrubbing UNWINDS the path
    // (future vertices drop as time walks back).
    var TRAIL_DM = 120;        // m between recorded vertices (~half a cell)
    var TRAIL_TURN = 0.35;     // rad (~20°) — heading change forces a vertex
    var TRAIL_TURN_DMIN = 25;  // m — min spacing for turn-triggered vertices
    var TRAIL_DT_MAX = 0.1;    // yr — max sim-time between vertices while
                               // moving: slow complex paths (seasonal loops)
                               // resolve instead of a pivoting head-chord
    var TRAIL_MAX = 240;       // vertex cap per particle
    var TRAIL_D0 = 2200;       // m — distance-fade scale of the tail
    // Trails also need a SCREEN budget, not just a ground one. The distance
    // and age fades are in metres and years, so a tail that is 15 px long at
    // z9 becomes 475 px at z14: the view fills with overlapping ribbons and
    // the per-frame segment count explodes. Cap the drawn length in pixels so
    // a trail reads the same at every zoom and costs the same to draw.
    var TRAIL_MAX_PX = 110;
    var TRAIL_T0 = 10;         // yr — age-fade scale (slow tails persist ~decade)

    // Trail entries are 4 floats: x, y, t, cumulative path length at record.
    // Distance-triggered AND turn-triggered (a straight head→vertex chord
    // pivots visibly through curved motion; turning records early so the
    // rendered polyline follows the real path). Cumulative distance keeps
    // the alpha fade honest with variable vertex spacing.
    function recordVertex(p, t) {
        p.trail.push(p.x, p.y, t, p.pathLen);
        if (p.trail.length > TRAIL_MAX * 4) p.trail.splice(0, 60 * 4);
    }

    // Fade progression is DISPLAY-time (per rendered frame, see
    // progressFades), not simulation-time: a paused map must still dissolve
    // over-dense patches (zoom-out leftovers, keyframe restores). Dying
    // particles freeze in place and skip advection.
    function progressFades() {
        for (var n = particles.length - 1; n >= 0; n--) {
            if (particles[n].fade < 1) {
                particles[n].fade -= 1 / FADE_STEPS;
                if (particles[n].fade <= 0) particles.splice(n, 1);
            }
        }
    }

    function stepParticles(dt) {
        for (var n = particles.length - 1; n >= 0; n--) {
            var p = particles[n];
            p.age += dt;
            if (p.fade < 1) continue;   // dying: frozen, progressFades owns it
            var v1 = sampleVelocity(p.x, p.y, simT);
            if (!v1) { p.fade = Math.min(p.fade, 1 - 1 / FADE_STEPS); continue; }
            var sp1 = Math.hypot(v1.vx, v1.vy);
            var nSub = Math.min(12, Math.max(1, Math.ceil(sp1 * Math.abs(dt) / DMAX_M)));
            var h = dt / nSub, t = simT, v = v1;
            for (var s = 0; s < nSub; s++) {
                var mx = p.x + v.vx * h / 2, my = p.y + v.vy * h / 2;
                var v2 = sampleVelocity(mx, my, t + h / 2) || v;
                var ddx = v2.vx * h, ddy = v2.vy * h;
                p.x += ddx;
                p.y += ddy;
                t += h;
                p.speed = Math.hypot(v2.vx, v2.vy);
                var stepD = Math.hypot(ddx, ddy);
                p.dAcc += stepD;
                p.pathLen += stepD;
                var doRecord = p.dAcc >= TRAIL_DM;
                if (!doRecord && p.dAcc >= TRAIL_TURN_DMIN && (p.dirX || p.dirY)) {
                    var dot = (v2.vx * p.dirX + v2.vy * p.dirY) /
                        ((Math.hypot(v2.vx, v2.vy) * Math.hypot(p.dirX, p.dirY)) + 1e-9);
                    doRecord = Math.acos(Math.max(-1, Math.min(1, dot))) > TRAIL_TURN;
                }
                if (!doRecord && p.dAcc >= 5 && h > 0) {
                    // Time trigger: any real motion records at least every
                    // TRAIL_DT_MAX sim-years, so sub-TRAIL_DM wander (slow
                    // seasonal loops) draws as its actual path.
                    var lastVT = p.trail.length
                        ? p.trail[p.trail.length - 2] : -Infinity;
                    doRecord = (t - lastVT) > TRAIL_DT_MAX;
                }
                if (doRecord) {
                    recordVertex(p, t);
                    p.dAcc = 0;
                    p.dirX = v2.vx; p.dirY = v2.vy;
                }
                if (s < nSub - 1) {
                    v = sampleVelocity(p.x, p.y, t);
                    if (!v) break;
                }
            }
            if (dt < 0 && p.trail.length) {
                // Walking backwards: drop vertices from the (now-)future.
                var L = p.trail.length;
                while (L >= 4 && p.trail[L - 2] > simT) L -= 4;
                if (L < p.trail.length) p.trail.length = L;
            }
            if (iceCb.checked && !onIce(p.x, p.y)) {
                p.fade = Math.min(p.fade, 1 - 1 / FADE_STEPS);
            }
            // NOTE: deliberately no speed-based retirement here. Retiring on
            // INSTANTANEOUS speed sunsets exactly the particles worth
            // watching: slow ice has a seasonal cycle that crosses any
            // threshold twice a year, so slow tracers flickered out and
            // respawned while fast ones — whose seasonal swing never
            // approaches the threshold — ran on undisturbed. The speed
            // window belongs at SPAWN time only; changing it clears the
            // population, so the visible set still matches the control.
        }
    }

    // ---------------------------------------------------------------------
    // Rendering — canvas over the map; trails via destination-out fade.
    // ---------------------------------------------------------------------
    var canvas = document.getElementById('tracer-canvas');
    var ctx = canvas.getContext('2d');

    function sizeCanvas() {
        var r = canvas.parentNode.getBoundingClientRect();
        var dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(r.width * dpr);
        canvas.height = Math.round(r.height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    sizeCanvas();
    window.addEventListener('resize', sizeCanvas);
    map.on('resize', sizeCanvas);
    // Trails are re-rendered from recorded paths every frame, so they
    // survive pan/zoom (no accumulation buffer to smear). After a move
    // settles, re-manage so newly visible ice seeds and the zoom-adaptive
    // density retargets even while paused.
    map.on('moveend', function () {
        if (typeof syncVLabel === 'function') syncVLabel();
        if (srcSel && srcSel.value === 'region') ensureRegionTiles();
        if (B && simT != null) manageDensity();
        writeHash();
    });

    // Hash writer — same replaceState pattern as the inventory map. simT is
    // written only when settled (no churn during play/catch-up; play pause,
    // convergence, and discrete controls all call this).
    function writeHash() {
        if (!window.LSHash || !map) return;
        var c = map.getCenter();
        var ov = {};
        OVERLAYS.forEach(function (o) {
            var st = _ovState[o.id];
            if (st.on) {
                ov[o.id] = { left: true, opLeft: st.op,
                             smooth: !!(o.variant && o.variant.get()) };
            }
        });
        var h = window.LSHash.encode({
            zoom: map.getZoom(), lat: c.lat, lon: c.lng,
            base: _currentBasemap !== DEFAULT_BASEMAP ? _currentBasemap : null,
            ov: Object.keys(ov).length ? ov : null,
            extras: {
                site: siteSel.value || null,
                t: (simT != null && !playing && targetT == null)
                    ? simT.toFixed(2) : null
            }
        });
        if (location.hash !== h) history.replaceState(null, '', h);
    }
    // While the camera is MOVING, draw inside MapLibre's render pass — the
    // overlay then projects with exactly the transform the basemap frame
    // used. A free-running rAF samples the camera at a slightly different
    // moment than the map's own render, which showed as sub-frame "waver"
    // during drags. (The rAF loop draws only when the camera is still.)
    map.on('render', function () { if (B && map.isMoving()) draw(); });

    // Speed color: same log ramp as the ice-v tiles (itslive_color_v.txt).
    var SPEED_RAMP = [
        [0, 8, 29, 88], [1, 34, 94, 168], [3, 29, 145, 192], [10, 65, 182, 196],
        [30, 127, 205, 187], [100, 199, 233, 180], [300, 255, 237, 160],
        [1000, 254, 178, 76], [3000, 240, 59, 32], [19200, 189, 0, 38]
    ];
    function speedColor(v) {
        var lo = SPEED_RAMP[0], hi = SPEED_RAMP[SPEED_RAMP.length - 1], i;
        for (i = 0; i < SPEED_RAMP.length - 1; i++) {
            if (v >= SPEED_RAMP[i][0] && v <= SPEED_RAMP[i + 1][0]) {
                lo = SPEED_RAMP[i]; hi = SPEED_RAMP[i + 1]; break;
            }
        }
        var t = hi[0] === lo[0] ? 0 :
            (Math.log(Math.max(v, 0.01) + 1) - Math.log(lo[0] + 1)) /
            (Math.log(hi[0] + 1) - Math.log(lo[0] + 1));
        t = Math.min(1, Math.max(0, t));
        return 'rgb(' + Math.round(lo[1] + t * (hi[1] - lo[1])) + ',' +
                        Math.round(lo[2] + t * (hi[2] - lo[2])) + ',' +
                        Math.round(lo[3] + t * (hi[3] - lo[3])) + ')';
    }
    // Quantized LUT (64 log buckets, 0.5–20000 m/yr) — at 10k+ particles,
    // building an rgb() string per particle per frame is real overhead.
    var _COLOR_N = 64, _COLOR_LUT = [];
    (function () {
        for (var k = 0; k < _COLOR_N; k++) {
            var v = Math.exp(Math.log(0.5) + (k / (_COLOR_N - 1)) *
                             (Math.log(20000) - Math.log(0.5)));
            _COLOR_LUT.push(speedColor(v));
        }
    })();
    function speedColorQ(v) {
        var k = Math.round((Math.log(Math.max(v, 0.5)) - Math.log(0.5)) /
                           (Math.log(20000) - Math.log(0.5)) * (_COLOR_N - 1));
        return _COLOR_LUT[Math.min(_COLOR_N - 1, Math.max(0, k))];
    }

    function draw() {
        var w = canvas.getBoundingClientRect().width, h = canvas.getBoundingClientRect().height;
        ctx.clearRect(0, 0, w, h);
        if (!cbTracers.checked || !B) return;

        // Zoom-aware vertex stride: recorded spacing is TRAIL_DM meters;
        // render only every k-th vertex so on-screen spacing stays ≈ the
        // tracer size (never denser than needed, never sparser than the dot).
        var c = map.getCenter();
        var mpp = 156543.03392 * Math.cos(c.lat * Math.PI / 180) /
                  Math.pow(2, map.getZoom());
        var vertPx = TRAIL_DM / mpp;
        var stride = Math.max(1, Math.round(2.4 / vertPx));
        var drawTrails = cbTrails.checked;
        ctx.lineWidth = 2.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        for (var n = 0; n < particles.length; n++) {
            var p = particles[n];
            var ll = ll3413ToLonLat(p.x, p.y);
            var pt = map.project(ll);
            var headVisible = !(pt.x < -10 || pt.y < -10 || pt.x > w + 10 || pt.y > h + 10);

            if (drawTrails && p.trail.length) {
                // Continuous tail: stroked polyline from the head back through
                // the recorded vertices (alpha-banded so one stroke covers a
                // run of similar opacity). Lines, not dots — recorded spacing
                // can exceed the dot size at high zoom, and a tail must never
                // be gappier than the tracer.
                ctx.strokeStyle = speedColorQ(p.speed || 0);
                var nv = p.trail.length / 4;
                var prevX = pt.x, prevY = pt.y;
                var band = -1, open = false, drawn = 0, pxLen = 0;
                var budget = TRAIL_MAX_PX * _q;
                for (var k = nv - 1; k >= 0; k -= stride) {
                    var d = p.pathLen - p.trail[k * 4 + 3];  // true path distance from head
                    var ageT = simT - p.trail[k * 4 + 2];
                    var a = p.fade * Math.exp(-d / TRAIL_D0) *
                            Math.exp(-Math.max(0, ageT) / TRAIL_T0);
                    if (a < 0.035) break;          // monotone decreasing → done
                    var tll = ll3413ToLonLat(p.trail[k * 4], p.trail[k * 4 + 1]);
                    var tp = map.project(tll);
                    var b = Math.min(5, (a * 6) | 0);
                    if (b !== band) {
                        if (open) ctx.stroke();
                        ctx.globalAlpha = ((b + 0.5) / 6) * 0.85;
                        ctx.beginPath();
                        ctx.moveTo(prevX, prevY);
                        band = b; open = true;
                    }
                    ctx.lineTo(tp.x, tp.y);
                    pxLen += Math.hypot(tp.x - prevX, tp.y - prevY);
                    prevX = tp.x; prevY = tp.y;
                    if (pxLen > budget) break;     // screen-length budget
                    if (++drawn >= 60) break;      // per-particle render cap
                }
                if (open) ctx.stroke();
            }
            if (headVisible) {
                ctx.globalAlpha = Math.max(0, Math.min(1, p.fade));
                ctx.fillStyle = speedColorQ(p.speed || 0);
                ctx.fillRect(pt.x - 1.3, pt.y - 1.3, 2.8, 2.8);
            }
        }
        ctx.globalAlpha = 1;
    }

    // ---------------------------------------------------------------------
    // Time control + animation loop
    // ---------------------------------------------------------------------
    var slider = document.getElementById('gl-time');
    var dateEl = document.getElementById('gl-date');
    var playBtn = document.getElementById('gl-play');
    var MAX_STEP = 0.03;                 // yr — sub-steps keep RK2 honest
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // Playback speed select (QuickTime-style). Values = sim-years per second.
    var speedSel = document.createElement('select');
    [['0.25', '¼×'], ['0.5', '½×'], ['1', '1×'], ['2', '2×'], ['4', '4×']]
        .forEach(function (o) {
            var el = document.createElement('option');
            el.value = o[0]; el.textContent = o[1];
            if (o[0] === '1') el.selected = true;
            speedSel.appendChild(el);
        });
    speedSel.title = 'Playback speed (simulated years per second)';
    speedSel.addEventListener('change', function () { if (typeof syncVLabel === 'function') syncVLabel(); });
    speedSel.style.cssText = 'font-size:11px;';
    playBtn.parentNode.insertBefore(speedSel, playBtn.nextSibling);

    function tRange() {
        // Mid-year of the first field to mid-year of the last — the span the
        // annual composites actually constrain. No steady-state coasting
        // beyond the end of data. In region mode take the span from a loaded
        // tile, so playback is not truncated by whichever site happens to be
        // selected in the (camera-only) site menu.
        var ys = B.hdr.years;
        if (srcSel && srcSel.value === 'region' && REG.loaded.size) {
            var first = REG.loaded.values().next().value;
            if (first && first.hdr.years) ys = first.hdr.years;
        }
        return [ys[0] + 0.5, ys[ys.length - 1] + 0.5];
    }
    // While the pointer is DOWN the thumb belongs to the user (it sets the
    // target). On release the thumb snaps to the computed simT and visibly
    // crawls toward the target as the physics catches up — the lag IS the
    // progress indicator (amber tint while behind).
    var scrubbing = false;
    function beginScrub() {
        if (scrubbing) return;
        scrubbing = true;
        // Thin the working set for the duration of the gesture. Integration
        // cost is linear in particle count, so this is what lets the flow
        // advect continuously under a fast drag instead of lurching between
        // cached snapshots. Keeps the OLDEST (longest-trailed) particles —
        // they carry the most legible motion — and parks the remainder.
        if (particles.length > SCRUB_CAP) {
            particles.sort(function (a, b) { return b.age - a.age; });
            _parked = particles.slice(SCRUB_CAP);
            particles = particles.slice(0, SCRUB_CAP);
        }
    }
    function endScrub() {
        if (!scrubbing) return;
        scrubbing = false;
        if (_parked) {
            // Parked particles are stale by however far time moved, so let
            // density management repopulate instead of returning them to
            // wrong positions.
            _parked = null;
            manageDensity();
        }
    }
    slider.addEventListener('pointerdown', beginScrub);
    slider.addEventListener('pointerup', endScrub);
    slider.addEventListener('pointercancel', endScrub);
    slider.addEventListener('change', endScrub);

    function setSimT(t, fromSlider) {
        var r = tRange();
        simT = Math.min(r[1] - 0.001, Math.max(r[0], t));
        if (!fromSlider && !scrubbing) {
            slider.value = String((simT - r[0]) / (r[1] - r[0]));
        }
        var lagging = targetT != null && Math.abs(targetT - simT) > 0.02;
        slider.style.accentColor = lagging ? '#c9971c' : '';
        // Ghost marker at the COMPUTED time — the visible lag indicator
        // while the thumb is user-held (accent-color doesn't repaint
        // reliably mid-drag, and the held thumb can't crawl).
        var lagEl = document.getElementById('gl-lagmark');
        if (lagEl) {
            if (lagging) {
                lagEl.style.left = (((simT - r[0]) / (r[1] - r[0])) * 100) + '%';
                lagEl.style.display = 'block';
            } else {
                lagEl.style.display = 'none';
            }
        }
        var yr = Math.floor(simT);
        var mo = Math.min(11, Math.floor((simT - yr) * 12));
        dateEl.textContent = MONTHS[mo] + ' ' + yr + (lagging ? ' …' : '');
    }

    // Signed, sub-stepped advance — the one path through which time moves,
    // whether from play, slider scrubbing, or arrow keys. Negative dt runs
    // the flow BACKWARDS (RK2 handles it), so wiping the slider drags the
    // ice back and forth — the trails paint the motion either way.
    var stepsSinceManage = 0;
    function advanceBy(dtSim) {
        if (!B) return;
        while (Math.abs(dtSim) > 1e-9) {
            var dt = Math.max(-MAX_STEP, Math.min(MAX_STEP, dtSim));
            if (cbTracers.checked) stepParticles(dt);
            setSimT(simT + dt);
            dtSim -= dt;
            if (++stepsSinceManage >= 4) { manageDensity(); stepsSinceManage = 0; }
            _kfMaybeCapture();
        }
    }

    // -----------------------------------------------------------------
    // Keyframe cache + budgeted catch-up: scrubbing never teleports and
    // never blocks. The slider sets targetT; the frame loop advances the
    // physics toward it inside a per-frame time budget (the date display
    // lags the thumb honestly while computing). Snapshots of the particle
    // SYSTEM (packed: position/age/fade/speed + the bright first vertices
    // of each tail) are taken every KF_DT of simulated time; a long throw
    // restores the nearest keyframe and advects the remainder, so repeat
    // back-and-forth scrubs come mostly from cache. Keyframes hold
    // PHYSICAL state, so they stay valid across zoom/density changes —
    // manageDensity refills for the current view after a restore.
    // -----------------------------------------------------------------
    var targetT = null;
    // Keyframe geometry is a memory trade, and the trails were eating it:
    // 12 stored vertices are 48 of the 54 floats per particle, so the cache
    // could only hold 7.5-12 yr of a 41 yr timeline and a scrub left the
    // cached window almost immediately — which is why repeat passes were no
    // faster. Keeping a 3-vertex stub instead (trails regrow within a second
    // of resuming) buys ~3x the keyframes, and halving KF_DT puts one within
    // 0.125 yr of any target, so a restore lands somewhere the population has
    // barely changed: cheap AND visually small.
    var KF_DT = 0.25;             // sim-years between keyframes
    var KF_TRAIL = 3;             // stored tail vertices (stub; regrows)
    var _kf = new Map();          // key: Math.round(t / KF_DT) → {t, n, data}
    var _kfStride = 6 + KF_TRAIL * 4;   // x,y,age,fade,speed,pathLen + 4/vertex

    function _kfMax() {
        var bytes = Math.max(1, particles.length) * _kfStride * 4;
        return Math.max(8, Math.min(160, Math.floor(90e6 / bytes)));
    }
    function _kfMaybeCapture() {
        var key = Math.round(simT / KF_DT);
        if (_kf.has(key) || !particles.length) return;
        var data = new Float32Array(particles.length * _kfStride);
        for (var i = 0; i < particles.length; i++) {
            var p = particles[i], o = i * _kfStride;
            data[o] = p.x; data[o + 1] = p.y; data[o + 2] = p.age;
            data[o + 3] = p.fade; data[o + 4] = p.speed || 0;
            data[o + 5] = p.pathLen || 0;
            var nv = p.trail.length / 4;
            var take = Math.min(KF_TRAIL, nv);
            for (var k = 0; k < take; k++) {
                var src = (nv - take + k) * 4;
                var d = o + 6 + k * 4;
                data[d] = p.trail[src]; data[d + 1] = p.trail[src + 1];
                data[d + 2] = p.trail[src + 2]; data[d + 3] = p.trail[src + 3];
            }
            for (var k2 = take; k2 < KF_TRAIL; k2++) data[o + 6 + k2 * 4 + 2] = NaN;
        }
        _kf.set(key, { t: simT, n: particles.length, data: data });
        // Evict the keyframe farthest from now once over budget.
        var max = _kfMax();
        while (_kf.size > max) {
            var worstK = null, worstD = -1;
            _kf.forEach(function (v, k) {
                var dd = Math.abs(v.t - simT);
                if (dd > worstD) { worstD = dd; worstK = k; }
            });
            _kf.delete(worstK);
        }
    }
    function _kfRestore(kf) {
        particles = [];
        for (var i = 0; i < kf.n; i++) {
            var o = i * _kfStride;
            var p = { x: kf.data[o], y: kf.data[o + 1], age: kf.data[o + 2],
                      fade: kf.data[o + 3], speed: kf.data[o + 4],
                      pathLen: kf.data[o + 5],
                      trail: [], dAcc: 0, dirX: 0, dirY: 0 };
            for (var k = 0; k < KF_TRAIL; k++) {
                var d = o + 6 + k * 4;
                if (kf.data[d + 2] === kf.data[d + 2]) {   // t not NaN
                    p.trail.push(kf.data[d], kf.data[d + 1],
                                 kf.data[d + 2], kf.data[d + 3]);
                }
            }
            // Same birth-seed rule as spawning: no bare tracers post-restore.
            if (!p.trail.length) p.trail.push(p.x, p.y, kf.t, p.pathLen);
            particles.push(p);
        }
        setSimT(kf.t);
    }
    function _kfNearest(t) {
        var best = null;
        _kf.forEach(function (v) {
            if (best === null || Math.abs(v.t - t) < Math.abs(best.t - t)) best = v;
        });
        return best;
    }

    slider.addEventListener('input', function () {
        if (!B) return;
        var r = tRange();
        // Clamp to the SAME ceiling setSimT enforces (r[1] - 0.001):
        // an exact-r[1] target could never be reached, so the catch-up
        // loop integrated the residual every frame — perpetual motion
        // whenever the thumb sat at the end stop.
        targetT = Math.min(r[1] - 0.001,
                           Math.max(r[0], r[0] + (+slider.value) * (r[1] - r[0])));
    });
    playBtn.addEventListener('click', function () {
        if (!playing && B && simT >= tRange()[1] - 0.01) {
            // Play pressed at the end — QuickTime behavior: restart.
            setSimT(tRange()[0]);
            particles = [];
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
        playing = !playing;
        playBtn.textContent = playing ? '❚❚' : '▶';
        if (!playing) writeHash();   // pausing settles t=
    });

    // Arrow keys: one month per press (Shift = one year), either direction —
    // expressed as catch-up targets so repeated presses queue smoothly.
    document.addEventListener('keydown', function (e) {
        if (!B) return;
        var tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
        var step = e.shiftKey ? 1 : 1 / 12;
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            var base = targetT != null ? targetT : simT;
            var r = tRange();
            targetT = Math.min(r[1] - 0.001, Math.max(r[0],
                base + (e.key === 'ArrowRight' ? step : -step)));
            // No thumb jump — the thumb crawls with simT toward the target.
            e.preventDefault();
        } else if (e.key === ' ') { playBtn.click(); e.preventDefault(); }
    });

    // Adaptive quality: if frames get expensive, shorten trails before doing
    // anything visible to the particles themselves — losing tail length reads
    // as a style change, losing particles reads as data disappearing.
    var _q = 1;
    // With dense keyframes a restore lands close in time, so it no longer
    // needs to be rare — only non-repetitive. Thresholds back down to keep
    // scrubbing responsive (the previous 3/8 yr made the cache unusable and
    // forced every drag to integrate).
    // Restores are the only way to scrub fast (integration manages only
    // ~0.06 sim-yr per frame at 10k particles), but each one swaps the
    // population and reads as a jump. Resolution: never restore DURING a
    // drag — instead make the drag cheap by thinning the working set, so
    // integration alone keeps up and motion stays continuous. Restores then
    // happen only on release, where a single settle is expected anyway.
    var RESTORE_GAIN = 0.6;        // yr of integration a restore must save
    var RESTORE_COOLDOWN_MS = 220;
    var SCRUB_CAP = 2200;          // particles kept while dragging
    var _parked = null;            // the rest, returned on release
    var _lastRestore = -1e9;
    var lastFrame = null;
    var CATCHUP_BUDGET_MS = 9;    // physics time per frame while catching up
    function frame(ts) {
        requestAnimationFrame(frame);
        if (!B) return;
        var dtReal = lastFrame == null ? 0 : Math.min(0.1, (ts - lastFrame) / 1000);
        if (lastFrame != null) {
            if (dtReal > 1 / 28) _q = Math.max(0.3, _q * 0.94);
            else if (dtReal < 1 / 55) _q = Math.min(1, _q * 1.03);
        }
        lastFrame = ts;
        if (targetT != null) {
            // A long throw with a nearer keyframe: jump the physics there
            // first, then integrate only the remainder.
            // A restore SWAPS the live population for the snapshot's, so it
            // always reads as "the points reset". It is a shortcut, not a
            // correctness requirement — integrating is always valid, just
            // slower. So it must be RARE and never surprise a user who is
            // mid-gesture:
            //   * it has to save real work (> RESTORE_GAIN years),
            //   * while the pointer is down the bar is much higher, because
            //     a swap during a drag interrupts motion the user is
            //     actively reading,
            //   * and never twice in quick succession, which is what made
            //     rapid scrubbing churn.
            var kf = scrubbing ? null : _kfNearest(targetT);
            var gain = kf ? Math.abs(simT - targetT) - Math.abs(kf.t - targetT) : 0;
            var nowMs = (typeof ts === 'number') ? ts : 0;
            if (kf && gain > RESTORE_GAIN && (nowMs - _lastRestore) > RESTORE_COOLDOWN_MS) {
                _lastRestore = nowMs;
                _kfRestore(kf);
                manageDensity();   // refill for the current zoom/view
            }
            var t0 = performance.now();
            var budgetMs = scrubbing ? CATCHUP_BUDGET_MS * 1.6 : CATCHUP_BUDGET_MS;
            while (Math.abs(targetT - simT) > 5e-4 &&
                   performance.now() - t0 < budgetMs) {
                advanceBy(Math.max(-0.06, Math.min(0.06, targetT - simT)));
            }
            if (Math.abs(targetT - simT) <= 5e-4) {
                targetT = null;
                setSimT(simT);     // re-sync thumb + drop the ellipsis
                writeHash();       // settled time → persist t=
            }
        } else if (playing) {
            var r = tRange();
            if (simT >= r[1] - 0.002) {
                // End of the record: STOP (no wrap into the sparse 1980s).
                playing = false;
                playBtn.textContent = '▶';
            } else {
                advanceBy(Math.min(dtReal * parseFloat(speedSel.value), r[1] - 0.001 - simT));
            }
        } else if (cbTracers.checked && B && particles.length === 0 && simT != null) {
            manageDensity();               // paused with no particles → seed
        }
        progressFades();                   // display-time: fades run even paused
        if (!map.isMoving()) draw();       // moving → the map's render event draws
    }
    requestAnimationFrame(frame);

    // ---------------------------------------------------------------------
    // Site switching
    // ---------------------------------------------------------------------
    function activateSite(slug) {
        var entry = (CFG.catalog || []).filter(function (c) { return c.slug === slug; })[0];
        B = null; particles = [];
        _kf.clear();                 // keyframes belong to the previous site
        targetT = null;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        dateEl.textContent = 'loading…';
        loadBundle(slug).then(function (bundle) {
            B = bundle;
            // First load honors a hash-restored time; later loads start at
            // the range start. Clamp handles stale t= values gracefully.
            var t0 = tRange()[0];
            if (_hashT != null && isFinite(_hashT)) {
                t0 = Math.min(tRange()[1] - 0.001, Math.max(t0, _hashT));
                _hashT = null;   // consume once
            }
            setSimT(t0);
            particles = [];
            manageDensity();
            // A hash-restored view outranks the site's default framing —
            // but only on the initial load (consume once).
            if (_hashHadView) {
                _hashHadView = false;
            } else if (entry && entry.center) {
                map.jumpTo({ center: entry.center, zoom: entry.zoom || 10 });
            }
            writeHash();
        }).catch(function (e) {
            dateEl.textContent = 'no data';
            console.error('tracer bundle load failed:', e);
        });
    }
    siteSel.addEventListener('change', function () {
        if (srcSel && srcSel.value === 'region') {
            // Region mode: the menu is a "fly there" control, not a data
            // switch — the field already covers everywhere.
            var e = (CFG.catalog || []).filter(function (c) { return c.slug === siteSel.value; })[0];
            if (e && e.center) map.jumpTo({ center: e.center, zoom: e.zoom || 10 });
            return;
        }
        activateSite(siteSel.value);
    });
    var _startSite = (_hash.extras.site &&
                      (CFG.catalog || []).some(function (c) { return c.slug === _hash.extras.site; }))
        ? _hash.extras.site
        : (CFG.catalog && CFG.catalog.length ? CFG.catalog[0].slug : null);
    if (_startSite) {
        siteSel.value = _startSite;
        activateSite(_startSite);
    }
    // Bootstrap the SELECTED field source. Whichever option is first in the
    // menu is the default, so this must not depend on the user touching it.
    if (srcSel.value === 'region') loadRegionManifest();
    else if (srcSel.value === 'fit') loadFitModel();
})();
