/* ext_explore.js — the Inventory tab's "Showing" switch: explore an OUTSIDE inventory
 * (DGGS, the Canadian database) with its own filters and symbology, in place of ours.
 *
 * Hig, 2026-11-16: "on the Inventory tab you can switch to one of the other inventories,
 * and there's a similar set of filter options ... a symbology that was unique to a
 * dataset and would articulate how it divided different landslides." Mock-up approved
 * the same day; decisions recorded in docs/EXTERNAL_INVENTORIES.md.
 *
 * What it does
 *   - The source's primary layer ships whole (api/external/<sid>/explore/, ~0.4–0.6 MB
 *     gzipped), so filters, counts and colours are local.
 *   - Colour = PROCESS FAMILY, one colour per family across every outside source
 *     (LSColors.EXT_FAMILIES; the type -> family map is the source's `explore` block in
 *     inventory/external.py). A second channel per source: ring / size / fade.
 *   - Filters: families (with the source's own types under each), presets, the source's
 *     fields, and the editor's review (unreviewed / promoted / their categories).
 *   - Our own records stay on the map as context unless the box says otherwise.
 *   - Click: the same popup, Review box and Promote button as the overlay rows
 *     (external_inv.js, through ctx.showRecord). Promote hands the tab back to our own
 *     records first (the `ls:ext-promote` event).
 *
 * State travels in the URL as extras: xs=<source id>, xf=<filters>, xc=<colour-by>
 * (grammar: encodeState below; additive, inventory-only), and per source in
 * localStorage so switching away and back keeps a focus.
 *
 * map.js calls window.LSExtExplore.init(ctx) once and uses: spec(), applyHash(s),
 * replay(m) (after the landslide layers are (re)built on a map).
 */
(function () {
    'use strict';

    window.LSExtExplore = { init: function (ctx) {
        var API_BASE = ctx.API_BASE, map = ctx.map, _swipe = ctx._swipe, esc = ctx.esc;
        var sel = document.getElementById('xex-ds');
        var panel = document.getElementById('xex-panel');
        var oursList = document.querySelector('.inv-panel[data-panel="inventory"] > ul.inv-nav');
        var NOOP = { spec: function () { return null; }, applyHash: function () {}, replay: function () {} };
        if (!sel || !panel || !window.LSColors || !LSColors.EXT_FAMILIES) return NOOP;

        var FAMC = LSColors.EXT_FAMILIES, CATC = LSColors.EXT_CATEGORICAL, BLANK = LSColors.EXT_BLANK;
        var SRC = 'xex-src', LYR = 'xex-pts', LS_KEY = 'ls_xex';
        var cache = {};        // sid -> prepared dataset
        var D = null;          // the dataset on show (null = ours)
        var S = blank();       // its filter state
        var C = null;          // S compiled against D
        var loadSeq = 0;
        var pending = null;    // {sid, xf, xc} while a dataset loads, so the URL keeps it
        var inView = null;
        var fcCache = { colour: null, feats: null };
        var UI = {};           // which family / field lists are open (not filter state)

        function blank() { return { fams: null, subOff: {}, facet: {}, rv: null, colour: 'fam', ctx: true, limit: false, preset: 0 }; }
        function fmt(n) { return n.toLocaleString('en-US'); }
        function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }

        // ------------------------------------------------------------ dataset prep
        function prep(P) {
            var n = P.n, fold = !!P.fold_spelling;
            var d = { P: P, sid: P.source, n: n, lon: P.cols.lon, lat: P.cols.lat, ext: P.cols.ext_id,
                      name: P.cols.name, type: P.cols.type, types: P.dicts.type, fold: fold, facets: [], fk: {} };
            d.vkey = function (label) { return label == null ? '' : (fold ? String(label).trim().toLowerCase() : String(label)); };
            d.famKeys = P.families.map(function (f) { return f.k; });
            var oth = d.famKeys.indexOf('OTH');
            d.fam = new Int8Array(n);
            d.subCount = d.famKeys.map(function () { return {}; });
            for (var i = 0; i < n; i++) {
                var t = d.type[i];
                var fi = t < 0 ? oth : P.type_family[t];
                d.fam[i] = fi;
                d.subCount[fi][t] = (d.subCount[fi][t] || 0) + 1;
            }
            P.facets.forEach(function (f) {
                var dict = P.dicts[f.key], codes = P.cols[f.key], tot = new Array(dict.length).fill(0), nb = 0;
                for (var j = 0; j < n; j++) { if (codes[j] < 0) nb++; else tot[codes[j]]++; }
                var order = dict.map(function (_v, k) { return k; });
                if (f.sort === 'num') order.sort(function (a, b) { return (parseFloat(dict[a]) || 0) - (parseFloat(dict[b]) || 0); });
                else if (f.order) order.sort(function (a, b) { return ord(f.order, d.vkey(dict[a])) - ord(f.order, d.vkey(dict[b])) || tot[b] - tot[a]; });
                else order.sort(function (a, b) { return tot[b] - tot[a]; });
                if (nb) order.push(-1);
                var F = { key: f.key, label: f.label, dict: dict, codes: codes, order: order, total: tot, blanks: nb,
                          variants: (P.variants && P.variants[f.key]) || {} };
                d.facets.push(F); d.fk[f.key] = F;
            });
            // Second visual channel: per dict code, the value it implies.
            function chan(ch, fn) {
                if (!ch || !ch.key || !d.fk[ch.key]) return null;
                var F = d.fk[ch.key];
                return F.dict.map(function (v) { return fn(d.vkey(v)); });
            }
            d.ring = chan(P.ring, function (v) { return v === String(P.ring.on).toLowerCase() ? 1 : 0; });
            d.size = chan(P.size, function (v) { return (P.size.scale || {})[v] || 1; });
            d.fade = chan(P.fade, function (v) { var a = (P.fade.alpha || {})[v]; return a == null ? 0.9 : a; });
            d.byExt = {};
            for (var k = 0; k < n; k++) (d.byExt[d.ext[k]] = d.byExt[d.ext[k]] || []).push(k);
            d.rv = null;           // per record: 'u' | 'p' | 'c<id>' (editors, once loaded)
            d.cats = [];
            return d;
        }
        function ord(arr, v) { var i = arr.indexOf(v); return i < 0 ? 99 : i; }

        function loadReviews(d) {
            if (!window._isInventoryEditor) return Promise.resolve();
            return fetch(API_BASE + 'api/external/' + d.sid + '/' + d.P.layer + '/reviews/', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (j) {
                    if (!j) return;
                    d.cats = j.categories || [];
                    var rv = new Array(d.n);
                    for (var i = 0; i < d.n; i++) {
                        var e = d.ext[i];
                        rv[i] = j.promoted[e] ? 'p' : (j.reviews[e] != null ? 'c' + j.reviews[e] : 'u');
                    }
                    d.rv = rv;
                })
                .catch(function (e) { console.error('explorer reviews failed', e); });
        }

        function load(sid) {
            if (cache[sid]) return Promise.resolve(cache[sid]);
            return fetch(API_BASE + 'api/external/' + sid + '/explore/')
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(function (P) { var d = prep(P); cache[sid] = d; return loadReviews(d).then(function () { return d; }); });
        }

        // ------------------------------------------------------------ filtering
        function compile() {
            var d = D;
            var famOK = d.famKeys.map(function (k) { return !S.fams || S.fams.indexOf(k) >= 0; });
            var typeOK = d.types.map(function (t) { return !S.subOff[d.vkey(t)]; });
            var facetOK = {};
            d.facets.forEach(function (F) {
                // ✓ = only these (any of them; one value per record), ✗ = not these.
                var fs = S.facet[F.key];
                if (!fs || (!fs.m.length && !fs.x.length)) return;
                var ok = function (vk) { return (!fs.m.length || fs.m.indexOf(vk) >= 0) && fs.x.indexOf(vk) < 0; };
                facetOK[F.key] = { ok: F.dict.map(function (v) { return ok(d.vkey(v)); }), blank: ok('') };
            });
            var rvOK = null;
            if (S.rv && d.rv) { rvOK = {}; S.rv.forEach(function (c) { rvOK[c] = 1; }); }
            C = { famOK: famOK, typeOK: typeOK, facetOK: facetOK, rvOK: rvOK };
        }
        function passes(i, skip) {
            var d = D;
            if (skip !== 'fam') {
                if (!C.famOK[d.fam[i]]) return false;
                var t = d.type[i];
                if (t >= 0 && !C.typeOK[t]) return false;
            }
            for (var k in C.facetOK) {
                if (k === skip) continue;
                var fo = C.facetOK[k], c = d.fk[k].codes[i];
                if (c < 0 ? !fo.blank : !fo.ok[c]) return false;
            }
            if (C.rvOK && skip !== 'rv' && !C.rvOK[d.rv[i]]) return false;
            return true;
        }
        function visible(i) {
            if (!S.limit || !inView) return true;
            var x = D.lon[i], y = D.lat[i];
            return x >= inView[0] && x <= inView[2] && y >= inView[1] && y <= inView[3];
        }

        // ------------------------------------------------------------ symbology
        function colourOf(i) {
            if (S.colour === 'fam') return (FAMC[D.famKeys[D.fam[i]]] || FAMC.OTH).col;
            var F = D.fk[S.colour];
            if (!F) return BLANK;
            var c = F.codes[i];
            if (c < 0) return BLANK;
            return CATC[F.order.indexOf(c) % CATC.length];
        }
        function features() {
            if (fcCache.colour === S.colour && fcCache.d === D) return fcCache.feats;
            var d = D, feats = new Array(d.n);
            for (var i = 0; i < d.n; i++) {
                var fk = d.famKeys[d.fam[i]];
                var s = 1, o = 0.9, r = 0;
                if (d.size) { var cs = d.fk[d.P.size.key].codes[i]; if (cs >= 0) s = d.size[cs]; }
                if (fk === 'RA') s = Math.max(s, 1.3);
                if (d.fade) { var cf = d.fk[d.P.fade.key].codes[i]; if (cf >= 0) o = d.fade[cf]; }
                if (d.ring) { var cr = d.fk[d.P.ring.key].codes[i]; if (cr >= 0) r = d.ring[cr]; }
                feats[i] = { type: 'Feature', geometry: { type: 'Point', coordinates: [d.lon[i], d.lat[i]] },
                             properties: { i: i, c: colourOf(i), s: s, o: o, r: r,
                                           z: S.colour === 'fam' ? (FAMC[fk] || FAMC.OTH).z : 0 } };
            }
            fcCache = { colour: S.colour, d: D, feats: feats };
            return feats;
        }
        function layerDef() {
            return { id: LYR, type: 'circle', source: SRC, layout: { 'circle-sort-key': ['get', 'z'] }, paint: {
                // Every point stays visible zoomed out (Hig, 2026-11-16): no zoom floor,
                // and a minimum dot of ~5 px rather than one that shrinks to a speck.
                'circle-radius': ['interpolate', ['linear'], ['zoom'],
                    2, ['*', 2.5, ['get', 's']], 5, ['*', 3, ['get', 's']],
                    8, ['*', 4, ['get', 's']], 12, ['*', 6.5, ['get', 's']]],
                'circle-color': ['get', 'c'], 'circle-opacity': ['get', 'o'],
                'circle-stroke-color': ['case', ['==', ['get', 'r'], 1], '#111111', '#ffffff'],
                // zoom must be the outer expression (MapLibre rejects it inside a case)
                'circle-stroke-width': ['interpolate', ['linear'], ['zoom'],
                    3, ['case', ['==', ['get', 'r'], 1], 1.2, 0.4],
                    10, ['case', ['==', ['get', 'r'], 1], 2.2, 0.4]],
                'circle-stroke-opacity': ['case', ['==', ['get', 'r'], 1], 1, 0.6] } };
        }
        var current = { type: 'FeatureCollection', features: [] };
        function maps() { return [map, _swipe && _swipe.map].filter(function (m) { return m && m.getStyle && m.getStyle(); }); }
        function ensure(m) {
            if (!D || !m.getStyle()) return;
            try {
                if (!m.getSource(SRC)) m.addSource(SRC, { type: 'geojson', data: current });
                // Under our own records, which stay the top of the landslide stack.
                if (!m.getLayer(LYR)) m.addLayer(layerDef(), m.getLayer('points') ? 'points' : undefined);
            } catch (e) {
                // Style mid-reload (a basemap switch): map.js calls replay() once our
                // layers are back, which adds this again.
            }
        }
        function unmount(m) {
            if (m.getLayer(LYR)) m.removeLayer(LYR);
            if (m.getSource(SRC)) m.removeSource(SRC);
        }
        function oursVisibility(m) {
            var hide = !!D && !S.ctx;
            (ctx.ourLayers() || []).forEach(function (l) {
                if (m.getLayer(l.id)) m.setLayoutProperty(l.id, 'visibility', hide ? 'none' : 'visible');
            });
        }
        function replay(m) {
            if (!m) return;
            if (D) ensure(m);
            oursVisibility(m);
        }
        function draw() {
            var feats = features(), out = [];
            for (var i = 0; i < D.n; i++) if (passes(i)) out.push(feats[i]);
            current = { type: 'FeatureCollection', features: out };
            maps().forEach(function (m) {
                ensure(m);
                if (m.getSource(SRC)) m.getSource(SRC).setData(current);
                oursVisibility(m);
            });
        }

        // ------------------------------------------------------------ sidebar
        function render() {
            panel.innerHTML = '';
            if (!D) return;
            var d = D, P = d.P;
            compile();

            var cite = el('div', 'xex-cite');
            cite.innerHTML = 'Published by others, mirrored unchanged. <a href="' + esc(P.url) + '" target="_blank" rel="noopener">' +
                esc(P.citation) + '</a>' + (d.fold ? ' Spellings that differ only in spaces or capitals are counted as one value (*).' : '');
            panel.appendChild(cite);

            var shown = 0, famCount = d.famKeys.map(function () { return 0; }), subCount = {};
            for (var i = 0; i < d.n; i++) {
                if (!visible(i)) continue;
                if (passes(i)) shown++;
                if (passes(i, 'fam')) { famCount[d.fam[i]]++; subCount[d.type[i]] = (subCount[d.type[i]] || 0) + 1; }
            }
            var cnt = el('div', 'xex-count');
            cnt.innerHTML = '<b>' + fmt(shown) + '</b> of ' + fmt(d.n) + (S.limit ? ' in view' : '') + ' shown';
            panel.appendChild(cnt);
            var lim = el('label', 'nav-filter-label small');
            lim.innerHTML = '<input type="checkbox" id="xex-limit"' + (S.limit ? ' checked' : '') + '> Limit counts to map view';
            lim.querySelector('input').onchange = function (e) { S.limit = e.target.checked; updateView(); changed(false); };
            panel.appendChild(lim);

            // Presets
            if (P.presets && P.presets.length) {
                panel.appendChild(el('div', 'nav-class-header', 'Focus'));
                var pw = el('div', 'xex-presets');
                P.presets.forEach(function (p, pi) {
                    var b = el('button', 'xex-preset' + (S.preset === pi ? ' on' : ''), p.label);
                    b.type = 'button';
                    b.onclick = function () { applyPreset(pi); };
                    pw.appendChild(b);
                });
                panel.appendChild(pw);
            }

            // Families
            var fh = el('div', 'nav-class-header', P.type_label + ', by process family');
            var tg = el('span', 'subgroup-toggle');
            var aAll = el('a', null, 'all'), aNone = el('a', null, 'none');
            aAll.href = '#'; aNone.href = '#';
            aAll.onclick = function (e) { e.preventDefault(); S.fams = null; S.subOff = {}; S.preset = -1; changed(); };
            aNone.onclick = function (e) { e.preventDefault(); S.fams = []; S.preset = -1; changed(); };
            tg.appendChild(aAll); tg.appendChild(document.createTextNode(' / ')); tg.appendChild(aNone);
            fh.appendChild(tg);
            panel.appendChild(fh);
            P.families.forEach(function (f, fi) {
                var subs = Object.keys(d.subCount[fi]).map(Number).sort(function (a, b) { return d.subCount[fi][b] - d.subCount[fi][a]; });
                if (!subs.length) return;
                var row = el('label', 'nav-filter-label' + (famCount[fi] ? '' : ' xex-dim'));
                var exp = el('button', 'xex-exp', UI['open_' + f.k] ? '▾' : '▸');
                exp.type = 'button'; exp.title = 'Show ' + P.short + '’s own types in this family';
                exp.onclick = function (e) { e.preventDefault(); UI['open_' + f.k] = !UI['open_' + f.k]; render(); };
                var cb = el('input'); cb.type = 'checkbox'; cb.checked = C.famOK[fi];
                cb.onchange = function () {
                    var cur = S.fams ? S.fams.slice() : d.famKeys.slice();
                    if (cb.checked) { if (cur.indexOf(f.k) < 0) cur.push(f.k); }
                    else cur = cur.filter(function (x) { return x !== f.k; });
                    S.fams = cur.length >= d.famKeys.length ? null : cur; S.preset = -1; changed();
                };
                var dot = el('span', 'cls-dot xex-dot'); dot.style.background = (FAMC[f.k] || FAMC.OTH).col;
                row.appendChild(exp); row.appendChild(cb); row.appendChild(dot);
                row.appendChild(el('span', 'cls-name', f.label)); row.appendChild(el('span', 'cls-count', fmt(famCount[fi])));
                panel.appendChild(row);
                if (UI['open_' + f.k]) {
                    var box = el('div', 'xex-subs');
                    subs.forEach(function (t) {
                        var label = t < 0 ? '(blank)' : d.types[t];
                        var r2 = el('label', 'nav-filter-label' + ((subCount[t] || 0) ? '' : ' xex-dim'));
                        var vk = t < 0 ? '' : d.vkey(label);
                        var c2 = el('input'); c2.type = 'checkbox'; c2.checked = !S.subOff[vk]; c2.disabled = !C.famOK[fi] || t < 0;
                        c2.onchange = function () { if (c2.checked) delete S.subOff[vk]; else S.subOff[vk] = 1; S.preset = -1; changed(); };
                        var v = t >= 0 && P.variants && P.variants.type && P.variants.type[label];
                        if (v) r2.title = 'Also spelt: ' + v.map(function (x) { return '“' + x + '”'; }).join(', ');
                        r2.appendChild(c2); r2.appendChild(el('span', 'cls-name', label + (v ? ' *' : '')));
                        r2.appendChild(el('span', 'cls-count', fmt(subCount[t] || 0)));
                        box.appendChild(r2);
                    });
                    panel.appendChild(box);
                }
            });
            var lg = el('div', 'xex-legend');
            if (P.ring) lg.innerHTML += '<div><span class="sw" style="border:2px solid #111;background:#fff"></span>' + esc(P.ring.label) + '</div>';
            if (P.size) lg.innerHTML += '<div>' + esc(P.size.label) + '</div>';
            if (P.fade) lg.innerHTML += '<div>' + esc(P.fade.label) + '</div>';
            panel.appendChild(lg);

            // Colour by
            panel.appendChild(el('div', 'nav-class-header', 'Colour by'));
            var cs = el('select', 'xex-sel'); cs.id = 'xex-colour';
            (P.colour_by || []).forEach(function (o) {
                if (o[0] !== 'fam' && !d.fk[o[0]]) return;
                var op = el('option', null, o[1]); op.value = o[0]; cs.appendChild(op);
            });
            cs.value = S.colour;
            cs.onchange = function () { S.colour = cs.value; changed(); };
            panel.appendChild(cs);
            if (S.colour !== 'fam' && d.fk[S.colour]) {
                var F0 = d.fk[S.colour], kl = el('div', 'xex-legend');
                F0.order.forEach(function (c, oi) {
                    var row = el('div');
                    var sw = el('span', 'sw'); sw.style.background = c < 0 ? BLANK : CATC[oi % CATC.length];
                    row.appendChild(sw);
                    row.appendChild(document.createTextNode((c < 0 ? '(blank)' : F0.dict[c]) + ' (' + fmt(c < 0 ? F0.blanks : F0.total[c]) + ')'));
                    kl.appendChild(row);
                });
                panel.appendChild(kl);
            }

            // Their fields
            panel.appendChild(el('div', 'nav-class-header', 'Their fields'));
            d.facets.forEach(function (F) { panel.appendChild(facetUI(F)); });

            // Review (editors)
            if (d.rv) {
                panel.appendChild(el('div', 'nav-class-header', 'Your review'));
                var rc = {};
                for (var k = 0; k < d.n; k++) if (visible(k) && passes(k, 'rv')) rc[d.rv[k]] = (rc[d.rv[k]] || 0) + 1;
                var opts = [['u', 'Unreviewed'], ['p', 'Promoted']].concat(d.cats.map(function (c) { return ['c' + c.id, c.label]; }));
                opts.forEach(function (o) {
                    var row = el('label', 'nav-filter-label' + ((rc[o[0]] || 0) ? '' : ' xex-dim'));
                    var c = el('input'); c.type = 'checkbox'; c.checked = !S.rv || S.rv.indexOf(o[0]) >= 0;
                    c.onchange = function () {
                        var cur = S.rv ? S.rv.slice() : opts.map(function (x) { return x[0]; });
                        if (c.checked) { if (cur.indexOf(o[0]) < 0) cur.push(o[0]); }
                        else cur = cur.filter(function (x) { return x !== o[0]; });
                        S.rv = cur.length >= opts.length ? null : cur; changed();
                    };
                    row.appendChild(c); row.appendChild(el('span', 'cls-name', o[1])); row.appendChild(el('span', 'cls-count', fmt(rc[o[0]] || 0)));
                    panel.appendChild(row);
                });
                panel.appendChild(el('div', 'xex-note', 'Set a record’s review from its popup; + there adds a category.'));
            }

            // Context
            panel.appendChild(el('div', 'nav-class-header', 'Context'));
            var cx = el('label', 'nav-filter-label small');
            cx.innerHTML = '<input type="checkbox" id="xex-ctx"' + (S.ctx ? ' checked' : '') + '> Show our own records too';
            cx.querySelector('input').onchange = function (e) { S.ctx = e.target.checked; changed(); };
            panel.appendChild(cx);
        }

        // One field's values with the three-way switch the Landslide features filter
        // uses (✓ only these · no preference ✗ exclude; shared .ft-list CSS).
        function facetUI(F) {
            var d = D, fs = S.facet[F.key] || { m: [], x: [] };
            var det = el('details', 'xex-facet'); det.open = !!UI['open_f_' + F.key];
            det.ontoggle = function () { UI['open_f_' + F.key] = det.open; };
            var sum = el('summary');
            sum.appendChild(el('span', null, F.label));
            var name = function (k) { return k === '' ? '(blank)' : labelOf(F, k); };
            var st = [];
            if (fs.m.length) st.push('only ' + fs.m.map(name).join(', '));
            if (fs.x.length) st.push('not ' + fs.x.map(name).join(', '));
            sum.appendChild(el('span', 'st', st.length ? st.join(' · ') : 'any'));
            det.appendChild(sum);
            var cnt = {};
            for (var i = 0; i < d.n; i++) if (visible(i) && passes(i, F.key)) { var c = F.codes[i]; cnt[c] = (cnt[c] || 0) + 1; }
            var list = el('div', 'ft-list');
            F.order.forEach(function (c) {
                var label = c < 0 ? '(blank)' : F.dict[c], vk = c < 0 ? '' : d.vkey(label);
                var state = fs.m.indexOf(vk) >= 0 ? 'must' : fs.x.indexOf(vk) >= 0 ? 'excl' : 'neutral';
                var row = el('div', 'ft-row' + (state === 'must' ? ' ft-must' : state === 'excl' ? ' ft-excl' : '') +
                                    ((cnt[c] || 0) ? '' : ' xex-dim'));
                var v = c >= 0 && F.variants[label];
                if (v) row.title = 'Also spelt: ' + v.map(function (x) { return '“' + x + '”'; }).join(', ');
                row.appendChild(el('span', 'ft-label cls-name', label + (v ? ' *' : '')));
                row.appendChild(el('span', 'cls-count', fmt(cnt[c] || 0)));
                var tri = el('span', 'ft-tri'); tri.setAttribute('role', 'group'); tri.setAttribute('aria-label', label);
                [['must', '✓', 'Only these'], ['neutral', '·', 'No preference'], ['excl', '✗', 'Exclude']].forEach(function (o) {
                    var b = el('button', o[0] === state ? 'on' : null, o[1]);
                    b.type = 'button'; b.title = o[2]; b.setAttribute('data-s', o[0]);
                    b.onclick = function () {
                        var nf = { m: fs.m.filter(function (x) { return x !== vk; }), x: fs.x.filter(function (x) { return x !== vk; }) };
                        if (o[0] === 'must') nf.m.push(vk);
                        if (o[0] === 'excl') nf.x.push(vk);
                        S.facet[F.key] = (nf.m.length || nf.x.length) ? nf : null;
                        S.preset = -1; changed();
                    };
                    tri.appendChild(b);
                });
                row.appendChild(tri);
                list.appendChild(row);
            });
            det.appendChild(list);
            if (fs.m.length || fs.x.length) {
                var clr = el('button', 'xex-clear', 'clear'); clr.type = 'button';
                clr.onclick = function () { S.facet[F.key] = null; S.preset = -1; changed(); };
                det.appendChild(clr);
            }
            return det;
        }
        function labelOf(F, vk) {
            for (var i = 0; i < F.dict.length; i++) if (D.vkey(F.dict[i]) === vk) return F.dict[i];
            return vk;
        }

        function applyPreset(pi) {
            var p = D.P.presets[pi]; if (!p) return;
            var keep = { colour: S.colour, ctx: S.ctx, limit: S.limit, rv: S.rv };
            S = blank(); S.colour = keep.colour; S.ctx = keep.ctx; S.limit = keep.limit; S.rv = keep.rv; S.preset = pi;
            Object.keys(p.set || {}).forEach(function (k) {
                var v = p.set[k];
                if (k === 'fam') S.fams = v.slice();
                else if (k === 'type') {
                    // Only these of their types: their families on, every other type in those families off.
                    var want = v.map(D.vkey), fams = {};
                    D.types.forEach(function (t, ti) {
                        if (want.indexOf(D.vkey(t)) >= 0) fams[D.famKeys[D.P.type_family[ti]]] = 1;
                    });
                    S.fams = Object.keys(fams);
                    D.types.forEach(function (t, ti) {
                        if (fams[D.famKeys[D.P.type_family[ti]]] && want.indexOf(D.vkey(t)) < 0) S.subOff[D.vkey(t)] = 1;
                    });
                }
                else if (D.fk[k]) S.facet[k] = { m: v.map(D.vkey), x: [] };
            });
            changed();
        }

        // ------------------------------------------------------------ switching
        function changed(redraw) {
            if (!D) return;
            compile();
            if (redraw !== false) draw();
            render();
            remember();
            if (ctx.writeUrlState) ctx.writeUrlState();
        }
        function remember() {
            try {
                var all = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
                all[D.sid] = { xf: encodeState(), xc: S.colour };
                localStorage.setItem(LS_KEY, JSON.stringify(all));
            } catch (e) { /* storage unavailable: the URL still carries it */ }
        }
        function recalled(sid) {
            try { return (JSON.parse(localStorage.getItem(LS_KEY) || '{}'))[sid] || null; } catch (e) { return null; }
        }

        // setDataset(sid, how): how.state = {xf, xc} to apply; how.fly = frame the data when none is in view.
        function setDataset(sid, how) {
            how = how || {};
            var seq = ++loadSeq;
            if (!sid) {
                D = null; C = null; pending = null;
                maps().forEach(function (m) { unmount(m); oursVisibility(m); });
                panel.innerHTML = ''; panel.hidden = true;
                if (oursList) oursList.style.display = '';
                sel.value = '';
                if (ctx.writeUrlState) ctx.writeUrlState();
                return;
            }
            var st0 = how.state || recalled(sid) || {};
            pending = { sid: sid, xf: st0.xf || '', xc: st0.xc || 'fam' };
            sel.value = sid;
            panel.hidden = false;
            if (oursList) oursList.style.display = 'none';
            panel.innerHTML = '<div class="xex-note">Loading…</div>';
            load(sid).then(function (d) {
                if (seq !== loadSeq) return;
                pending = null;
                if (D !== d) maps().forEach(unmount);
                D = d; S = blank();
                var st = how.state || recalled(sid);
                if (st) { decodeState(st.xf || ''); if (st.xc && (st.xc === 'fam' || D.fk[st.xc])) S.colour = st.xc; S.preset = -1; }
                compile();
                draw();
                render();
                if (how.fly) flyIfEmpty();
                if (ctx.writeUrlState) ctx.writeUrlState();
            }).catch(function (e) {
                if (seq !== loadSeq) return;
                pending = null;
                panel.innerHTML = '';
                panel.appendChild(el('div', 'xex-note', 'Could not load this inventory: ' + e.message));
            });
        }
        function flyIfEmpty() {
            var b = map.getBounds(), any = false;
            for (var i = 0; i < D.n && !any; i++) {
                if (D.lon[i] >= b.getWest() && D.lon[i] <= b.getEast() && D.lat[i] >= b.getSouth() && D.lat[i] <= b.getNorth()) any = true;
            }
            var h = D.P.home;
            if (!any && h) map.flyTo({ center: [h[0], h[1]], zoom: h[2] });
        }
        function updateView() {
            var b = map.getBounds();
            inView = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
        }

        sel.addEventListener('change', function () { setDataset(sel.value, { fly: true }); });
        map.on('moveend', function () { if (D && S.limit) { updateView(); render(); } });
        window.addEventListener('ls:ext-promote', function () { if (D) setDataset(''); });
        window.addEventListener('ls:ext-review', function (e) {
            var d = cache[e.detail && e.detail.sid];
            if (!d) return;
            loadReviews(d).then(function () { if (D === d) changed(); });
        });

        // Click: the full record, then the shared popup (Review box, Promote).
        if (window.LSTools && LSTools.clicks) {
            LSTools.clicks.register({
                id: 'ext-explore', kind: 'reference', priority: 10,
                layers: function () { return D ? [LYR] : []; },
                handler: function (f) {
                    if (!D) return;
                    var d = D, i = f.properties.i, fk = d.famKeys[d.fam[i]];
                    var ll = { lng: d.lon[i], lat: d.lat[i] };
                    fetch(API_BASE + 'api/external/' + d.sid + '/' + d.P.layer + '/record/' + encodeURIComponent(d.ext[i]) + '/',
                          { credentials: 'same-origin' })
                        .then(function (r) { return r.ok ? r.json() : null; })
                        .then(function (j) {
                            if (!j) return;
                            var s = Object.assign({}, j.source, { colour: (FAMC[fk] || FAMC.OTH).col });
                            var L = { layer: j.layer, label: 'Landslide points', fields: j.labels, field_order: j.order };
                            ctx.showRecord(ll, s, L, j.properties);
                        })
                        .catch(function (e) { console.error('explorer record failed', e); });
                }
            });
        }

        // ------------------------------------------------------------ URL state
        // xf = comma-separated tokens <key>.<v>~<v>…  where key is `fam` (family keys
        // on), `sub` (their types switched off), `rv` (review codes on: u, p, c<id>),
        // `ctx` (0 = hide our records), `lim` (1 = counts in view), one of the
        // source's facet keys (values marked ✓: only these), or a facet key + `-x`
        // (values marked ✗: excluded). Values are the folded spelling when the
        // fold is on, percent-encoded so only [A-Za-z0-9_-%] remain (the stored-view
        // charset, views._VIEW_STATE_RE). An empty value is "(blank)".
        function enc(v) {
            return encodeURIComponent(v).replace(/[.!~*'()]/g, function (c) {
                return '%' + c.charCodeAt(0).toString(16).toUpperCase();
            });
        }
        function encodeState() {
            var t = [];
            if (S.fams) t.push('fam.' + S.fams.map(enc).join('~'));
            var so = Object.keys(S.subOff);
            if (so.length) t.push('sub.' + so.map(enc).join('~'));
            Object.keys(S.facet).forEach(function (k) {
                var fs = S.facet[k];
                if (!fs) return;
                if (fs.m.length) t.push(k + '.' + fs.m.map(enc).join('~'));
                if (fs.x.length) t.push(k + '-x.' + fs.x.map(enc).join('~'));
            });
            if (S.rv) t.push('rv.' + S.rv.map(enc).join('~'));
            if (!S.ctx) t.push('ctx.0');
            if (S.limit) t.push('lim.1');
            return t.join(',');
        }
        function decodeState(xf) {
            (xf || '').split(',').forEach(function (tok) {
                var p = tok.indexOf('.');
                if (p < 1) return;
                var k = tok.slice(0, p), vals;
                try { vals = tok.slice(p + 1).split('~').map(decodeURIComponent); } catch (e) { return; }
                if (k === 'fam') S.fams = vals.filter(function (v) { return D.famKeys.indexOf(v) >= 0; });
                else if (k === 'sub') vals.forEach(function (v) { S.subOff[v] = 1; });
                else if (k === 'rv') S.rv = vals;
                else if (k === 'ctx') S.ctx = vals[0] !== '0';
                else if (k === 'lim') { S.limit = vals[0] === '1'; if (S.limit) updateView(); }
                else if (D.fk[k]) { S.facet[k] = S.facet[k] || { m: [], x: [] }; S.facet[k].m = vals; }
                else if (/-x$/.test(k) && D.fk[k.slice(0, -2)]) {
                    var fk = k.slice(0, -2);
                    S.facet[fk] = S.facet[fk] || { m: [], x: [] }; S.facet[fk].x = vals;
                }
            });
        }
        function spec() {
            if (!D && pending) {
                var o0 = { xs: pending.sid };
                if (pending.xf) o0.xf = pending.xf;
                if (pending.xc && pending.xc !== 'fam') o0.xc = pending.xc;
                return o0;
            }
            if (!D) return null;
            var o = { xs: D.sid };
            var xf = encodeState();
            if (xf) o.xf = xf;
            if (S.colour !== 'fam') o.xc = S.colour;
            return o;
        }
        function applyHash(s) {
            if (s.xs === undefined) return;               // absent: leave alone
            if (!s.xs || !sel.querySelector('option[value="' + s.xs + '"]')) { setDataset(''); return; }
            setDataset(s.xs, { state: { xf: s.xf || '', xc: s.xc || 'fam' }, fly: false });
        }

        return { spec: spec, applyHash: applyHash, replay: replay };
    } };
})();
