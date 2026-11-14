/* revise_ui.js — Revising an existing outline on the live map: the bar, the tool hold, splicing the saved result back in.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSReviseUI.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   API_BASE, map, _csrf, _getFeaturesData, _getPolygonsData, _setPolygonsData, _swipeSetFeatures, _swipeSetPolygons, onMoveEnd, buildFilter, esc.
 * It returns what map.js still uses: nothing.
 *
 * The editing engine is LSRevise (revise.js); this is the map page's side of it.
 */
(function () {
    'use strict';

    window.LSReviseUI = { init: function (ctx) {
        var API_BASE = ctx.API_BASE;
        var map = ctx.map;
        var _csrf = ctx._csrf;
        var _getFeaturesData = ctx._getFeaturesData;
        var _getPolygonsData = ctx._getPolygonsData;
        var _setPolygonsData = ctx._setPolygonsData;
        var _swipeSetFeatures = ctx._swipeSetFeatures;
        var _swipeSetPolygons = ctx._swipeSetPolygons;
        var onMoveEnd = ctx.onMoveEnd;
        var buildFilter = ctx.buildFilter;
        var esc = ctx.esc;

        // -----------------------------------------------------------------------
        // Revising an existing outline, on the live map
        // -----------------------------------------------------------------------
        // The draw tool makes new components; this edits ones that exist. It runs
        // against the whole map -- basemaps, wiper, lidar, Sentinel -- which is the
        // entire point: the form's preview has none of that, and an outline is only
        // as good as what you could see while drawing it.
        var _reviseBar = null;

        function _reviseBuildBar(info) {
            if (_reviseBar) _reviseBar.remove();
            var bar = document.createElement('div');
            bar.id = 'revise-bar';
            bar.innerHTML =
                '<div class="rv-title">Revising <b>' + esc(info.name) + '</b>' +
                ' <span class="rv-count">' + info.count + ' polygon' +
                (info.count === 1 ? '' : 's') +
                ((info.skipped && info.skipped.length)
                  // Say what is NOT being edited. A component left out because
                  // Terra Draw cannot represent it is untouched by a save, but
                  // silence would read as "this is all of it".
                  ? ', ' + info.skipped.length + ' not editable: ' +
                    esc(info.skipped.join('; '))
                  : '') + '</span></div>' +
                '<div class="rv-hint">Click a polygon to select it, then drag a vertex to ' +
                'move it or a midpoint to add one; right-click a vertex to remove it. ' +
                'Nothing is written until you save.</div>' +
                '<div class="rv-actions">' +
                '  <button type="button" id="rv-save" class="rv-primary">Save</button>' +
                '  <button type="button" id="rv-revert">Revert</button>' +
                '  <button type="button" id="rv-exit">Done</button>' +
                '  <span id="rv-status"></span>' +
                '</div>';
            document.body.appendChild(bar);
            _reviseBar = bar;
            var status = bar.querySelector('#rv-status');
            function say(msg, cls) {
                status.textContent = msg || '';
                status.className = cls || '';
            }
            bar.querySelector('#rv-save').addEventListener('click', function () {
                var d = LSRevise.diff();
                if (!d.updates.length && !d.deletes.length) { say('Nothing changed.', 'rv-quiet'); return; }
                say('Saving ' + d.updates.length + ' change' + (d.updates.length === 1 ? '' : 's') + '…');
                LSRevise.save().then(function (res) {
                    if (res.nothing) { say('Nothing changed.', 'rv-quiet'); return; }
                    say('Saved.', 'rv-ok');
                    // The endpoint hands back this landslide's polygons as they
                    // now stand, so splice them straight in: the outline redraws
                    // at once instead of after a round trip. The refetch still
                    // runs behind it, because the rule cascade may also have moved
                    // the centroid, area and size class that other layers show.
                    _reviseSplicePolygons(res.polygons);
                    _reviseSplicePoint(res.derived);
                    _reviseRefreshData();
                }).catch(function (e) {
                    say(e && e.message ? e.message : 'Save failed.', 'rv-bad');
                });
            });
            bar.querySelector('#rv-revert').addEventListener('click', function () {
                if (!window.confirm('Discard unsaved changes to this outline?')) return;
                var id = LSRevise.current() && LSRevise.current().id;
                LSRevise.stop();
                if (id) _reviseStart(id);
            });
            bar.querySelector('#rv-exit').addEventListener('click', function () {
                var d = LSRevise.diff();
                if ((d.updates.length || d.deletes.length) &&
                    !window.confirm('There are unsaved changes. Leave without saving?')) return;
                LSRevise.stop();
            });
        }

        // After a save, the map must redraw what was just written. This asked
        // api/polygons/ with no bbox -- which that endpoint requires, so it
        // answered 400, the catch swallowed it, and nothing changed until a pan or
        // zoom ran the real loader. Two things were wrong: the request, and
        // hand-rolling a loader at all when onMoveEnd() already fetches for the
        // current view AND merges susceptibility values, mirrors to the wiper pane
        // and rebuilds the filter -- none of which a bare setData does.
        function _reviseRefreshData() {
            onMoveEnd();
        }

        // Replace just this landslide's polygons in the loaded set, keeping every
        // other record untouched, and push the result through the same sources a
        // normal load feeds.
        function _reviseSplicePolygons(fc) {
            var cur = LSRevise.current();
            var _polygonsData = _getPolygonsData();
            if (!fc || !fc.features || !cur || !_polygonsData || !_polygonsData.features) return;
            var lid = cur.id;
            var keep = _polygonsData.features.filter(function (f) {
                return String(f.properties && f.properties.landslide_id) !== String(lid);
            });
            // The save response carries db_id/role/is_primary only, so copy the
            // styling properties off a feature we are replacing -- without them
            // the outline would redraw in the default colour until the next load.
            var donor = _polygonsData.features.find(function (f) {
                return String(f.properties && f.properties.landslide_id) === String(lid);
            });
            var fresh = fc.features.map(function (f) {
                var props = {};
                if (donor) for (var k in donor.properties) props[k] = donor.properties[k];
                for (var k2 in f.properties) props[k2] = f.properties[k2];
                props.landslide_id = lid;
                return { type: 'Feature', geometry: f.geometry, properties: props };
            });
            _polygonsData = { type: 'FeatureCollection', features: keep.concat(fresh) };
            _setPolygonsData(_polygonsData);
            var src = map.getSource('polygons');
            if (src) src.setData(_polygonsData);
            if (typeof _swipeSetPolygons === 'function') _swipeSetPolygons(_polygonsData);
        }

        // The landslide DOT is the stored centroid, and the points source is
        // fetched once at load (initLayers), never by onMoveEnd -- so after the
        // cascade moved the centroid the outline redrew and the dot stayed put
        // until a reload (the "centroids don't recalculate" report, 2026-09-26).
        // The save response carries the recomputed columns; apply them to this
        // one feature the way _patchFeatureProp does, geometry included.
        function _reviseSplicePoint(derived) {
            var cur = LSRevise.current();
            var _featuresData = _getFeaturesData();
            if (!derived || !cur || !_featuresData || !_featuresData.features) return;
            var lid = String(cur.id);
            var f = _featuresData.features.find(function (x) {
                return x.properties && String(x.properties.id) === lid;
            });
            if (!f) return;
            if (derived.centroid_lon != null && derived.centroid_lat != null) {
                f.geometry = { type: 'Point', coordinates: [+derived.centroid_lon, +derived.centroid_lat] };
            }
            var props = { area_src: derived.area_source, area_dep: derived.area_deposit,
                          size_inclusion: derived.size_inclusion, landslide_class: derived.landslide_class };
            Object.keys(props).forEach(function (k) {
                if (props[k] !== undefined) f.properties[k] = props[k];
            });
            if (map.getSource('landslides')) map.getSource('landslides').setData(_featuresData);
            _swipeSetFeatures(_featuresData);
            if (typeof buildFilter === 'function') buildFilter();
        }

        // The reviser is a Terra Draw session on the live map, exactly like the
        // draw tool, so it holds the map the same way -- headless, since it opens
        // from a record's detail panel rather than a button. Before this it set
        // map.__reviseActive, which exactly one place read: landslide clicks still
        // opened detail panels mid-edit, right-click still copied coordinates
        // instead of deleting a vertex, and measure or InSAR could start on top.
        // Hig, 2026-09-24: a full holder. Another tool taking the map releases
        // it, asking first only when there are unsaved edits, instead of the two
        // hand-written refusals that used to guard each direction.
        function _reviseDirty() {
            if (typeof LSRevise === 'undefined' || !LSRevise.isActive()) return false;
            var d = LSRevise.diff();
            return !!(d && ((d.updates && d.updates.length) || (d.deletes && d.deletes.length)));
        }
        LSTools.hold({
            id: 'revise',
            onRelease: function () {
                if (typeof LSRevise !== 'undefined' && LSRevise.isActive()) LSRevise.stop();
            },
            canRelease: function () {
                if (!_reviseDirty()) return true;
                return window.confirm('Unsaved outline edits will be discarded.'
                                      + '\n\nSwitch tools anyway?');
            },
            // Two-step Escape: with edits pending, say what to do rather than
            // discard them; with nothing pending, exit. stop() runs onExit, which
            // releases the holder.
            cancel: function () {
                if (typeof LSRevise === 'undefined' || !LSRevise.isActive()) return;
                if (_reviseDirty()) {
                    var st = _reviseBar && _reviseBar.querySelector('#rv-status');
                    if (st) st.textContent = 'Unsaved changes — Save, Revert or Done';
                    return;
                }
                LSRevise.stop();
            }
        });

        function _reviseStart(id) {
            if (typeof LSRevise === 'undefined') return;
            // Stop a previous session BEFORE claiming: its onExit releases the
            // holder, and doing that after the claim would drop the new one.
            if (LSRevise.isActive()) LSRevise.stop();
            // Takes the map from draw / measure / InSAR; false only if the outgoing
            // tool objected (draw does, with a ring still open).
            if (!LSTools.claim('revise')) return;
            LSRevise.start({
                id: id,
                map: map, api: API_BASE, csrf: _csrf,
                terraDraw: window.terraDraw, adapter: window.terraDrawMaplibreGlAdapter,
                onExit: function () {
                    LSTools.release('revise');
                    if (_reviseBar) { _reviseBar.remove(); _reviseBar = null; }
                    _reviseRefreshData();
                }
            }).then(function (info) {
                _reviseBuildBar(info);
            }).catch(function (e) {
                LSTools.release('revise');
                window.alert('Could not open the reviser: ' +
                             (e && e.message ? e.message : 'unknown error'));
            });
        }

        // Delegated: the detail panel is re-rendered on every open, so a handler
        // bound to the button itself would be lost each time.
        document.addEventListener('click', function (e) {
            var b = e.target && e.target.closest && e.target.closest('.revise-open');
            if (!b) return;
            e.preventDefault();
            _reviseStart(+b.dataset.id);
        });

        return {  };
    } };
})();
