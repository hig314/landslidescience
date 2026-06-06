/* Per-field autosave for the landslide edit/review form.
 *
 * Each scalar field (text / number / date / checkbox / select) saves on change
 * (which fires on blur for text inputs) to manage_edit_field, with a small
 * status indicator: "saving…" → blue "✓ saved" (fades) → red on error. Fields
 * that aren't simple scalars (subset_*, polygon_role_*, polygon_primary) are
 * left to the form's "Save changes" button. Reads:
 *   window.LS_AUTOSAVE = { url, csrftoken }   (set by _autosave.html)
 * and operates on the form #ls-edit-form.
 */
(function () {
  var cfg = window.LS_AUTOSAVE;
  if (!cfg || !cfg.url) return;
  var form = document.getElementById('ls-edit-form');
  if (!form) return;

  function isExcluded(name) {
    return !name || name === 'csrfmiddlewaretoken' || name === 'polygon_primary' ||
           name.indexOf('subset_') === 0 || name.indexOf('polygon_role_') === 0;
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

  var fields = form.querySelectorAll('input, select, textarea');
  Array.prototype.forEach.call(fields, function (field) {
    if (isExcluded(field.name)) return;
    if (field.type === 'hidden' || field.type === 'submit' || field.type === 'button') return;
    field._lsBaseline = valueOf(field);
    field.addEventListener('change', function () {
      var v = valueOf(field);
      if (v === field._lsBaseline) return;   // unchanged → skip
      field._lsBaseline = v;
      save(field);
    });
  });
})();
