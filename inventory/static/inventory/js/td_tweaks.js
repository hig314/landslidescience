/* Two corrections to Terra Draw's pointer behaviour, shared by every place
 * that draws or reshapes with it (terra_draw.js, draw_ui.js, revise.js,
 * scarps.js). Loaded straight after the Terra Draw library.
 *
 * They reach into the mode objects of terra-draw 1.0.0 (currentCoordinate,
 * closingPoints, selected, featuresAtMouseEvent, midPoints). Each is written
 * to fall back to the library's own behaviour if those are not as expected,
 * so a library upgrade can lose the correction but not break drawing. Re-read
 * this file when the pinned version changes.
 *
 *   LSTerraDraw.decisiveFinish(mode, map)   polygon or linestring mode
 *   LSTerraDraw.stickySelect(mode, map)     select mode
 */
(function () {
  var DBL_MS = 400;   // two clicks this close in time ...
  var DBL_PX = 8;     // ... and in place are a double-click
  var GRAB_PX = 12;   // reach of a vertex / midpoint handle / outline

  function evPt(ev) { return { x: ev.containerX, y: ev.containerY }; }
  function dist(a, b) { return Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)); }
  function segDist(p, a, b) {
    var dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    var t = l2 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    return dist(p, { x: a.x + t * dx, y: a.y + t * dy });
  }
  function ringOf(mode, id) {
    var g = mode.store.getGeometryCopy(id);
    if (!g) return [];
    return g.type === 'Polygon' ? g.coordinates[0] : (g.type === 'LineString' ? g.coordinates : []);
  }

  // ---- finishing takes a double-click ------------------------------------
  // Terra Draw ends a polygon on ANY click within pointerDistance of the first
  // vertex or of the vertex just placed, and a line on any click near its last
  // vertex. A second, unhurried click beside a point just drawn (to nudge it,
  // or by accident) therefore ended a long outline early (Hig, 2026-11-27).
  // Now such a click finishes only as the second half of a quick double-click;
  // a slow one is ignored (it would have been a duplicate vertex anyway).
  // Enter still finishes.

  // true = Terra Draw would finish on this click; false = it would not;
  // null = cannot tell (leave the click to the library).
  function wouldFinish(mode, map, ev) {
    var n = mode.currentCoordinate;
    if (typeof n !== 'number') return null;
    if (!mode.currentId) return false;
    if (mode.closingPoints && typeof mode.closingPoints.isClosingPoint === 'function') {
      if (n < 3) return false;
      var c = mode.closingPoints.isClosingPoint(ev);
      return !!(c.isClosing || c.isPreviousClosing);
    }
    var g = mode.store.getGeometryCopy(mode.currentId);
    if (!g || g.type !== 'LineString') return null;
    // n committed vertices and one that follows the cursor.
    if (n < 2 || g.coordinates.length < 3) return false;
    var prev = g.coordinates[g.coordinates.length - 2];
    return dist(evPt(ev), map.project(prev)) < (mode.pointerDistance || 40);
  }

  function decisiveFinish(mode, map) {
    if (!mode || typeof mode.onClick !== 'function') return mode;
    var orig = mode.onClick, last = null;
    mode.onClick = function (ev) {
      var now = Date.now(), p = evPt(ev);
      var dbl = !!last && now - last.t <= DBL_MS && dist(p, last.p) <= DBL_PX;
      last = { t: now, p: p };
      var fin = null;
      try { fin = wouldFinish(this, map, ev); } catch (e) { fin = null; }
      if (fin === null) return orig.call(this, ev);
      if (fin) {
        if (!dbl) return;                 // slow click on a closing point
        last = null;
        return orig.call(this, ev);       // the decisive double-click
      }
      // The second click of a double-click where nothing can finish yet
      // (fewer than three vertices): not another vertex on top of the last.
      if (dbl && this.currentId) return;
      return orig.call(this, ev);
    };
    return mode;
  }

  // ---- reshaping keeps its selection -------------------------------------
  // 1. A click on a vertex or on the outline of the selected shape is tested
  //    by Terra Draw as "inside the polygon?", which for a point ON the
  //    boundary comes out either way; "outside" deselected the shape being
  //    worked on. Such a click now does nothing. A click well clear of the
  //    shape still deselects.
  // 2. A midpoint handle can be dragged directly: the vertex is inserted as
  //    the drag starts, so adding a vertex and placing it is one gesture, not
  //    click-then-drag.
  function stickySelect(mode, map) {
    if (!mode) return mode;
    var click = mode.onLeftClick, dragStart = mode.onDragStart;

    if (typeof click === 'function') mode.onLeftClick = function (ev) {
      try {
        var id = this.selected && this.selected[0];
        if (id != null) {
          var hit = this.featuresAtMouseEvent.find(ev, true);
          if (!hit.clickedMidPoint && !(hit.clickedFeature && hit.clickedFeature.id)) {
            var p = evPt(ev), ring = ringOf(this, id), near = Infinity;
            for (var i = 0; i < ring.length - 1; i++) {
              near = Math.min(near, segDist(p, map.project(ring[i]), map.project(ring[i + 1])));
            }
            if (near <= GRAB_PX) return;
          }
        }
      } catch (e) { /* fall through to the library */ }
      return click.call(this, ev);
    };

    if (typeof dragStart === 'function') mode.onDragStart = function (ev, setDraggable) {
      try {
        var id = this.selected && this.selected[0];
        if (id != null) {
          var mid = this.featuresAtMouseEvent.find(ev, true).clickedMidPoint;
          if (mid) {
            var p = evPt(ev), dm = dist(p, map.project(mid.geometry.coordinates));
            var ring = ringOf(this, id), dv = Infinity;
            for (var i = 0; i < ring.length; i++) dv = Math.min(dv, dist(p, map.project(ring[i])));
            if (dm <= GRAB_PX && dm < dv) this.midPoints.insert(mid.id, this.coordinatePrecision);
          }
        }
      } catch (e) { /* fall through to the library */ }
      return dragStart.call(this, ev, setDraggable);
    };
    return mode;
  }

  window.LSTerraDraw = { decisiveFinish: decisiveFinish, stickySelect: stickySelect };
})();
