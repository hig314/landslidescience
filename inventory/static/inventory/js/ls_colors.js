/* Shared landslide map styling — SINGLE SOURCE OF TRUTH for point/polygon
 * colors. Used by BOTH the main inventory map (map.js) and the per-record
 * form preview map (_polygon_map.html) so their symbology matches. Edit the
 * palette or an expression here once; don't re-derive colors in either map.
 *
 *   window.LSColors.palette(settings) → resolved style object (colors + numerics)
 *   .classFill(P) / .classStroke(P) / .classStrokeWidth()  → point paint exprs
 *   .polygonFill(P) / .polygonOutline(P)                   → polygon paint exprs
 *
 * `settings` is the flat map_settings object (api/settings); missing keys fall
 * back to defaults. MapLibre expressions key on feature props landslide_class
 * and (for polygons) role.
 */
(function () {
  var DEFAULTS = {
    geomorph: '#d3e9cf', subtle: '#faf075', obvious: '#f69fa1',
    cat: '#3f67b1', catPale: '#96b8df', off: '#9e9e9e', stroke: '#ffffff'
  };

  function palette(s) {
    s = s || {};
    return {
      cG: s.color_geomorph || DEFAULTS.geomorph,
      cS: s.color_subtle   || DEFAULTS.subtle,
      cO: s.color_obvious  || DEFAULTS.obvious,
      cC: s.color_cat      || DEFAULTS.cat,
      cCPale: DEFAULTS.catPale,   // de-emphasized catastrophic (Holocene/Modern/Small)
      OFF: DEFAULTS.off,          // unclassified: blank/unrecognized landslide_class
      sk: s.stroke_color   || DEFAULTS.stroke,
      fOp: parseFloat(s.fill_opacity) || 0.35,
      lW:  parseFloat(s.line_width)   || 1.5,
      rSm: parseFloat(s.circle_sm)    || 3,
      rMd: parseFloat(s.circle_md)    || 5,
      rLg: parseFloat(s.circle_lg)    || 7
    };
  }

  function classFill(p) {
    return ['match', ['get', 'landslide_class'],
      'Slow Obvious creep', p.cO, 'Slow Patchy obvious creep', p.cO,
      'Slow Subtle creep', p.cS,
      'Slow Geomorph creep', p.cG, 'Small slow landslide', p.cG,
      'Catastrophic Cryptic', p.cC,
      'Catastrophic Obvious creep', p.cC, 'Catastrophic Patchy obvious creep', p.cC,
      'Catastrophic Subtle creep', p.cC, 'Catastrophic Geomorph creep', p.cC,
      'Catastrophic Modern', p.cCPale, 'Catastrophic Holocene', p.cCPale,
      'Small catastrophic landslide', p.cCPale,
      p.OFF];   // blank / unrecognized → neutral off-colour
  }

  function classStroke(p) {
    return ['match', ['get', 'landslide_class'],
      'Catastrophic Obvious creep', p.cO, 'Catastrophic Patchy obvious creep', p.cO,
      'Catastrophic Subtle creep', p.cS, 'Catastrophic Geomorph creep', p.cG,
      p.sk];
  }

  function classStrokeWidth() {
    return ['match', ['get', 'landslide_class'],
      'Catastrophic Obvious creep', 3, 'Catastrophic Patchy obvious creep', 3,
      'Catastrophic Subtle creep', 3, 'Catastrophic Geomorph creep', 3,
      1];
  }

  function polygonFill(p) {
    return ['case',
      // blank/unclassified → off-colour first, so an unclassified catastrophic
      // polygon matches its grey dot.
      ['==', ['coalesce', ['get', 'landslide_class'], ''], ''], p.OFF,
      ['==', ['get', 'role'], 'deposit'], p.cC,
      ['==', ['get', 'role'], 'source'],  p.cC,
      classFill(p)];
  }

  function polygonOutline(p) {
    return ['case',
      ['==', ['get', 'role'], 'deposit'], '#1a3f80',
      ['==', ['get', 'role'], 'source'],  '#1a3f80',
      p.sk];
  }

  window.LSColors = {
    palette: palette,
    classFill: classFill,
    classStroke: classStroke,
    classStrokeWidth: classStrokeWidth,
    polygonFill: polygonFill,
    polygonOutline: polygonOutline
  };
})();
