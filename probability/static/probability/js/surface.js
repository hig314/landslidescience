/* surface.js: the shading and 3D controls, recycled from landslidescience.org's
 * /lidar/ page (pages/templates/pages/lidar_preview.html). Global `PH.Surface`.
 *
 * Kept THE SAME as /lidar/ on purpose, so the two read alike and a link's
 * shading keys mean the same thing on both: the control ids and URL keys, the
 * slider ranges and units, the defaults (terrain exaggeration 1.0), the value
 * printed beside every slider, the checkbox-plus-slider rows for the optional
 * ramps, the Preset / Hillshade-only buttons, and the terrain-centre tracking.
 *
 *   id    slider    means                       default
 *   hs    0..100    hillshade strength / 100    100
 *   az    0..359    sun azimuth, degrees        315
 *   alt   1..89     sun altitude, degrees       45
 *   ve    1..60     shading exaggeration / 10   10  (1.0)
 *   bl    select    blend: 2 hard light, 0 overlay, 3 multiply, 1 plain
 *   sl md as        slope ramp, mod 5 m, aspect tint, strength / 100 (0 = off)
 *   t3d   checkbox  3D terrain                  on
 *   ex    0..40     terrain exaggeration / 10   10  (1.0)
 *   bop   0..100    imagery opacity / 100       0   (/lidar/ opens at 55 over a basemap)
 *
 * Needs dem_shade_bridge.js (window.DemShade). Not carried over: surveys,
 * difference, water, context terrain, save image.
 *
 *   PH.Surface.restore(Q)            set the controls from a link, before the map exists
 *   PH.Surface.readOpts()            {az, alt, ve, hs, bl, sl, md, as} for DemShade.url
 *   PH.Surface.init(map, o)          o = {demId, shadeSource, terrainSource, imageryLayer, onChange}
 *   PH.Surface.writeUrl(q)           add the controls to a URLSearchParams
 */
window.PH = window.PH || {};
PH.Surface = (function () {
  var CONTROLS = ['az', 'alt', 've', 'hs', 'bl', 'sl', 'md', 'as', 'ex', 'bop'];
  var CHECKS = ['t3d'];
  // [checkbox, slider, row, default-when-first-ticked] for the optional ramps.
  var VIS = [['slon', 'sl', 'slrow', 60], ['mdon', 'md', 'mdrow', 100], ['ason', 'as', 'asrow', 50]];
  var $ = function (id) { return document.getElementById(id); };
  var V = function (id) { return +$(id).value; };
  var map, O, untrackTerrain = null, retileTimer = null;

  function readOpts() {
    return {az: V('az'), alt: V('alt'), ve: V('ve') / 10, hs: V('hs') / 100, bl: V('bl'),
            sl: V('sl') / 100, md: V('md') / 100, as: V('as') / 100};
  }

  // The three optional ramps: the checkbox is DERIVED from the value, so the
  // URL does not grow a key and zero already means off.
  function syncVisRows() {
    VIS.forEach(function (v) {
      var on = V(v[1]) > 0;
      $(v[0]).checked = on;
      $(v[2]).hidden = !on;
    });
  }

  function labels() {
    var o = readOpts();
    $('azv').textContent = o.az + '°';
    $('altv').textContent = o.alt + '°';
    $('vev').textContent = o.ve.toFixed(1);
    $('hsv').textContent = o.hs.toFixed(2);
    $('slv').textContent = o.sl.toFixed(2);
    $('mdv').textContent = o.md.toFixed(2);
    $('asv').textContent = o.as.toFixed(2);
    $('exv').textContent = (V('ex') / 10).toFixed(1);
    $('bopv').textContent = (V('bop') / 100).toFixed(2);
  }

  function restore(Q) {
    CONTROLS.forEach(function (id) {
      var v = Q.get(id);
      if (v !== null && v !== '') $(id).value = v;
    });
    syncVisRows();
    CHECKS.forEach(function (id) {
      var v = Q.get(id);
      $(id).checked = v === null ? $(id).defaultChecked : (v === '1' || v === 'true');
    });
    labels();
  }

  function setTerrain(on) {
    if (on && V('ex') > 0) {
      map.setTerrain({source: O.terrainSource, exaggeration: V('ex') / 10});
      // Clamped (MapLibre's default), the centre elevation is re-sampled every
      // frame and the view lurches as sharper DEM tiles stream in. The package
      // samples the surface under the centre after each gesture instead, with
      // a threshold scaled to the camera's height (2 %, never under 0.5 m).
      if (!untrackTerrain && DemShade.trackTerrainCenter) {
        untrackTerrain = DemShade.trackTerrainCenter(map, {
          threshold: DemShade.relativeThreshold ? DemShade.relativeThreshold(0.02, 0.5) : undefined
        });
      }
    } else {
      map.setTerrain(null);
      if (untrackTerrain) { untrackTerrain(); untrackTerrain = null; }
    }
  }

  function render() {
    labels();
    map.setPaintProperty(O.imageryLayer, 'raster-opacity', V('bop') / 100);
    setTerrain($('t3d').checked);
    // every shading value is in the tile URL, so a slider drag would ask for a new tile set per pixel moved
    clearTimeout(retileTimer);
    retileTimer = setTimeout(function () {
      map.getSource(O.shadeSource).setTiles([DemShade.url(O.demId, readOpts())]);
    }, 160);
    if (O.onChange) O.onChange();
  }

  function applyPreset(p) {
    Object.keys(p).forEach(function (k) { $(k).value = p[k]; });
    syncVisRows();
    render();
  }

  function init(m, o) {
    map = m; O = o;
    CONTROLS.forEach(function (id) {
      $(id).addEventListener('input', render);
      $(id).addEventListener('change', render);
    });
    VIS.forEach(function (v) {
      $(v[0]).addEventListener('change', function () {
        var slider = $(v[1]);
        // Turning one on restores what it was, or a sensible default the first
        // time; never 0, which would tick a box and change nothing.
        slider.value = this.checked ? (slider.dataset.last || v[3]) : 0;
        $(v[2]).hidden = !this.checked;
        render();
      });
      $(v[1]).addEventListener('input', function () { if (+this.value > 0) this.dataset.last = this.value; });
    });
    $('t3d').addEventListener('change', function () {
      if (this.checked && map.getPitch() < 30) map.easeTo({pitch: 60});
      render();
    });
    // Hard light rather than Overlay: Overlay lets the ramp decide and washes
    // the shading out wherever the ramp is pale, which is most of a mod-5 base.
    $('preset').addEventListener('click', function () { applyPreset({md: 100, sl: 60, hs: 100, bl: 2, as: 0, az: 315, alt: 45}); });
    $('plain').addEventListener('click', function () { applyPreset({md: 0, sl: 0, hs: 100, bl: 2, as: 0}); });
    render();
  }

  // Only what differs from the defaults, so a plain view keeps a short address.
  function writeUrl(q) {
    CONTROLS.forEach(function (id) {
      var el = $(id), def = el.tagName === 'SELECT' ? el.querySelector('option[selected]').value : el.defaultValue;
      if (el.value !== def) q.set(id, el.value);
    });
    CHECKS.forEach(function (id) { if ($(id).checked !== $(id).defaultChecked) q.set(id, $(id).checked ? '1' : '0'); });
  }

  return {restore: restore, readOpts: readOpts, init: init, writeUrl: writeUrl};
})();
