/* Per-field autosave for the landslide edit/review form.
 *
 * Each scalar field (text / number / date / checkbox / select) saves on change
 * (which fires on blur for text inputs) to manage_edit_field, with a small
 * status indicator: "saving…" → blue "✓ saved" (fades) → red on error. The
 * polygon role/primary table (polygon_role_<id>, polygon_primary) saves the
 * same way to its own endpoint, manage_polygon_role, which also returns the
 * primary as the server left it and the preview map's polygons. Subset
 * memberships (subset_*) are left to the form's "Save changes" button. Reads:
 *   window.LS_AUTOSAVE = { url, polyUrl, csrftoken }   (set by _autosave.html)
 * and operates on the form #ls-edit-form.
 */
(function () {
  var cfg = window.LS_AUTOSAVE;
  if (!cfg || !cfg.url) return;
  var form = document.getElementById('ls-edit-form');
  if (!form) return;

  function isExcluded(name) {
    return !name || name === 'csrfmiddlewaretoken' || name.indexOf('subset_') === 0;
  }
  function isPolygonField(name) {
    return name === 'polygon_primary' || name.indexOf('polygon_role_') === 0;
  }

  function statusEl(field) {
    if (!field._lsStatus) {
      var s = document.createElement('span');
      s.className = 'ls-save-status';
      if (field.parentNode) field.parentNode.insertBefore(s, field.nextSibling);
      field._lsStatus = s;
    }
    return field._lsStatus;
  }
  function setStatus(field, kind, text) {
    var s = statusEl(field);
    s.className = 'ls-save-status ' + (kind || '');
    s.textContent = text || '';
    s.style.opacity = '1';
    field.classList.remove('ls-field-saved', 'ls-field-error');
    if (kind === 'saved') {
      field.classList.add('ls-field-saved');
      setTimeout(function () {
        s.style.opacity = '0';
        field.classList.remove('ls-field-saved');
      }, 1400);
    } else if (kind === 'error') {
      field.classList.add('ls-field-error');
    }
  }

  function valueOf(field) {
    return field.type === 'checkbox' ? field.checked : field.value;
  }

  // Refresh rule-derived fields the server recomputed (never the field being edited).
  function applyDerived(derived) {
    if (!derived) return;
    Object.keys(derived).forEach(function (k) {
      var el = form.querySelector('[name="' + k + '"]');
      if (!el || el === document.activeElement) return;
      var v = derived[k];
      if (el.type === 'checkbox') el.checked = !!v;
      else el.value = (v == null ? '' : v);
    });
  }

  function save(field) {
    setStatus(field, 'saving', 'saving…');
    fetch(cfg.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrftoken },
      body: JSON.stringify({ name: field.name, value: valueOf(field) })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) {
          // Server may normalize the value (e.g. date → "14-Sep-2010").
          if (res.j.value != null && field !== document.activeElement) {
            field.value = res.j.value;
            field._lsBaseline = res.j.value;
          }
          applyDerived(res.j.derived);
          setStatus(field, 'saved', '✓ saved');
        } else {
          setStatus(field, 'error', (res.j && res.j.error) || 'save failed');
        }
      }).catch(function () { setStatus(field, 'error', 'save failed'); });
  }

  // A role/primary change re-runs the cascade server-side; the response says
  // which polygon is primary now (a role change normalizes it) and carries the
  // polygons, so the radios and the preview map follow without a reload.
  function savePolygon(field) {
    var status = field.type === 'radio' ? field.parentNode : field;
    var body = field.type === 'radio'
      ? { primary: field.value }
      : { polygon_id: field.name.slice('polygon_role_'.length), role: field.value };
    setStatus(status, 'saving', 'saving…');
    fetch(cfg.polyUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrftoken },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!(res.ok && res.j.ok)) {
          setStatus(status, 'error', (res.j && res.j.error) || 'save failed');
          return;
        }
        applyDerived(res.j.derived);
        Array.prototype.forEach.call(form.querySelectorAll('input[name="polygon_primary"]'),
          function (r) {
            r.checked = (res.j.primary != null && String(res.j.primary) === r.value);
            r._lsBaseline = r.checked;
          });
        var host = window.LSPolyMap;
        if (host && res.j.polygons) {
          // In place: terra_draw.js holds the same object.
          host.polygons.features = res.j.polygons.features;
          var src = host.map && host.map.getSource('lspoly');
          if (src) src.setData(host.polygons);
        }
        setStatus(status, 'saved', '✓ saved');
      }).catch(function () { setStatus(status, 'error', 'save failed'); });
  }

  var fields = form.querySelectorAll('input, select, textarea');
  Array.prototype.forEach.call(fields, function (field) {
    if (isExcluded(field.name)) return;
    if (field.type === 'hidden' || field.type === 'submit' || field.type === 'button') return;
    if (isPolygonField(field.name)) {
      if (!cfg.polyUrl) return;
      field._lsBaseline = field.type === 'radio' ? field.checked : field.value;
      field.addEventListener('change', function () {
        if (field.type === 'radio' && !field.checked) return;
        var v = field.type === 'radio' ? field.checked : field.value;
        if (v === field._lsBaseline) return;
        field._lsBaseline = v;
        savePolygon(field);
      });
      return;
    }
    field._lsBaseline = valueOf(field);
    field.addEventListener('change', function () {
      var v = valueOf(field);
      if (v === field._lsBaseline) return;   // unchanged → skip
      field._lsBaseline = v;
      save(field);
    });
  });
})();
