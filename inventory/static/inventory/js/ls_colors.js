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

  var PATCHY_FILTER = ['all',
    ['==', ['get', 'creep_behavior'], 'Patchy obvious creep'],
    ['==', ['get', 'landslide_type'], 'slow']];

  // The four landslide data layers: points, the red centre of patchy-obvious
  // points, polygon fill, polygon outline. Used by the inventory map, its
  // wiper pane and /lidar/compare/, so the symbology cannot drift. Sources
  // are named 'landslides' (centroids) and 'polygons'. o.pitchMap lays the
  // dots on the ground (a tilted 3D view) instead of facing the screen.
  function landslideLayers(P, o) {
    o = o || {};
    var flat = o.pitchMap ? { 'circle-pitch-alignment': 'map', 'circle-pitch-scale': 'map' } : {};
    function pt(paint) { return Object.assign(paint, flat); }
    return [
      { id: 'points', type: 'circle', source: 'landslides',
        layout: { 'circle-sort-key': pointSortKey() },
        paint: pt({
          'circle-color': classFill(P),
          'circle-radius': pointRadius(P, 1),
          'circle-stroke-width': classStrokeWidth(),
          'circle-stroke-color': classStroke(P),
          'circle-opacity': 0.9
        }) },
      // Slow patchy-obvious: a small red center dot over the yellow base
      // (symbolically "partly obvious"). Scaled to sit inside the dot.
      { id: 'points-patchy', type: 'circle', source: 'landslides',
        filter: PATCHY_FILTER,
        paint: pt({
          'circle-color': P.patchyDot,
          'circle-radius': pointRadius(P, 0.45),
          'circle-stroke-width': 0,
          'circle-opacity': 0.95
        }) },
      { id: 'polygon-fill', type: 'fill', source: 'polygons',
        paint: { 'fill-color': polygonFill(P), 'fill-opacity': P.fOp } },
      { id: 'polygon-outline', type: 'line', source: 'polygons',
        paint: { 'line-color': polygonOutline(P), 'line-width': P.lW } },
    ];
  }

  // A key to the colours above, for pages without the inventory sidebar's
  // per-class list: [{label, items: [{label, fill, stroke?, dot?}]}].
  function legend(P) {
    return [
      { label: 'Slow', items: [
        { label: 'obvious creep', fill: P.cO },
        { label: 'patchy obvious creep', fill: P.cS, dot: P.patchyDot },
        { label: 'subtle creep', fill: P.cS },
        { label: 'geomorphic creep', fill: P.cG }] },
      { label: 'Catastrophic', items: [
        { label: '2012 or later', fill: P.cCRecent },
        { label: '1850–2011', fill: P.cCModern },
        { label: 'before 1850', fill: P.cCHolocene },
        { label: 'with precursory creep (ring)', fill: P.cCModern, stroke: P.cO }] },
      { label: '', items: [{ label: 'incomplete classification', fill: P.INC }] }
    ];
  }

  // Process families for the OUTSIDE inventories explored from the Inventory
  // tab (inventory/external.py FAMILIES has the labels and each source's
  // type -> family map). One colour per family across every source, chosen
  // clear of our own class colours (pink / yellow / pale green / blues).
  // `z` = drawing order, rarest and most wanted on top.
  var EXT_FAMILIES = {
    RA:   { col: '#d62728', z: 7 },
    SD:   { col: '#c2185b', z: 6 },
    RSL:  { col: '#7b3fa0', z: 5 },
    FALL: { col: '#6b6b6b', z: 4 },
    FLOW: { col: '#0f8b8d', z: 3 },
    SOIL: { col: '#b8860b', z: 2 },
    PF:   { col: '#4a7c2a', z: 1 },
    OTH:  { col: '#a8a29a', z: 0 }
  };
  // Colour-by one of THEIR fields: categorical, in the field's value order;
  // blank is always this grey.
  var EXT_CATEGORICAL = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b',
    '#e377c2', '#17becf', '#bcbd22', '#393b79', '#ad494a', '#637939', '#7b4173', '#3182bd',
    '#e6550d', '#31a354'];
  var EXT_BLANK = '#c9c5bd';

  // ---- glacier extents: colour by YEAR of the observation ----------------
  // /glaciers/ draws every dated outline and terminus line with one ramp, so
  // a retreat reads as a colour sequence whichever source a line came from.
  // Dark = old, bright = recent (most of the map is ice and dark water, and
  // the newest position is the one the eye should find first). The stops are
  // not evenly spaced: nearly all observations are after 1945, and a linear
  // ramp from 1700 would spend its range on a handful of Little Ice Age lines.
  var GLACIER_YEAR_STOPS = [
    [1700, '#3b0f70'], [1850, '#641a80'], [1900, '#8c2981'], [1945, '#b73779'],
    [1965, '#de4968'], [1980, '#f66e5b'], [1990, '#fe9f6d'], [2000, '#fece91'],
    [2010, '#f6f1a9'], [2025, '#ffffff']
  ];
  // MapLibre paint expression on a numeric year property; no year -> grey.
  function glacierYearColor(prop) {
    var e = ['interpolate', ['linear'], ['to-number', ['get', prop], 0]];
    GLACIER_YEAR_STOPS.forEach(function (st) { e.push(st[0], st[1]); });
    return ['case', ['==', ['typeof', ['get', prop]], 'number'], e, '#9e9e9e'];
  }
  // The same ramp for one value, in JS (list swatches, chart points).
  function glacierYearCss(y) {
    var S = GLACIER_YEAR_STOPS;
    if (y == null || isNaN(y)) return '#9e9e9e';
    if (y <= S[0][0]) return S[0][1];
    for (var i = 1; i < S.length; i++) {
      if (y <= S[i][0]) {
        var t = (y - S[i - 1][0]) / (S[i][0] - S[i - 1][0]);
        var a = S[i - 1][1], b = S[i][1], out = '#';
        for (var k = 1; k < 7; k += 2) {
          var va = parseInt(a.substr(k, 2), 16), vb = parseInt(b.substr(k, 2), 16);
          out += ('0' + Math.round(va + t * (vb - va)).toString(16)).slice(-2);
        }
        return out;
      }
    }
    return S[S.length - 1][1];
  }

  window.LSColors = {
    GLACIER_YEAR_STOPS: GLACIER_YEAR_STOPS,
    glacierYearColor: glacierYearColor,
    glacierYearCss: glacierYearCss,
    EXT_FAMILIES: EXT_FAMILIES,
    EXT_CATEGORICAL: EXT_CATEGORICAL,
    EXT_BLANK: EXT_BLANK,
    palette: palette,
    landslideLayers: landslideLayers,
    legend: legend,
    classFill: classFill,
    classStroke: classStroke,
    classStrokeWidth: classStrokeWidth,
    pointRadius: pointRadius,
    pointSortKey: pointSortKey,
    polygonFill: polygonFill,
    polygonOutline: polygonOutline,
    // Slow patchy-obvious → a small red center dot over the yellow base.
    PATCHY_FILTER: PATCHY_FILTER
  };
})();
