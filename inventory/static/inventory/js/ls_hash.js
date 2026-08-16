/* Shared URL-hash view-state codec — ONE grammar for /inventory/ and
 * /glaciers/:
 *
 *   map=<zoom>/<lat>/<lon> & base=<id> & swipe=<id> & sx=<pct>
 *   & ov=<id>[~s].l<pct>r<pct>,…   (~s = data-variant flag, e.g. smoothed
 *                                   thinning; l/r = pane visibility+opacity)
 *   & <extras…>                    (app-specific params pass through:
 *                                   inventory id/tab/an, glaciers site/t)
 *
 * /glaciers/ consumes this module directly. map.js still carries its own
 * embedded parser with the IDENTICAL grammar (migration onto this module is
 * the planned next inventory-touching step — see CLAUDE.md); until that
 * lands, any grammar change MUST be made in both places. The server-side
 * validator charset (views.py _VIEW_STATE_RE) is the third party to keep
 * in sync.
 */
(function () {
    'use strict';

    function parse(hash) {
        var out = { extras: {} };
        if (!hash) return out;
        String(hash).replace(/^#/, '').split('&').forEach(function (kv) {
            var i = kv.indexOf('=');
            if (i < 0) return;
            var k = kv.slice(0, i), v = kv.slice(i + 1);
            if (k === 'map') {
                var m = v.split('/');
                var z = parseFloat(m[0]), lat = parseFloat(m[1]), lon = parseFloat(m[2]);
                if (isFinite(z) && isFinite(lat) && isFinite(lon) &&
                    z >= 0 && z <= 24 && lat >= -90 && lat <= 90 &&
                    lon >= -540 && lon <= 540) {
                    out.zoom = z; out.lat = lat; out.lon = lon;
                }
            } else if (k === 'base') {
                if (v) out.base = v;
            } else if (k === 'swipe') {
                if (v) out.swipe = v;
            } else if (k === 'sx') {
                var x = parseFloat(v);
                if (isFinite(x) && x >= 0 && x <= 100) out.sx = x;
            } else if (k === 'ov') {
                var ovOut = {};
                v.split(',').forEach(function (ent) {
                    var m2 = /^(.+)\.((?:[lr]\d+)+)$/.exec(ent);
                    if (!m2) return;
                    var e = {};
                    m2[2].replace(/([lr])(\d+)/g, function (_, sideCh, pct) {
                        var o = Math.min(100, Math.max(0, parseInt(pct, 10))) / 100;
                        if (sideCh === 'l') { e.left = true; e.opLeft = o; }
                        else                { e.right = true; e.opRight = o; }
                        return '';
                    });
                    if (e.left || e.right) {
                        var idBits = m2[1].split('~');
                        if (idBits.indexOf('s') > 0) e.smooth = true;
                        ovOut[idBits[0]] = e;
                    }
                });
                out.ov = ovOut;
            } else {
                out.extras[k] = v;
            }
        });
        return out;
    }

    /* o: { zoom, lat, lon, base, swipe, sx,
     *      ov: {id: {left, right, opLeft, opRight, smooth}}, extras: {…} }
     * Omit/null any part to leave it out of the hash. Number formats match
     * the inventory writer exactly (zoom 2dp, lat/lon 4dp). */
    function encode(o) {
        var parts = [];
        if (o.zoom != null && o.lat != null && o.lon != null) {
            parts.push('map=' + o.zoom.toFixed(2) + '/' +
                       o.lat.toFixed(4) + '/' + o.lon.toFixed(4));
        }
        if (o.base) parts.push('base=' + o.base);
        if (o.swipe) {
            parts.push('swipe=' + o.swipe);
            if (o.sx != null) parts.push('sx=' + Math.round(o.sx));
        }
        if (o.ov) {
            var ovp = [];
            Object.keys(o.ov).forEach(function (id) {
                var e = o.ov[id];
                var spec = '';
                if (e.left)  spec += 'l' + Math.round((e.opLeft != null ? e.opLeft : 1) * 100);
                if (e.right) spec += 'r' + Math.round((e.opRight != null ? e.opRight : 1) * 100);
                if (spec) ovp.push(id + (e.smooth ? '~s' : '') + '.' + spec);
            });
            if (ovp.length) parts.push('ov=' + ovp.join(','));
        }
        if (o.extras) {
            Object.keys(o.extras).forEach(function (k) {
                var v = o.extras[k];
                if (v != null && v !== '') parts.push(k + '=' + v);
            });
        }
        return '#' + parts.join('&');
    }

    window.LSHash = { parse: parse, encode: encode };
})();
