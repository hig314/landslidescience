/* Shared EPSG:3413 <-> lon/lat transforms (WGS84 north polar stereographic,
 * lat_ts=70, lon_0=-45) — the grid every ITS_LIVE Alaska product uses.
 * Snyder (1987) ellipsoidal series; verified against pyproj to sub-millimetre
 * at Alaskan latitudes. Lives here so the tracer engine and the image-pair
 * viewer cannot drift apart (same rule as basemaps.js / ls_overlays.js).
 */
(function () {
    'use strict';

    var A = 6378137.0, E = 0.081819190842622, LON0 = -45 * Math.PI / 180;
    var E2 = E / 2;

    function _tOf(phi) {
        return Math.tan(Math.PI / 4 - phi / 2) /
               Math.pow((1 - E * Math.sin(phi)) / (1 + E * Math.sin(phi)), E2);
    }
    var PHI_TS = 70 * Math.PI / 180;
    var M_TS = Math.cos(PHI_TS) / Math.sqrt(1 - E * E * Math.sin(PHI_TS) * Math.sin(PHI_TS));
    var T_TS = _tOf(PHI_TS);

    function fromLonLat(lon, lat) {
        var phi = lat * Math.PI / 180, lam = lon * Math.PI / 180;
        var rho = A * M_TS * _tOf(phi) / T_TS;
        return [rho * Math.sin(lam - LON0), -rho * Math.cos(lam - LON0)];
    }

    function toLonLat(x, y) {
        var rho = Math.hypot(x, y);
        var t = rho * T_TS / (A * M_TS);
        var chi = Math.PI / 2 - 2 * Math.atan(t);
        var e2 = E * E, e4 = e2 * e2, e6 = e4 * e2, e8 = e4 * e4;
        var phi = chi +
            (e2 / 2 + 5 * e4 / 24 + e6 / 12 + 13 * e8 / 360) * Math.sin(2 * chi) +
            (7 * e4 / 48 + 29 * e6 / 240 + 811 * e8 / 11520) * Math.sin(4 * chi) +
            (7 * e6 / 120 + 81 * e8 / 1120) * Math.sin(6 * chi) +
            (4279 * e8 / 161280) * Math.sin(8 * chi);
        var lam = LON0 + Math.atan2(x, -y);
        return [lam * 180 / Math.PI, phi * 180 / Math.PI];
    }

    window.LSProj = { fromLonLat: fromLonLat, toLonLat: toLonLat };
})();
