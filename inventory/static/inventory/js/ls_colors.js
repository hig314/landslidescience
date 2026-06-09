/* Shared landslide map styling — SINGLE SOURCE OF TRUTH for point/polygon
 * colors. Used by BOTH the main inventory map (map.js), the swipe comparison
 * map, and the per-record form preview map (_polygon_map.html) so their
 * symbology matches. Edit the palette or an expression here once; don't
 * re-derive colors in any map.
 *
 * STYLING MODEL (attribute-driven, NOT landslide_class):
 *   • Dot SIZE   ← size_inclusion (included = full dot, excluded = half).
 *   • Slow COLOR ← creep_behavior (obvious=red, subtle/patchy=yellow,
 *                  geomorph=green). Patchy obvious also gets a red center dot
 *                  (a separate layer, see PATCHY_FILTER).
 *   • Catastrophic COLOR ← age, from year_num (>=2012 near-black, Modern
 *                  medium blue, Holocene pale blue). Catastrophic STROKE ←
 *                  creep_behavior (the precursory-creep halo).
 *   • A slow record with NO creep, or a catastrophic with NO resolvable age,
 *     is *meaningfully incomplete* (missing the dimension display rests on) →
 *     rendered magenta so gaps are visible on the map.
 *
 *   window.LSColors.palette(settings) → resolved style object (colors + numerics)
 *   .classFill(P) / .classStroke(P) / .classStrokeWidth()  → point/polygon paint
 *   .pointRadius(P, scale) / .pointSortKey()               → point layout/paint
 *   .polygonFill(P) / .polygonOutline(P)                   → polygon paint
 *   .PATCHY_FILTER                                         → red-center layer filter
 *
 * `settings` is the flat map_settings object (api/settings); missing keys fall
 * back to defaults. Expressions key on feature props landslide_type,
 * creep_behavior, year_num, size_inclusion and (polygons) role.
 *
 * NOTE: the catastrophic age blues + incomplete magenta are mirrored in
 * views.py (_CLASS_COLOR / legend swatches). Keep the two in sync.
 */
(function () {
  var DEFAULTS = {
    geomorph: '#d3e9cf', subtle: '#faf075', obvious: '#f69fa1',
    cat: '#3f67b1',                 // legacy single catastrophic blue (compat)
    catRecent: '#2b368f',           // >=2012 — deep saturated blue
    catModern: '#5479bd',           // Modern (1850–2011) — medium blue
    catHolocene: '#aecbe9',         // Holocene — pale blue
    incomplete: '#d11fa0',          // missing creep (slow) or age (catastrophic)
    patchyDot: '#d62728',           // red center for slow patchy-obvious
    off: '#9e9e9e', stroke: '#ffffff'
  };

  function palette(s) {
    s = s || {};
    return {
      cG: s.color_geomorph || DEFAULTS.geomorph,
      cS: s.color_subtle   || DEFAULTS.subtle,
      cO: s.color_obvious  || DEFAULTS.obvious,
      cC: s.color_cat      || DEFAULTS.cat,
      cCRecent:   DEFAULTS.catRecent,
      cCModern:   DEFAULTS.catModern,
      cCHolocene: DEFAULTS.catHolocene,
      INC:       DEFAULTS.incomplete,
      patchyDot: DEFAULTS.patchyDot,
      OFF: DEFAULTS.off,
      sk: s.stroke_color   || DEFAULTS.stroke,
      fOp: parseFloat(s.fill_opacity) || 0.35,
      lW:  parseFloat(s.line_width)   || 1.5,
      rSm: parseFloat(s.circle_sm)    || 3,
      rMd: parseFloat(s.circle_md)    || 5,
      rLg: parseFloat(s.circle_lg)    || 7
    };
  }

  // year_num: -1 = Holocene marker, 0 = Modern marker, else a 4-digit year (or
  // null when unknown). Coalesce null to a sentinel so numeric comparisons are
  // safe and an unknown age falls through to the "incomplete" branch.
  function _yr() { return ['coalesce', ['get', 'year_num'], -9999]; }
  function _isCat()  { return ['==', ['get', 'landslide_type'], 'catastrophic']; }
  function _isSlow() { return ['==', ['get', 'landslide_type'], 'slow']; }

  function _ageColor(P) {
    return ['case',
      ['>=', _yr(), 2012], P.cCRecent,
      ['==', _yr(), -1],   P.cCHolocene,
      ['==', _yr(), 0],    P.cCModern,
      ['all', ['>=', _yr(), 1850], ['<=', _yr(), 2011]], P.cCModern,
      ['all', ['>=', _yr(), 1],    ['<=', _yr(), 1849]], P.cCHolocene,
      P.INC];   // catastrophic with no resolvable age → incomplete
  }

  function _creepColor(P) {
    return ['match', ['get', 'creep_behavior'],
      'Obvious creep',        P.cO,
      'Patchy obvious creep', P.cS,   // yellow base; red center via PATCHY_FILTER layer
      'Subtle creep',         P.cS,
      'Geomorph creep',       P.cG,
      P.INC];   // slow with no creep distinction → incomplete
  }

  // Fill: catastrophic → age blue; slow → creep color. Used for points AND
  // polygons (polygons carry the same landslide_type/creep_behavior/year_num).
  function classFill(P) {
    return ['case', _isCat(), _ageColor(P), _creepColor(P)];
  }

  // Stroke: catastrophic gets the precursory-creep halo; slow is plain white.
  function classStroke(P) {
    return ['case', _isCat(),
      ['match', ['get', 'creep_behavior'],
        'Obvious creep',        P.cO, 'Patchy obvious creep', P.cO,
        'Subtle creep',         P.cS, 'Geomorph creep',       P.cG,
        P.sk],
      P.sk];
  }

  function classStrokeWidth() {
    return ['case',
      ['all', ['==', ['get', 'landslide_type'], 'catastrophic'],
              ['match', ['get', 'creep_behavior'],
                ['Obvious creep', 'Patchy obvious creep', 'Subtle creep', 'Geomorph creep'],
                true, false]],
      3, 1];
  }

  // Radius interpolates by zoom; size_inclusion=false halves it. `scale` shrinks
  // the whole thing (the red patchy center uses scale≈0.45 to sit inside the dot).
  function pointRadius(P, scale) {
    scale = scale || 1;
    function r(base) {
      return ['case', ['==', ['get', 'size_inclusion'], true], base * scale, base * 0.5 * scale];
    }
    return ['interpolate', ['linear'], ['zoom'],
      4, r(P.rSm), 8, r(P.rMd), 12, r(P.rLg)];
  }

  // Draw order (higher = on top), per the curated stacking:
  // >2012 catastrophic → slow obvious(+patchy) → slow subtle → Modern cat →
  // Holocene cat → slow geomorph. Everything else (incl. incomplete) sits mid.
  function pointSortKey() {
    var cat = _isCat(), slow = _isSlow(), creep = ['get', 'creep_behavior'];
    var recent = ['>=', _yr(), 2012];
    var modern = ['any', ['==', _yr(), 0], ['all', ['>=', _yr(), 1850], ['<=', _yr(), 2011]]];
    var holo   = ['any', ['==', _yr(), -1], ['all', ['>=', _yr(), 1], ['<=', _yr(), 1849]]];
    return ['case',
      ['all', cat,  recent], 60,
      ['all', slow, ['match', creep, ['Obvious creep', 'Patchy obvious creep'], true, false]], 50,
      ['all', slow, ['==', creep, 'Subtle creep']], 40,
      ['all', cat,  modern], 30,
      ['all', cat,  holo],   20,
      ['all', slow, ['==', creep, 'Geomorph creep']], 10,
      25];
  }

  function polygonFill(P) {
    // Same attribute logic as the points (catastrophic → age, slow → creep,
    // incomplete → magenta), so a polygon matches its centroid dot.
    return classFill(P);
  }

  function polygonOutline(P) {
    return ['case',
      ['==', ['get', 'role'], 'deposit'], '#1a3f80',
      ['==', ['get', 'role'], 'source'],  '#1a3f80',
      P.sk];
  }

  window.LSColors = {
    palette: palette,
    classFill: classFill,
    classStroke: classStroke,
    classStrokeWidth: classStrokeWidth,
    pointRadius: pointRadius,
    pointSortKey: pointSortKey,
    polygonFill: polygonFill,
    polygonOutline: polygonOutline,
    // Slow patchy-obvious → a small red center dot over the yellow base.
    PATCHY_FILTER: ['all',
      ['==', ['get', 'creep_behavior'], 'Patchy obvious creep'],
      ['==', ['get', 'landslide_type'], 'slow']]
  };
})();
