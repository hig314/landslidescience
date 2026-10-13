/* ls_goto.js — paste a "lat, lon" and the map goes there. Global `window.LSGoto`.
 *
 * Added 2026-10-12, shared by /inventory/ (map.js) and /lidar/. Two ways in:
 *   - a one-button map control (⌖) that opens a small input; Enter goes,
 *     Esc closes;
 *   - pasting coordinates anywhere on the page while no text field has focus
 *     (the counterpart of right-click → copy "lat, lon").
 * The spot is marked with a ring until the next click on the map.
 *
 *   LSGoto.attach(map, {position: 'top-left', zoom: 14})
 *   LSGoto.parse('61.2, -149.9')  → {lat, lon} or null
 *
 * Accepted: "61.21, -149.90" (lat first; also space- or semicolon-separated),
 * hemisphere letters before or after ("61.21N 149.90W", "N61.21 W149.90"),
 * degrees-minutes(-seconds) ("61°12'36\"N 149°54'W", "61 12.6 N 149 54 W"),
 * and map links carrying "@lat,lon" or "q=/ll=/center=lat,lon". With no
 * hemisphere letters a first value beyond ±90 is taken as longitude (a
 * "lon, lat" paste). Otherwise the numbers are taken as written: an unsigned
 * Alaskan longitude goes to Asia, and the toast says where it went.
 */
window.LSGoto = (function () {
  'use strict';

  function fromUrl(s) {
    var m = s.match(/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/) ||
            s.match(/[?&#](?:q|ll|query|center|sll)=(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)/i);
    return m ? check(+m[1], +m[2]) : null;
  }

  function check(lat, lon) {
    if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat: lat, lon: lon };
  }

  function parse(text) {
    if (!text) return null;
    var s = String(text).trim();
    if (!s || s.length > 200) return null;
    if (/^https?:|^www\./i.test(s)) return fromUrl(s);
    s = s.replace(/[−–—]/g, '-')      // unicode minus / dashes
         .replace(/[º˚]/g, '°').replace(/[′’‘]/g, "'").replace(/[″”“]|''/g, '"');
    // Anything other than numbers, signs, separators, unit marks and N/S/E/W
    // means this is not a coordinate (so a pasted sentence is left alone).
    if (/[^0-9.,;:\s+\-°'"NSEWnsew]/.test(s)) return null;

    var re = /([-+]?\d+(?:\.\d+)?)\s*([°'"])?|([NSEWnsew])/g, m;
    var coords = [], cur = null, prefixStyle = false, prevEnd = 0;
    function fresh() { cur = { parts: [], neg: false, hemi: null, decimal: false }; coords.push(cur); }
    while ((m = re.exec(s))) {
      var gap = s.slice(prevEnd, m.index);
      prevEnd = re.lastIndex;
      if (m[3]) {                                     // a hemisphere letter
        var h = m[3].toUpperCase();
        if (!cur || (cur.parts.length && (cur.hemi || prefixStyle))) fresh();
        if (!cur.parts.length) prefixStyle = true;   // letter written before its numbers
        if (cur.hemi) return null;
        cur.hemi = h;
        continue;
      }
      var raw = m[1], v = Math.abs(parseFloat(raw)), mark = m[2] || '';
      var signed = /^[-+]/.test(raw);
      // Does this number continue the current coordinate as minutes/seconds?
      var cont = cur && cur.parts.length && cur.parts.length < 3 && !signed &&
                 !cur.decimal && v < 60 && mark !== '°' && !/[,;]/.test(gap) &&
                 !(cur.hemi && !prefixStyle);
      if (!cont) {
        if (!cur || cur.parts.length) fresh();
        cur.neg = raw.charAt(0) === '-';
      }
      cur.parts.push(v);
      if (raw.indexOf('.') >= 0) cur.decimal = true;
    }
    coords = coords.filter(function (c) { return c.parts.length; });
    if (coords.length !== 2) return null;

    var val = coords.map(function (c) {
      var d = c.parts[0] + (c.parts[1] || 0) / 60 + (c.parts[2] || 0) / 3600;
      if (c.neg || c.hemi === 'S' || c.hemi === 'W') d = -d;
      return d;
    });
    var h0 = coords[0].hemi, h1 = coords[1].hemi;
    var isLat = function (h) { return h === 'N' || h === 'S'; };
    if (h0 || h1) {
      if ((h0 && isLat(h0)) || (h1 && !isLat(h1))) return check(val[0], val[1]);
      return check(val[1], val[0]);
    }
    if (Math.abs(val[0]) > 90 && Math.abs(val[1]) <= 90) return check(val[1], val[0]);
    return check(val[0], val[1]);
  }

  function fmt(p) { return p.lat.toFixed(5) + ', ' + p.lon.toFixed(5); }

  // Same element and look as the right-click copy toast on both pages.
  function toast(map, msg) {
    var t = document.getElementById('_coord-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = '_coord-toast';
      t.style.cssText = 'position:absolute;bottom:24px;left:50%;transform:translateX(-50%);' +
        'background:rgba(20,20,20,.88);color:#fff;padding:6px 12px;border-radius:4px;' +
        'font-size:13px;z-index:5;pointer-events:none;transition:opacity .3s;opacity:0;';
      map.getContainer().appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(t._h);
    t._h = setTimeout(function () { t.style.opacity = '0'; }, 2200);
  }

  function attach(map, opts) {
    opts = opts || {};
    var zoom = opts.zoom || 14, marker = null;

    function go(p) {
      if (marker) marker.remove();
      var el = document.createElement('div');
      el.className = 'ls-goto-mark';
      marker = new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map);
      map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), zoom), essential: true });
      toast(map, '→ ' + fmt(p));
    }
    map.on('click', function () { if (marker) { marker.remove(); marker = null; } });

    var ctrl = {
      onAdd: function () {
        var box = document.createElement('div');
        box.className = 'maplibregl-ctrl maplibregl-ctrl-group ls-goto';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ls-goto-btn';
        btn.textContent = '⌖';
        btn.title = 'Go to a latitude, longitude (or paste one anywhere on the map)';
        var inp = document.createElement('input');
        inp.type = 'text';
        inp.className = 'ls-goto-in';
        inp.placeholder = 'lat, lon';
        inp.spellcheck = false;
        inp.setAttribute('autocomplete', 'off');
        box.appendChild(btn); box.appendChild(inp);
        function open() { box.classList.add('open'); inp.focus(); inp.select(); }
        function close() { box.classList.remove('open', 'bad'); inp.blur(); }
        function submit() {
          var p = parse(inp.value);
          if (!p) { box.classList.add('bad'); return; }
          box.classList.remove('bad');
          go(p); close();
        }
        btn.addEventListener('click', function () { box.classList.contains('open') ? close() : open(); });
        inp.addEventListener('keydown', function (e) {
          e.stopPropagation();                       // keep map shortcuts and LSTools' Escape out
          if (e.key === 'Enter') { e.preventDefault(); submit(); }
          else if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
        inp.addEventListener('input', function () { box.classList.remove('bad'); });
        // A paste into the box goes straight there.
        inp.addEventListener('paste', function (e) {
          var t = (e.clipboardData || window.clipboardData).getData('text');
          var p = parse(t);
          if (p) { e.preventDefault(); inp.value = t.trim(); go(p); close(); }
        });
        inp.addEventListener('blur', function () {
          setTimeout(function () { if (document.activeElement !== inp && !inp.value) close(); }, 150);
        });
        // Opens toward the map's middle whichever corner it sits in.
        setTimeout(function () {
          if (box.closest('.maplibregl-ctrl-top-right, .maplibregl-ctrl-bottom-right')) box.classList.add('right');
        }, 0);
        return box;
      },
      onRemove: function () {}
    };
    map.addControl(ctrl, opts.position || 'top-left');

    // Paste anywhere that is not a text field.
    document.addEventListener('paste', function (e) {
      var a = e.target;
      if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
      var t = (e.clipboardData || window.clipboardData);
      t = t && t.getData('text');
      var p = parse(t);
      if (!p) return;
      e.preventDefault();
      go(p);
    });
    return { go: go };
  }

  return { attach: attach, parse: parse };
})();
