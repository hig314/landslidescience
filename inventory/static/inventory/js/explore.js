/* ============================================================================
 * Inventory explorer — /inventory/table/
 *
 * A spreadsheet over the whole inventory: per-column autofilters, multi-sort,
 * a column chooser, live aggregates, a group-by cross-tab, charts, and CSV/TSV
 * export. Everything runs client-side over the columnar payload from
 * /inventory/api/table/ (see inventory/table_data.py for its shape) — ~1,500
 * records is small enough that a filter pass is sub-millisecond, which is what
 * makes the autofilter feel like Excel rather than like a web form.
 *
 * STATE lives in one object and round-trips through the URL hash, so any view
 * you build is a link you can paste to someone. The hash grammar here is this
 * page's own (the map's ls_hash.js grammar is about map view state and shares
 * nothing with it beyond the &-separated k=v shape).
 *
 * Data model reminders:
 *   - `cat` / `multi` columns arrive dictionary-encoded (integer indices into
 *     col.dict). Filters store the *values*, not indices, so a shared link
 *     survives a data reload that renumbers the dictionary.
 *   - `bool` columns are 1/0/null and are filtered through the same value-list
 *     UI as `cat`, with the vocabulary ['Yes','No'].
 *   - null/blank is never a value: it is a separate opt-in on every filter.
 * ==========================================================================*/
(function () {
    'use strict';

    var ROW_H   = 27;    // must match .xp-table td height in explore.css
    var OVERSCAN = 10;   // rows rendered above/below the viewport

    var BOOL_DICT = ['Yes', 'No'];

    var DEFAULT_W = {
        text: 250, cat: 150, multi: 210, num: 112,
        bool: 62, date: 102, datetime: 140, link: 84
    };

    // ---- payload ---------------------------------------------------------
    var D    = null;   // whole payload
    var COL  = {};     // name -> column spec
    var NAMES = [];    // all column names in payload order
    var N    = 0;      // record count

    // ---- view state (hash-encoded) --------------------------------------
    var S = {
        q: '',
        cols: [],       // visible column names, in display order
        sort: [],       // [{c: name, d: 1|-1}]
        filters: {},    // name -> filter object
        group: null,    // {by, by2, measure, agg}
        chart: null,    // {c: name, log: bool}
        stat: 'sum',    // footer statistic
        widths: {}      // name -> px (only when user-resized)
    };

    var rows = [];      // row indices passing all filters, in sort order

    // ---- element handles -------------------------------------------------
    var $ = function (id) { return document.getElementById(id); };
    var elScroll, elTable, elColgroup, elThead, elTbody, elTfoot,
        elChips, elSummary, elEmpty, elPop, elTip, elChartbox;

    // =====================================================================
    // Small helpers
    // =====================================================================
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    function fmtNum(v) {
        if (v == null) return '';
        var a = Math.abs(v);
        if (a >= 1000 || Number.isInteger(v)) return Math.round(v).toLocaleString('en-US');
        if (a >= 1)   return v.toFixed(2);
        if (a >= 0.01) return v.toFixed(4);
        return v.toPrecision(3);
    }
    // Compact form for chart axes and pivot cells, where a 12-digit volume
    // would blow the column width.
    function fmtShort(v) {
        if (v == null) return '';
        var a = Math.abs(v);
        if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + 'B';
        if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
        if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
        return fmtNum(v);
    }
    function isNumType(t) { return t === 'num'; }
    function isCatType(t) { return t === 'cat' || t === 'bool' || t === 'multi'; }
    function isDateType(t) { return t === 'date' || t === 'datetime'; }

    // Vocabulary for a value-list filter, in display order.
    function vocab(c) { return c.type === 'bool' ? BOOL_DICT : (c.dict || []); }

    // ---- per-row value accessors ----------------------------------------
    // Categorical key(s) for row i: a string, an array of strings (multi),
    // or null when blank.
    function keyOf(c, i) {
        var v = D.data[c.name][i];
        if (c.type === 'bool') return v == null ? null : (v ? 'Yes' : 'No');
        if (c.type === 'multi') return (v && v.length) ? v.map(function (k) { return c.dict[k]; }) : null;
        if (c.type === 'cat')  return v == null ? null : c.dict[v];
        if (v == null || v === '') return null;
        return String(v);
    }
    function numOf(c, i) {
        var v = D.data[c.name][i];
        return (typeof v === 'number') ? v : null;
    }
    function strOf(c, i) {
        var v = D.data[c.name][i];
        if (v == null) return null;
        if (c.type === 'multi') return v.map(function (k) { return c.dict[k]; }).join(' ');
        if (c.type === 'cat')   return c.dict[v];
        if (c.type === 'bool')  return v ? 'Yes' : 'No';
        return String(v);
    }
    // Sort key. Nulls are returned as null and always sort last, both ways —
    // a blank is "no answer", not "smaller than everything".
    function sortOf(c, i) {
        if (c.type === 'num' || c.type === 'bool') {
            var v = D.data[c.name][i];
            return v == null ? null : v;
        }
        var s = strOf(c, i);
        return s == null ? null : s.toLowerCase();
    }

    // =====================================================================
    // Filtering
    // =====================================================================
    // `skip` lets a column's own menu compute value counts against every
    // OTHER active filter — Excel's behaviour, and the thing that makes the
    // list usable ("what is still reachable from here?").
    function passes(i, skip) {
        if (S.q) {
            var nm = D.data.unique_name ? D.data.unique_name[i] : null;
            if (!nm || nm.toLowerCase().indexOf(S.q) < 0) return false;
        }
        for (var name in S.filters) {
            if (name === skip) continue;
            var f = S.filters[name], c = COL[name];
            if (!c) continue;
            if (f.k === 'in') {
                var k = keyOf(c, i);
                if (k == null) { if (!f.blank) return false; continue; }
                if (c.type === 'multi') {
                    var hit = false;
                    for (var m = 0; m < k.length; m++) {
                        if (f.v.indexOf(k[m]) >= 0) { hit = true; break; }
                    }
                    if (!hit) return false;
                } else if (f.v.indexOf(k) < 0) return false;
            } else if (f.k === 'rg') {
                var v = isDateType(c.type) ? D.data[name][i] : numOf(c, i);
                if (v == null) { if (f.blank !== 'in' && f.blank !== 'only') return false; continue; }
                if (f.blank === 'only') return false;
                if (f.min != null && v < f.min) return false;
                if (f.max != null && v > f.max) return false;
            } else if (f.k === 'tx') {
                var s = strOf(c, i);
                if (s == null) { if (f.blank !== 'in' && f.blank !== 'only') return false; continue; }
                if (f.blank === 'only') return false;
                if (f.q && s.toLowerCase().indexOf(f.q) < 0) return false;
            }
        }
        return true;
    }

    function recompute() {
        rows = [];
        for (var i = 0; i < N; i++) if (passes(i, null)) rows.push(i);
        applySort();
    }

    function applySort() {
        if (!S.sort.length) return;
        var specs = S.sort.map(function (s) { return { c: COL[s.c], d: s.d }; })
                          .filter(function (s) { return s.c; });
        if (!specs.length) return;
        rows.sort(function (a, b) {
            for (var k = 0; k < specs.length; k++) {
                var c = specs[k].c, d = specs[k].d;
                var va = sortOf(c, a), vb = sortOf(c, b);
                if (va == null && vb == null) continue;
                if (va == null) return 1;      // blanks last regardless of dir
                if (vb == null) return -1;
                if (va < vb) return -d;
                if (va > vb) return d;
            }
            return a - b;                       // stable: fall back to record order
        });
    }

    // =====================================================================
    // Statistics
    // =====================================================================
    function stats(vals) {
        var n = vals.length;
        if (!n) return { n: 0 };
        var sorted = vals.slice().sort(function (a, b) { return a - b; });
        var sum = 0;
        for (var i = 0; i < n; i++) sum += sorted[i];
        var mid = n >> 1;
        return {
            n: n,
            min: sorted[0],
            max: sorted[n - 1],
            sum: sum,
            mean: sum / n,
            median: n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
        };
    }
    function colValues(c, idx) {
        var out = [];
        for (var i = 0; i < idx.length; i++) {
            var v = numOf(c, idx[i]);
            if (v != null) out.push(v);
        }
        return out;
    }
    function statOf(c, idx, which) {
        if (which === 'count') return idx.length;
        var st = stats(colValues(c, idx));
        return st.n ? st[which] : null;
    }

    // =====================================================================
    // Rendering — header
    // =====================================================================
    function widthOf(name) {
        return S.widths[name] || DEFAULT_W[COL[name].type] || 130;
    }

    function renderHead() {
        clear(elColgroup); clear(elThead);
        var total = 0;
        S.cols.forEach(function (name) {
            var cg = el('col');
            var w = widthOf(name);
            cg.style.width = w + 'px';
            total += w;
            elColgroup.appendChild(cg);
        });
        elTable.style.width = total + 'px';
        elTable.style.minWidth = '100%';

        var tr = el('tr');
        S.cols.forEach(function (name, ci) {
            var c = COL[name];
            var th = el('th');
            th.dataset.col = name;
            if (ci === 0) th.className = 'xp-sticky';
            if (S.filters[name]) th.className += ' xp-filtered';
            th.title = c.label + '  ·  ' + name + '  (' + c.type + ')';

            var inner = el('div', 'xp-th-inner');
            inner.appendChild(el('span', 'xp-th-label', c.label));

            var si = -1;
            for (var k = 0; k < S.sort.length; k++) if (S.sort[k].c === name) si = k;
            if (si >= 0) {
                inner.appendChild(el('span', 'xp-th-sort',
                    (S.sort[si].d > 0 ? '▲' : '▼') + (S.sort.length > 1 ? (si + 1) : '')));
            }
            inner.appendChild(el('span', 'xp-th-btn', '▼'));
            inner.addEventListener('click', function (ev) {
                if (ev.target.classList.contains('xp-th-btn')) {
                    // Must not reach the document handler below, which closes
                    // any open popover — including the one we just opened.
                    ev.stopPropagation();
                    openColMenu(name, th);
                    return;
                }
                toggleSort(name, ev.shiftKey);
            });
            th.appendChild(inner);

            var rz = el('div', 'xp-resize');
            rz.addEventListener('mousedown', function (ev) { startResize(ev, name); });
            rz.addEventListener('click', function (ev) { ev.stopPropagation(); });
            th.appendChild(rz);
            tr.appendChild(th);
        });
        elThead.appendChild(tr);
    }

    function toggleSort(name, additive) {
        var cur = null;
        for (var k = 0; k < S.sort.length; k++) if (S.sort[k].c === name) cur = S.sort[k];
        if (!additive) {
            if (cur && cur.d > 0)      S.sort = [{ c: name, d: -1 }];
            else if (cur && cur.d < 0) S.sort = [];
            else                        S.sort = [{ c: name, d: 1 }];
        } else if (cur) {
            if (cur.d > 0) cur.d = -1;
            else S.sort = S.sort.filter(function (s) { return s.c !== name; });
        } else {
            S.sort.push({ c: name, d: 1 });
        }
        recompute(); render();
    }

    function startResize(ev, name) {
        ev.preventDefault(); ev.stopPropagation();
        var x0 = ev.clientX, w0 = widthOf(name);
        function mv(e) {
            S.widths[name] = Math.max(46, Math.round(w0 + (e.clientX - x0)));
            renderHead(); renderBody(); renderFoot();
        }
        function up() {
            document.removeEventListener('mousemove', mv);
            document.removeEventListener('mouseup', up);
            writeHash();
        }
        document.addEventListener('mousemove', mv);
        document.addEventListener('mouseup', up);
    }

    // =====================================================================
    // Rendering — body (windowed: only the visible slice is in the DOM, so
    // "show every column" over 1,500 rows stays interactive)
    // =====================================================================
    var padTop, padBot;

    function cellFor(c, i, first) {
        var td = el('td');
        if (first) td.className = 'xp-sticky';
        var raw = D.data[c.name][i];

        if (first) {
            var id = D.data.id ? D.data.id[i] : null;
            var a = el('a', null, raw == null ? '(unnamed)' : String(raw));
            a.href = XP_MAP + '#id=' + id;
            a.target = '_blank';
            a.rel = 'noopener';
            a.title = 'Open on the map';
            td.appendChild(a);
            if (D.editor && id != null) {
                var g = el('a', 'xp-cog', '⚙');
                g.href = XP_EDIT + id + '/';
                g.target = '_blank';
                g.rel = 'noopener';
                g.title = 'Edit this record';
                td.appendChild(g);
            }
            return td;
        }
        if (raw == null || (c.type === 'multi' && !raw.length)) {
            td.className += (c.type === 'num' ? ' xp-num' : '') + ' xp-null';
            td.textContent = '—';
            return td;
        }
        switch (c.type) {
        case 'num':
            td.className += ' xp-num';
            td.textContent = fmtNum(raw);
            break;
        case 'bool':
            td.innerHTML = raw ? '<span class="xp-tick">✓</span>'
                               : '<span class="xp-cross">·</span>';
            break;
        case 'multi':
            raw.forEach(function (k) { td.appendChild(el('span', 'xp-pill', c.dict[k])); });
            break;
        case 'cat':
            td.textContent = c.dict[raw];
            td.title = c.dict[raw];
            break;
        case 'link':
            var la = el('a', null, '↗ open');
            la.href = raw; la.target = '_blank'; la.rel = 'noopener noreferrer';
            la.title = raw;
            td.appendChild(la);
            break;
        case 'datetime':
            td.textContent = String(raw).replace('T', ' ').slice(0, 16);
            td.title = String(raw);
            break;
        default:
            td.textContent = String(raw);
            td.title = String(raw);
        }
        return td;
    }

    function renderBody() {
        if (S.group) return;   // the pivot owns the scroll pane instead
        clear(elTbody);
        elEmpty.hidden = rows.length > 0;
        if (!rows.length) return;

        var vh    = elScroll.clientHeight || 500;
        var first = Math.max(0, Math.floor(elScroll.scrollTop / ROW_H) - OVERSCAN);
        var last  = Math.min(rows.length, first + Math.ceil(vh / ROW_H) + OVERSCAN * 2);

        padTop = el('tr'); padBot = el('tr');
        var tdT = el('td'); tdT.colSpan = S.cols.length;
        tdT.style.height = (first * ROW_H) + 'px';
        tdT.style.padding = '0'; tdT.style.border = 'none';
        padTop.appendChild(tdT);
        elTbody.appendChild(padTop);

        var frag = document.createDocumentFragment();
        for (var r = first; r < last; r++) {
            var i = rows[r];
            var tr = el('tr');
            for (var ci = 0; ci < S.cols.length; ci++) {
                tr.appendChild(cellFor(COL[S.cols[ci]], i, ci === 0));
            }
            frag.appendChild(tr);
        }
        elTbody.appendChild(frag);

        var tdB = el('td'); tdB.colSpan = S.cols.length;
        tdB.style.height = ((rows.length - last) * ROW_H) + 'px';
        tdB.style.padding = '0'; tdB.style.border = 'none';
        padBot.appendChild(tdB);
        elTbody.appendChild(padBot);
    }

    // Columns that are positions or labels-as-numbers, never quantities.
    var NO_SUM = {
        year_num: 1, centroid_lat: 1, centroid_lon: 1,
        centroid_albers_x: 1, centroid_albers_y: 1, id: 1
    };

    var STAT_LABELS = [['sum', 'Sum'], ['mean', 'Mean'], ['median', 'Median'],
                       ['min', 'Min'], ['max', 'Max'], ['n', 'Count (non-blank)']];

    function renderFoot() {
        clear(elTfoot);
        if (S.group) return;
        var tr = el('tr');
        S.cols.forEach(function (name, ci) {
            var c = COL[name], td = el('td');
            if (ci === 0) td.className = 'xp-sticky';
            if (ci === 0) {
                var sel = el('select', 'xp-foot-sel');
                STAT_LABELS.forEach(function (p) {
                    var o = el('option', null, p[1]); o.value = p[0];
                    if (p[0] === S.stat) o.selected = true;
                    sel.appendChild(o);
                });
                sel.title = 'Statistic shown under every numeric column, over the ' +
                            'records currently selected';
                sel.addEventListener('change', function () {
                    S.stat = sel.value; renderFoot(); writeHash();
                });
                td.appendChild(sel);
            } else if (isNumType(c.type)) {
                td.className += ' xp-num';
                var st = stats(colValues(c, rows));
                var v = st.n ? (S.stat === 'n' ? st.n : st[S.stat]) : null;
                // A total of years, latitudes or Albers coordinates is not a
                // quantity — better to show nothing than a confident number
                // that means nothing.
                if (S.stat === 'sum' && NO_SUM[c.name]) {
                    td.textContent = '—';
                    td.title = 'A sum of ' + c.label + ' has no meaning; ' +
                               'pick another statistic.';
                    tr.appendChild(td);
                    return;
                }
                td.textContent = v == null ? '—' : fmtNum(v);
                td.title = st.n
                    ? 'n ' + st.n + '\nmin ' + fmtNum(st.min) + '\nmedian ' +
                      fmtNum(st.median) + '\nmean ' + fmtNum(st.mean) +
                      '\nmax ' + fmtNum(st.max) + '\nsum ' + fmtNum(st.sum)
                    : 'no numeric values in the current selection';
            } else if (isCatType(c.type)) {
                var seen = {}, nb = 0;
                for (var r = 0; r < rows.length; r++) {
                    var k = keyOf(c, rows[r]);
                    if (k == null) { nb++; continue; }
                    if (c.type === 'multi') k.forEach(function (x) { seen[x] = 1; });
                    else seen[k] = 1;
                }
                var nd = Object.keys(seen).length;
                td.textContent = nd + (nd === 1 ? ' value' : ' values');
                td.title = nd + ' distinct, ' + nb + ' blank';
                td.style.fontWeight = '400';
                td.style.color = '#888';
            }
            tr.appendChild(td);
        });
        elTfoot.appendChild(tr);
    }

    // =====================================================================
    // Summary bar + filter chips
    // =====================================================================
    function renderSummary() {
        clear(elSummary);
        var cnt = el('span', 'xp-count');
        cnt.innerHTML = '<b>' + rows.length.toLocaleString('en-US') + '</b> of ' +
                        N.toLocaleString('en-US') + ' records';
        elSummary.appendChild(cnt);

        // Two headline totals — the questions asked most often of this
        // inventory. Shown only when the column exists in the payload.
        [['volume_preferred', 'Σ volume', 'm³'],
         ['area_total', 'Σ area', 'm²']].forEach(function (p) {
            var c = COL[p[0]];
            if (!c) return;
            var st = stats(colValues(c, rows));
            var s = el('span', 'xp-stat');
            s.innerHTML = p[1] + ' <b>' + (st.n ? fmtShort(st.sum) : '—') + '</b> ' + p[2];
            s.title = st.n
                ? st.n + ' of ' + rows.length + ' selected records carry a value'
                : 'no values in the current selection';
            elSummary.appendChild(s);
        });

        var sl = el('span', 'xp-stat');
        var nSlow = 0, nCat = 0;
        var tc = COL.landslide_type;
        if (tc) {
            for (var r = 0; r < rows.length; r++) {
                var k = keyOf(tc, rows[r]);
                if (k === 'slow') nSlow++; else if (k === 'catastrophic') nCat++;
            }
            sl.innerHTML = '<b>' + nSlow + '</b> slow · <b>' + nCat + '</b> catastrophic';
            elSummary.appendChild(sl);
        }
    }

    function chipText(name, f) {
        var c = COL[name];
        if (f.k === 'in') {
            var parts = f.v.slice(0, 3).join(', ');
            if (f.v.length > 3) parts += ' +' + (f.v.length - 3);
            if (f.blank) parts += (f.v.length ? ', ' : '') + '(blank)';
            return parts || '(blank)';
        }
        if (f.k === 'rg') {
            if (f.blank === 'only') return 'blank only';
            var lo = f.min == null ? '' : (isDateType(c.type) ? f.min : fmtShort(f.min));
            var hi = f.max == null ? '' : (isDateType(c.type) ? f.max : fmtShort(f.max));
            var t = (lo && hi) ? (lo + ' – ' + hi) : (lo ? '≥ ' + lo : '≤ ' + hi);
            if (f.blank === 'in') t += ' or blank';
            return t;
        }
        if (f.blank === 'only') return 'blank only';
        var t2 = f.q ? '“' + f.q + '”' : 'not blank';
        if (f.blank === 'in') t2 += ' or blank';
        return t2;
    }

    function renderChips() {
        clear(elChips);
        var names = Object.keys(S.filters);
        var any = names.length || S.q || S.sort.length;
        elChips.hidden = !any;
        if (!any) return;

        if (S.q) {
            elChips.appendChild(makeChip('Name contains', '“' + S.q + '”', function () {
                S.q = ''; $('xp-q').value = ''; refresh();
            }));
        }
        names.forEach(function (name) {
            elChips.appendChild(makeChip(COL[name].label, chipText(name, S.filters[name]),
                function () { delete S.filters[name]; refresh(); }));
        });
        S.sort.forEach(function (s) {
            elChips.appendChild(makeChip('Sorted by', COL[s.c].label +
                (s.d > 0 ? ' ▲' : ' ▼'), function () {
                S.sort = S.sort.filter(function (x) { return x.c !== s.c; });
                recompute(); render();
            }));
        });
    }

    function makeChip(label, value, onX) {
        var chip = el('span', 'xp-chip');
        chip.appendChild(el('b', null, label + ' '));
        var v = el('span', 'xp-chip-val', value);
        v.title = value;
        chip.appendChild(v);
        var x = el('button', 'xp-chip-x', '×');
        x.type = 'button';
        x.title = 'Remove';
        x.addEventListener('click', onX);
        chip.appendChild(x);
        return chip;
    }

    // =====================================================================
    // Popovers
    // =====================================================================
    function closePop() { clear(elPop); }

    function panelAt(anchor, width) {
        closePop();
        var p = el('div', 'xp-panel');
        p.style.width = (width || 260) + 'px';
        // .xp-panel is position:fixed, so these viewport coordinates from
        // getBoundingClientRect are already the right frame of reference.
        var r = anchor.getBoundingClientRect();
        var left = Math.min(r.left, window.innerWidth - (width || 260) - 10);
        p.style.left = Math.max(6, left) + 'px';
        p.style.top  = (r.bottom + 3) + 'px';
        // A header near the bottom of a short window would otherwise open a
        // panel that runs off-screen with no way to reach its Apply button.
        p.style.maxHeight = Math.max(180, window.innerHeight - r.bottom - 14) + 'px';
        p.addEventListener('click', function (e) { e.stopPropagation(); });
        elPop.appendChild(p);
        return p;
    }

    document.addEventListener('click', function () { closePop(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePop(); });

    // ---- column autofilter menu -----------------------------------------
    function openColMenu(name, th) {
        var c = COL[name];
        var p = panelAt(th, isCatType(c.type) ? 280 : 270);

        var head = el('div', 'xp-panel-head');
        head.appendChild(el('span', null, c.label));
        head.appendChild(el('span', 'xp-sub', c.type));
        p.appendChild(head);

        var sortRow = el('div', 'xp-sort-row');
        var asc  = el('button', 'xp-mini', c.type === 'num' ? '1 → 9 ▲' : 'A → Z ▲');
        var desc = el('button', 'xp-mini', c.type === 'num' ? '9 → 1 ▼' : 'Z → A ▼');
        asc.addEventListener('click', function () { S.sort = [{ c: name, d: 1 }]; closePop(); recompute(); render(); });
        desc.addEventListener('click', function () { S.sort = [{ c: name, d: -1 }]; closePop(); recompute(); render(); });
        sortRow.appendChild(asc); sortRow.appendChild(desc);

        var mvL = el('button', 'xp-mini', '←');
        mvL.title = 'Move this column left';
        mvL.addEventListener('click', function () { moveCol(name, -1); closePop(); });
        var mvR = el('button', 'xp-mini', '→');
        mvR.title = 'Move this column right';
        mvR.addEventListener('click', function () { moveCol(name, 1); closePop(); });
        var hide = el('button', 'xp-mini', 'Hide');
        hide.title = 'Remove this column from the table';
        hide.addEventListener('click', function () {
            S.cols = S.cols.filter(function (x) { return x !== name; });
            closePop(); render(); writeHash();
        });
        sortRow.appendChild(mvL); sortRow.appendChild(mvR); sortRow.appendChild(hide);
        p.appendChild(sortRow);

        var body = el('div', 'xp-panel-body');
        p.appendChild(body);

        var draft;   // filter under construction; committed by Apply
        if (isCatType(c.type)) draft = buildValueFilter(c, body);
        else if (isNumType(c.type) || isDateType(c.type)) draft = buildRangeFilter(c, body);
        else draft = buildTextFilter(c, body);

        var foot = el('div', 'xp-panel-foot');
        var apply = el('button', 'xp-mini xp-on', 'Apply');
        apply.addEventListener('click', function () {
            var f = draft();
            // Analytics: which fields people interrogate is the whole point of
            // knowing whether this page earns its place. Column name and filter
            // kind only — never the values someone typed.
            window.LSTrack && LSTrack.event(f ? 'explore_filter' : 'explore_filter_clear',
                                            { col: name, kind: f ? f.k : null });
            if (f) S.filters[name] = f; else delete S.filters[name];
            closePop(); refresh();
        });
        var clr = el('button', 'xp-mini', 'Clear');
        clr.addEventListener('click', function () {
            delete S.filters[name]; closePop(); refresh();
        });
        foot.appendChild(apply); foot.appendChild(clr);
        p.appendChild(foot);
    }

    function moveCol(name, dir) {
        var i = S.cols.indexOf(name);
        var j = i + dir;
        if (i < 0 || j < 0 || j >= S.cols.length) return;
        S.cols[i] = S.cols[j]; S.cols[j] = name;
        render(); writeHash();
    }

    // Value-list (checkbox) filter for cat / bool / multi columns. Counts are
    // computed against every OTHER active filter, so the list answers "what
    // is still reachable from here", the way Excel's does.
    function buildValueFilter(c, body) {
        var counts = {}, blankN = 0, base = [];
        for (var i = 0; i < N; i++) {
            if (!passes(i, c.name)) continue;
            base.push(i);
            var k = keyOf(c, i);
            if (k == null) { blankN++; continue; }
            if (c.type === 'multi') k.forEach(function (x) { counts[x] = (counts[x] || 0) + 1; });
            else counts[k] = (counts[k] || 0) + 1;
        }

        var f = S.filters[c.name];
        var vv = vocab(c);
        var sel = {};
        if (f && f.k === 'in') f.v.forEach(function (v) { sel[v] = 1; });
        else vv.forEach(function (v) { sel[v] = 1; });
        var blankSel = f && f.k === 'in' ? !!f.blank : true;

        var search = el('input', 'xp-inp');
        search.type = 'search';
        search.placeholder = 'Find a value…';
        if (vv.length > 12) body.appendChild(search);

        var tools = el('div', 'xp-sort-row');
        tools.style.padding = '6px 0 4px';
        tools.style.borderBottom = 'none';
        var all  = el('button', 'xp-mini', 'All');
        var none = el('button', 'xp-mini', 'None');
        var inv  = el('button', 'xp-mini', 'Invert');
        tools.appendChild(all); tools.appendChild(none); tools.appendChild(inv);
        body.appendChild(tools);

        var list = el('div', 'xp-vlist');
        body.appendChild(list);

        function paint() {
            clear(list);
            var q = search.value.trim().toLowerCase();
            vv.forEach(function (v) {
                if (q && v.toLowerCase().indexOf(q) < 0) return;
                var n = counts[v] || 0;
                var row = el('label', 'xp-vrow' + (n ? '' : ' xp-zero'));
                var cb = el('input'); cb.type = 'checkbox'; cb.checked = !!sel[v];
                cb.addEventListener('change', function () {
                    if (cb.checked) sel[v] = 1; else delete sel[v];
                });
                row.appendChild(cb);
                var nm = el('span', 'xp-vname', v); nm.title = v;
                row.appendChild(nm);
                row.appendChild(el('span', 'xp-vn', n.toLocaleString('en-US')));
                list.appendChild(row);
            });
            if (!q) {
                var brow = el('label', 'xp-vrow' + (blankN ? '' : ' xp-zero'));
                var bcb = el('input'); bcb.type = 'checkbox'; bcb.checked = blankSel;
                bcb.addEventListener('change', function () { blankSel = bcb.checked; });
                brow.appendChild(bcb);
                var bn = el('span', 'xp-vname xp-blank',
                    c.type === 'multi' ? '(none)' : '(blank)');
                brow.appendChild(bn);
                brow.appendChild(el('span', 'xp-vn', blankN.toLocaleString('en-US')));
                list.appendChild(brow);
            }
        }
        search.addEventListener('input', paint);
        all.addEventListener('click',  function () { vv.forEach(function (v) { sel[v] = 1; }); blankSel = true; paint(); });
        none.addEventListener('click', function () { sel = {}; blankSel = false; paint(); });
        inv.addEventListener('click',  function () {
            var next = {};
            vv.forEach(function (v) { if (!sel[v]) next[v] = 1; });
            sel = next; blankSel = !blankSel; paint();
        });
        paint();

        return function () {
            var chosen = vv.filter(function (v) { return sel[v]; });
            // Everything selected == no constraint; don't carry a no-op filter
            // in the chip strip or the shared URL.
            if (chosen.length === vv.length && blankSel) return null;
            return { k: 'in', v: chosen, blank: blankSel };
        };
    }

    // Numeric / date range filter, over a distribution sketch of what the
    // OTHER filters leave reachable.
    function buildRangeFilter(c, body) {
        var f = S.filters[c.name];
        var dateish = isDateType(c.type);

        var vals = [], blankN = 0;
        for (var i = 0; i < N; i++) {
            if (!passes(i, c.name)) continue;
            var v = dateish ? D.data[c.name][i] : numOf(c, i);
            if (v == null) blankN++; else vals.push(v);
        }

        var wrap = el('div', 'xp-range');
        var lo = el('input', 'xp-inp'), hi = el('input', 'xp-inp');
        lo.type = hi.type = dateish ? 'text' : 'number';
        lo.placeholder = 'min'; hi.placeholder = 'max';
        if (dateish) { lo.placeholder = 'YYYY-MM-DD'; hi.placeholder = 'YYYY-MM-DD'; }
        if (f && f.k === 'rg') {
            if (f.min != null) lo.value = f.min;
            if (f.max != null) hi.value = f.max;
        }
        wrap.appendChild(lo); wrap.appendChild(el('span', null, '–')); wrap.appendChild(hi);
        body.appendChild(wrap);

        if (!dateish && vals.length) {
            var nums = vals.slice().sort(function (a, b) { return a - b; });
            body.appendChild(sparkHist(nums, function (a, b) {
                lo.value = a; hi.value = b;
            }));
            var st = stats(nums);
            var g = el('div', 'xp-statgrid');
            [['n', st.n], ['min', st.min], ['median', st.median],
             ['mean', st.mean], ['max', st.max], ['sum', st.sum]].forEach(function (p) {
                g.appendChild(el('span', null, p[0]));
                g.appendChild(el('b', null, fmtNum(p[1])));
            });
            body.appendChild(g);
        }

        var blank = blankSelect(f, blankN);
        body.appendChild(blank.node);
        // A record with no volume is not "within 0–1e6"; entering a bound
        // means you are asking about records that have a value. The user can
        // still override — we only move the control while it is untouched.
        function autoBlank() {
            if (blank.touched()) return;
            blank.set((lo.value.trim() || hi.value.trim()) ? 'out' : 'in');
        }
        lo.addEventListener('input', autoBlank);
        hi.addEventListener('input', autoBlank);

        return function () {
            var a = lo.value.trim(), b = hi.value.trim();
            var min = a === '' ? null : (dateish ? a : parseFloat(a));
            var max = b === '' ? null : (dateish ? b : parseFloat(b));
            if (!dateish) {
                if (min != null && !isFinite(min)) min = null;
                if (max != null && !isFinite(max)) max = null;
            }
            var bl = blank.value();
            if (min == null && max == null && bl === 'in') return null;
            return { k: 'rg', min: min, max: max, blank: bl };
        };
    }

    function buildTextFilter(c, body) {
        var f = S.filters[c.name];
        var blankN = 0;
        for (var i = 0; i < N; i++) {
            if (!passes(i, c.name)) continue;
            if (strOf(c, i) == null) blankN++;
        }
        var inp = el('input', 'xp-inp');
        inp.type = 'search';
        inp.placeholder = 'contains…';
        if (f && f.k === 'tx') inp.value = f.q || '';
        body.appendChild(inp);
        var blank = blankSelect(f, blankN);
        body.appendChild(blank.node);
        inp.addEventListener('input', function () {
            if (!blank.touched()) blank.set(inp.value.trim() ? 'out' : 'in');
        });
        return function () {
            var q = inp.value.trim().toLowerCase();
            var bl = blank.value();
            if (!q && bl === 'in') return null;
            return { k: 'tx', q: q, blank: bl };
        };
    }

    // Shared blank handling: every filter type gets the same three-way choice
    // so "records with no value" is always an explicit decision, never an
    // accident of how a comparison treats null.
    function blankSelect(f, blankN) {
        var wrap = el('div', 'xp-field');
        wrap.style.marginTop = '8px';
        wrap.appendChild(el('label', null, 'Blanks (' + blankN + ')'));
        var sel = el('select');
        [['in', 'Include'], ['out', 'Exclude'], ['only', 'Only blanks']]
            .forEach(function (p) {
                var o = el('option', null, p[1]); o.value = p[0];
                sel.appendChild(o);
            });
        sel.value = (f && f.blank) ? f.blank : 'in';
        var touched = false;
        sel.addEventListener('change', function () { touched = true; });
        wrap.appendChild(sel);
        return {
            node: wrap,
            value:   function () { return sel.value; },
            touched: function () { return touched; },
            set:     function (v) { sel.value = v; }
        };
    }

    // 24-bin sketch under the range inputs; click-drag selects a range.
    function sparkHist(sorted, onPick) {
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'xp-hist');
        svg.setAttribute('viewBox', '0 0 240 54');
        svg.setAttribute('preserveAspectRatio', 'none');
        var min = sorted[0], max = sorted[sorted.length - 1];
        if (min === max) { max = min + 1; }
        var NB = 24, bins = new Array(NB).fill(0);
        for (var i = 0; i < sorted.length; i++) {
            var b = Math.min(NB - 1, Math.floor((sorted[i] - min) / (max - min) * NB));
            bins[b]++;
        }
        var peak = Math.max.apply(null, bins) || 1;
        bins.forEach(function (n, b) {
            var r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            var h = Math.max(n ? 1 : 0, Math.round(n / peak * 50));
            r.setAttribute('x', (b * 10) + 0.5);
            r.setAttribute('y', 52 - h);
            r.setAttribute('width', 9);
            r.setAttribute('height', h);
            r.style.cursor = 'pointer';
            var a = min + (max - min) * b / NB, z = min + (max - min) * (b + 1) / NB;
            r.addEventListener('click', function () {
                onPick(Math.floor(a), Math.ceil(z));
            });
            var t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
            t.textContent = fmtShort(a) + ' – ' + fmtShort(z) + ': ' + n;
            r.appendChild(t);
            svg.appendChild(r);
        });
        return svg;
    }

    // =====================================================================
    // Column chooser
    // =====================================================================
    function openColumns(btn) {
        var p = panelAt(btn, 320);
        p.appendChild(headWith('Columns', S.cols.length + ' of ' + NAMES.length + ' shown'));
        var body = el('div', 'xp-panel-body');

        var search = el('input', 'xp-inp');
        search.type = 'search'; search.placeholder = 'Find a column…';
        body.appendChild(search);

        var host = el('div');
        body.appendChild(host);
        p.appendChild(body);

        function paint() {
            clear(host);
            var q = search.value.trim().toLowerCase();
            D.groups.forEach(function (g) {
                var mine = NAMES.filter(function (n) {
                    return COL[n].group === g.key &&
                           (!q || COL[n].label.toLowerCase().indexOf(q) >= 0 ||
                                  n.indexOf(q) >= 0);
                });
                if (!mine.length) return;
                host.appendChild(el('div', 'xp-colgroup-title', g.title));
                mine.forEach(function (n) {
                    var row = el('label', 'xp-vrow');
                    var cb = el('input'); cb.type = 'checkbox';
                    cb.checked = S.cols.indexOf(n) >= 0;
                    cb.addEventListener('change', function () {
                        if (cb.checked) { if (S.cols.indexOf(n) < 0) S.cols.push(n); }
                        else S.cols = S.cols.filter(function (x) { return x !== n; });
                        // Never leave the table with no columns at all.
                        if (!S.cols.length) S.cols = ['unique_name'];
                        render(); writeHash();
                    });
                    row.appendChild(cb);
                    var nm = el('span', 'xp-vname', COL[n].label);
                    nm.title = n + ' · ' + COL[n].type;
                    row.appendChild(nm);
                    if (S.filters[n]) row.appendChild(el('span', 'xp-vn', '▼ filtered'));
                    host.appendChild(row);
                });
            });
        }
        search.addEventListener('input', paint);
        paint();

        var foot = el('div', 'xp-panel-foot');
        var addAll = el('button', 'xp-mini', 'Show all');
        addAll.addEventListener('click', function () {
            S.cols = NAMES.slice(); render(); writeHash(); paint();
        });
        var def = el('button', 'xp-mini', 'Default set');
        def.addEventListener('click', function () {
            S.cols = D.presets['default'].slice(); render(); writeHash(); paint();
        });
        foot.appendChild(addAll); foot.appendChild(def);
        p.appendChild(foot);
    }

    function headWith(title, sub) {
        var h = el('div', 'xp-panel-head');
        h.appendChild(el('span', null, title));
        if (sub) h.appendChild(el('span', 'xp-sub', sub));
        return h;
    }

    var PRESET_LABELS = {
        'default': 'Default', events: 'Catastrophic events', creep: 'Creep evidence',
        volume: 'Volumes & areas', location: 'Location'
    };

    function openPresets(btn) {
        var p = panelAt(btn, 220);
        p.appendChild(headWith('Column presets'));
        var body = el('div', 'xp-panel-body');
        Object.keys(D.presets).forEach(function (k) {
            var b = el('button', 'xp-mini', PRESET_LABELS[k] || k);
            b.style.display = 'block';
            b.style.width = '100%';
            b.style.textAlign = 'left';
            b.style.marginBottom = '4px';
            b.addEventListener('click', function () {
                window.LSTrack && LSTrack.event('explore_preset', { preset: k });
                S.cols = D.presets[k].slice();
                closePop(); render(); writeHash();
            });
            body.appendChild(b);
        });
        var all = el('button', 'xp-mini', 'Everything (' + NAMES.length + ')');
        all.style.display = 'block'; all.style.width = '100%';
        all.style.textAlign = 'left';
        all.addEventListener('click', function () {
            S.cols = NAMES.slice(); closePop(); render(); writeHash();
        });
        body.appendChild(all);
        p.appendChild(body);
    }

    // =====================================================================
    // Group-by cross-tab
    // =====================================================================
    // Only columns with a manageable number of distinct values can be a
    // grouping axis — otherwise the "pivot" is just the table with extra
    // steps. Computed once, from the data itself, so it stays right as the
    // inventory grows.
    var GROUPABLE_MAX = 200;

    function groupable() {
        return NAMES.filter(function (n) { return COL[n].ndist <= GROUPABLE_MAX; });
    }
    function measurable() {
        return NAMES.filter(function (n) { return isNumType(COL[n].type); });
    }

    function openGroup(btn) {
        var p = panelAt(btn, 280);
        p.appendChild(headWith('Group by', 'cross-tab of the current selection'));
        var body = el('div', 'xp-panel-body');
        var g = S.group || { by: '', by2: '', measure: '', agg: 'count' };

        function picker(label, value, options, blankLabel) {
            var f = el('div', 'xp-field');
            f.appendChild(el('label', null, label));
            var sel = el('select');
            var o0 = el('option', null, blankLabel); o0.value = '';
            sel.appendChild(o0);
            options.forEach(function (n) {
                var o = el('option', null, COL[n].label); o.value = n;
                if (n === value) o.selected = true;
                sel.appendChild(o);
            });
            f.appendChild(sel);
            body.appendChild(f);
            return sel;
        }
        var byS  = picker('Rows', g.by, groupable(), '— pick a column —');
        var by2S = picker('Columns (optional)', g.by2, groupable(), '— none —');
        var mS   = picker('Measure', g.measure, measurable(), 'Record count');

        var aggF = el('div', 'xp-field');
        aggF.appendChild(el('label', null, 'Aggregate'));
        var aggS = el('select');
        [['count', 'Count'], ['sum', 'Sum'], ['mean', 'Mean'], ['median', 'Median'],
         ['min', 'Min'], ['max', 'Max']].forEach(function (pr) {
            var o = el('option', null, pr[1]); o.value = pr[0];
            if (pr[0] === g.agg) o.selected = true;
            aggS.appendChild(o);
        });
        aggF.appendChild(aggS);
        body.appendChild(aggF);
        body.appendChild(el('div', 'xp-note',
            'Multi-value columns (subsets) count a record once under each of ' +
            'its values, so the column totals can exceed the record count.'));
        p.appendChild(body);

        var foot = el('div', 'xp-panel-foot');
        var go = el('button', 'xp-mini xp-on', 'Apply');
        go.addEventListener('click', function () {
            if (byS.value) {
                window.LSTrack && LSTrack.event('explore_group', {
                    by: byS.value, by2: by2S.value || null,
                    measure: mS.value || null, agg: mS.value ? aggS.value : 'count'
                });
            }
            if (!byS.value) { S.group = null; }
            else S.group = {
                by: byS.value, by2: by2S.value || null,
                measure: mS.value || null,
                agg: mS.value ? aggS.value : 'count'
            };
            closePop(); render(); writeHash();
        });
        var off = el('button', 'xp-mini', 'Back to rows');
        off.addEventListener('click', function () {
            S.group = null; closePop(); render(); writeHash();
        });
        foot.appendChild(go); foot.appendChild(off);
        p.appendChild(foot);
    }

    // Expand one row into the group keys it belongs to (multi-value columns
    // land in several; blanks land in a single explicit "(blank)" bucket so
    // they are visible rather than dropped).
    function groupKeys(c, i) {
        var k = keyOf(c, i);
        if (k == null) return ['(blank)'];
        if (Array.isArray(k)) return k.length ? k : ['(none)'];
        if (isDateType(c.type)) return [String(k).slice(0, 4)];
        return [String(k)];
    }

    function renderPivot() {
        clear(elTbody); clear(elThead); clear(elTfoot); clear(elColgroup);
        elTable.style.width = '';
        elEmpty.hidden = true;

        var g = S.group;
        var cBy  = COL[g.by], cBy2 = g.by2 ? COL[g.by2] : null;
        var cM   = g.measure ? COL[g.measure] : null;

        var cells = {}, rowKeys = {}, colKeys = {};
        rows.forEach(function (i) {
            var rk = groupKeys(cBy, i);
            var ck = cBy2 ? groupKeys(cBy2, i) : ['__all__'];
            rk.forEach(function (a) {
                rowKeys[a] = 1;
                ck.forEach(function (b) {
                    colKeys[b] = 1;
                    var key = a + '' + b;
                    (cells[key] || (cells[key] = [])).push(i);
                });
            });
        });

        var rk = Object.keys(rowKeys).sort(cmpKey);
        var ck = Object.keys(colKeys).sort(cmpKey);

        function agg(idx) {
            if (!idx || !idx.length) return null;
            if (!cM || g.agg === 'count') return idx.length;
            return statOf(cM, idx, g.agg === 'n' ? 'n' : g.agg);
        }

        // Peak drives the in-cell heat bar; grand totals are excluded so one
        // big total doesn't flatten every real cell to invisible.
        var peak = 0;
        rk.forEach(function (a) {
            ck.forEach(function (b) {
                var v = agg(cells[a + '' + b]);
                if (v != null && v > peak) peak = v;
            });
        });

        var tbl = el('table', 'xp-pivot');
        var thead = el('thead'), htr = el('tr');
        htr.appendChild(thHead(cBy.label, 'xp-rowhead'));
        if (cBy2) ck.forEach(function (b) { htr.appendChild(thHead(b)); });
        else htr.appendChild(thHead(measureLabel()));
        htr.appendChild(thHead('Total', 'xp-total'));
        thead.appendChild(htr);
        tbl.appendChild(thead);

        var tb = el('tbody');
        rk.forEach(function (a) {
            var tr = el('tr');
            var rh = el('td', 'xp-rowhead'); rh.textContent = a; rh.title = a;
            tr.appendChild(rh);
            var rowIdx = [];
            ck.forEach(function (b) {
                var idx = cells[a + '' + b] || [];
                rowIdx = rowIdx.concat(idx);
                tr.appendChild(pivotCell(agg(idx), peak));
            });
            tr.appendChild(pivotCell(agg(dedupe(rowIdx)), 0, 'xp-total'));
            tb.appendChild(tr);
        });
        tbl.appendChild(tb);

        var tf = el('tfoot'), ftr = el('tr');
        var fh = el('td', 'xp-rowhead xp-total'); fh.textContent = 'Total';
        ftr.appendChild(fh);
        ck.forEach(function (b) {
            var idx = [];
            rk.forEach(function (a) { idx = idx.concat(cells[a + '' + b] || []); });
            ftr.appendChild(pivotCell(agg(dedupe(idx)), 0, 'xp-total'));
        });
        ftr.appendChild(pivotCell(agg(rows), 0, 'xp-total'));
        tf.appendChild(ftr);
        tbl.appendChild(tf);

        var host = el('div');
        host.appendChild(tbl);
        var note = el('div', 'xp-note');
        note.style.margin = '0 12px 16px';
        note.textContent = measureLabel() + ' over the ' + rows.length +
            ' records currently selected' +
            (cBy2 ? ', by ' + cBy.label + ' × ' + cBy2.label : ', by ' + cBy.label) + '.';
        host.appendChild(note);

        // The pivot replaces the virtualized table inside the same scroll pane.
        var old = document.getElementById('xp-pivot-host');
        if (old) old.remove();
        host.id = 'xp-pivot-host';
        elScroll.appendChild(host);

        function measureLabel() {
            if (!cM || g.agg === 'count') return 'Record count';
            return g.agg.charAt(0).toUpperCase() + g.agg.slice(1) + ' of ' + cM.label;
        }
        function thHead(t, cls) {
            var th = el('th', cls); th.textContent = t; th.title = t; return th;
        }
    }

    function dedupe(idx) {
        var seen = {}, out = [];
        for (var i = 0; i < idx.length; i++) {
            if (!seen[idx[i]]) { seen[idx[i]] = 1; out.push(idx[i]); }
        }
        return out;
    }
    // Numbers sort numerically, "(blank)" sinks to the bottom, everything
    // else alphabetically.
    function cmpKey(a, b) {
        if (a === '(blank)' || a === '(none)') return 1;
        if (b === '(blank)' || b === '(none)') return -1;
        var na = parseFloat(a), nb = parseFloat(b);
        if (isFinite(na) && isFinite(nb) && String(na) === a && String(nb) === b) return na - nb;
        return a < b ? -1 : (a > b ? 1 : 0);
    }
    function pivotCell(v, peak, cls) {
        var td = el('td', cls || (peak ? 'xp-heat' : ''));
        if (v == null) { td.textContent = '—'; td.style.color = '#c4c4c4'; return td; }
        if (peak) {
            var bar = el('div', 'xp-heatbar');
            bar.style.width = Math.max(2, Math.round(v / peak * 100)) + '%';
            td.appendChild(bar);
        }
        var span = el('span', 'xp-heatval', fmtShort(v));
        span.title = fmtNum(v);
        td.appendChild(span);
        return td;
    }

    // =====================================================================
    // Chart strip
    // =====================================================================
    function openChart(btn) {
        var p = panelAt(btn, 260);
        p.appendChild(headWith('Chart', 'over the current selection'));
        var body = el('div', 'xp-panel-body');
        var f = el('div', 'xp-field');
        f.appendChild(el('label', null, 'Column'));
        var sel = el('select');
        var o0 = el('option', null, '— none —'); o0.value = '';
        sel.appendChild(o0);
        NAMES.forEach(function (n) {
            var c = COL[n];
            if (c.type === 'text' || c.type === 'link') return;
            var o = el('option', null, c.label); o.value = n;
            if (S.chart && S.chart.c === n) o.selected = true;
            sel.appendChild(o);
        });
        f.appendChild(sel);
        body.appendChild(f);

        var lg = el('label', 'xp-vrow');
        var lcb = el('input'); lcb.type = 'checkbox';
        lcb.checked = !!(S.chart && S.chart.log);
        lg.appendChild(lcb);
        lg.appendChild(el('span', 'xp-vname', 'Log bins (numeric only)'));
        body.appendChild(lg);
        body.appendChild(el('div', 'xp-note',
            'Numeric columns histogram; categorical columns show counts per value. ' +
            'Areas and volumes span several orders of magnitude — log bins are ' +
            'usually the readable choice there.'));
        p.appendChild(body);

        var foot = el('div', 'xp-panel-foot');
        var go = el('button', 'xp-mini xp-on', 'Show');
        go.addEventListener('click', function () {
            if (sel.value) {
                window.LSTrack && LSTrack.event('explore_chart',
                                                { col: sel.value, log: lcb.checked });
            }
            S.chart = sel.value ? { c: sel.value, log: lcb.checked } : null;
            closePop(); renderChart(); writeHash();
        });
        var off = el('button', 'xp-mini', 'Hide');
        off.addEventListener('click', function () {
            S.chart = null; closePop(); renderChart(); writeHash();
        });
        foot.appendChild(go); foot.appendChild(off);
        p.appendChild(foot);
    }

    function renderChart() {
        clear(elChartbox);
        $('xp-chart-btn').classList.toggle('xp-on', !!S.chart);
        if (!S.chart || !COL[S.chart.c]) { elChartbox.hidden = true; return; }
        elChartbox.hidden = false;
        var c = COL[S.chart.c];

        var head = el('div', 'xp-chart-head');
        head.appendChild(el('strong', null, c.label));
        var bars = isNumType(c.type) || isDateType(c.type)
            ? buildNumBars(c) : buildCatBars(c);
        head.appendChild(el('span', null, bars.caption));
        var x = el('button', 'xp-mini', '×');
        x.style.marginLeft = 'auto';
        x.addEventListener('click', function () { S.chart = null; renderChart(); writeHash(); });
        head.appendChild(x);
        elChartbox.appendChild(head);
        elChartbox.appendChild(svgBars(bars.items, bars.clickable));
    }

    function buildCatBars(c) {
        var counts = {}, blank = 0;
        rows.forEach(function (i) {
            var k = keyOf(c, i);
            if (k == null) { blank++; return; }
            if (Array.isArray(k)) k.forEach(function (x) { counts[x] = (counts[x] || 0) + 1; });
            else counts[k] = (counts[k] || 0) + 1;
        });
        var items = Object.keys(counts).map(function (k) {
            return { label: k, value: counts[k], filter: { k: 'in', v: [k], blank: false } };
        }).sort(function (a, b) { return b.value - a.value; }).slice(0, 40);
        if (blank) items.push({ label: '(blank)', value: blank,
                                filter: { k: 'in', v: [], blank: true } });
        return { items: items, caption: items.length + ' values · click a bar to filter',
                 clickable: c.name };
    }

    function buildNumBars(c) {
        var dateish = isDateType(c.type);
        var vals = [];
        rows.forEach(function (i) {
            var v = dateish ? D.data[c.name][i] : numOf(c, i);
            if (v == null) return;
            vals.push(dateish ? parseInt(String(v).slice(0, 4), 10) : v);
        });
        if (!vals.length) return { items: [], caption: 'no values in selection' };
        vals.sort(function (a, b) { return a - b; });
        var min = vals[0], max = vals[vals.length - 1];
        var useLog = S.chart.log && min > 0 && max / min > 100;
        var NB = 28;
        if (min === max) return {
            items: [{ label: fmtShort(min), value: vals.length }],
            caption: 'single value ' + fmtNum(min)
        };
        var lo = useLog ? Math.log10(min) : min;
        var hi = useLog ? Math.log10(max) : max;
        var bins = new Array(NB).fill(0);
        vals.forEach(function (v) {
            var t = useLog ? Math.log10(v) : v;
            bins[Math.min(NB - 1, Math.floor((t - lo) / (hi - lo) * NB))]++;
        });
        var items = bins.map(function (n, b) {
            var a = lo + (hi - lo) * b / NB, z = lo + (hi - lo) * (b + 1) / NB;
            if (useLog) { a = Math.pow(10, a); z = Math.pow(10, z); }
            return {
                label: fmtShort(a), value: n,
                tip: fmtShort(a) + ' – ' + fmtShort(z) + ': ' + n + ' records',
                filter: { k: 'rg', min: dateish ? String(Math.floor(a)) + '-01-01' : a,
                          max: dateish ? String(Math.ceil(z)) + '-12-31' : z, blank: 'out' }
            };
        });
        return {
            items: items,
            caption: 'n ' + vals.length + ' · ' + fmtShort(min) + ' – ' + fmtShort(max) +
                     (useLog ? ' · log bins' : ''),
            clickable: c.name
        };
    }

    function svgBars(items, clickCol) {
        // viewBox matches the element's real pixel width, so the default
        // uniform scaling is 1:1 and the axis labels are not stretched. (An
        // earlier preserveAspectRatio="none" made the text wider on wide
        // screens.) renderChart re-runs on resize to keep them in step.
        var H = 180, padB = 26, padL = 34;
        var W = Math.max(360, (elChartbox.clientWidth || 900) - 24);
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'xp-chart-svg');
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
        if (!items.length) return svg;

        var peak = Math.max.apply(null, items.map(function (d) { return d.value; })) || 1;
        var bw = (W - padL - 6) / items.length;
        var plotH = H - padB - 8;

        var ax = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        ax.setAttribute('class', 'xp-ax');
        ax.setAttribute('x1', padL); ax.setAttribute('x2', W - 4);
        ax.setAttribute('y1', H - padB); ax.setAttribute('y2', H - padB);
        svg.appendChild(ax);
        svg.appendChild(svgText(4, 16, fmtShort(peak), 'start'));
        svg.appendChild(svgText(4, H - padB, '0', 'start'));

        items.forEach(function (d, k) {
            var h = Math.max(d.value ? 1 : 0, Math.round(d.value / peak * plotH));
            var r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            r.setAttribute('class', 'xp-bar');
            r.setAttribute('x', padL + k * bw + 0.5);
            r.setAttribute('y', H - padB - h);
            r.setAttribute('width', Math.max(1, bw - 1.5));
            r.setAttribute('height', h);
            if (clickCol && d.filter) {
                r.style.cursor = 'pointer';
                r.addEventListener('click', function () {
                    S.filters[clickCol] = d.filter;
                    refresh();
                });
            }
            r.addEventListener('mousemove', function (e) {
                showTip(e, d.tip || (d.label + ': ' + d.value.toLocaleString('en-US')));
            });
            r.addEventListener('mouseleave', hideTip);
            svg.appendChild(r);
            // Label every bar when they fit, otherwise every fourth.
            if (bw > 42 || k % Math.ceil(46 / bw) === 0) {
                svg.appendChild(svgText(padL + k * bw + bw / 2, H - padB + 13,
                    String(d.label).slice(0, 14), 'middle'));
            }
        });
        return svg;
    }

    function svgText(x, y, t, anchor) {
        var e = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        e.setAttribute('x', x); e.setAttribute('y', y);
        e.setAttribute('text-anchor', anchor || 'start');
        e.textContent = t;
        return e;
    }
    function showTip(e, text) {
        elTip.textContent = text;
        elTip.hidden = false;
        elTip.style.left = Math.min(e.clientX + 14, window.innerWidth - 240) + 'px';
        elTip.style.top  = (e.clientY - 30) + 'px';
    }
    function hideTip() { elTip.hidden = true; }

    // =====================================================================
    // Export
    // =====================================================================
    // Export carries RAW values, not the display strings: a number stays a
    // number, a date stays ISO, a boolean is TRUE/FALSE. The point of the
    // download is to keep working on the data elsewhere.
    function exportValue(c, i) {
        var v = D.data[c.name][i];
        if (v == null) return '';
        if (c.type === 'bool')  return v ? 'TRUE' : 'FALSE';
        if (c.type === 'cat')   return c.dict[v];
        if (c.type === 'multi') return v.map(function (k) { return c.dict[k]; }).join('; ');
        return String(v);
    }
    function buildDelim(sep) {
        var cols = S.cols.map(function (n) { return COL[n]; });
        var head = cols.map(function (c) { return c.label; });
        // The id is not usually a visible column, but an export without a
        // stable key is a dead end — prepend it.
        var lines = [['id'].concat(head).map(function (s) { return q(s, sep); }).join(sep)];
        rows.forEach(function (i) {
            var vals = [String(D.data.id ? D.data.id[i] : '')];
            cols.forEach(function (c) { vals.push(exportValue(c, i)); });
            lines.push(vals.map(function (s) { return q(s, sep); }).join(sep));
        });
        return lines.join('\r\n');
        function q(s, sp) {
            s = String(s);
            if (sp === '\t') return s.replace(/[\t\r\n]/g, ' ');
            return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        }
    }

    function openDownload(btn) {
        var p = panelAt(btn, 250);
        p.appendChild(headWith('Data', rows.length + ' rows × ' + S.cols.length + ' columns'));
        var body = el('div', 'xp-panel-body');

        add('⬇  Download CSV', function () {
            window.LSTrack && LSTrack.event('download',
                                            { kind: 'explore_csv', rows: rows.length });
            var blob = new Blob([buildDelim(',')], { type: 'text/csv;charset=utf-8' });
            var a = el('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'inventory_selection_' + stamp() + '.csv';
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
        });
        add('⧉  Copy for Excel (TSV)', function (b) {
            window.LSTrack && LSTrack.event('download',
                                            { kind: 'explore_tsv', rows: rows.length });
            var text = buildDelim('\t');
            navigator.clipboard.writeText(text).then(function () {
                b.textContent = '✓  Copied ' + rows.length + ' rows';
            }).catch(function () {
                b.textContent = '✗  Clipboard blocked — use CSV';
            });
        });
        add('⬇  Full inventory (GeoJSON zip)', function () {
            window.location.href = '/inventory/export/';
        });
        body.appendChild(el('div', 'xp-note',
            'CSV and TSV carry exactly what is on screen: the filtered rows, the ' +
            'visible columns, raw values, plus the record id. The zip is the ' +
            'complete inventory with geometry and QGIS styles.'));
        p.appendChild(body);

        function add(label, fn) {
            var b = el('button', 'xp-mini', label);
            b.style.display = 'block'; b.style.width = '100%';
            b.style.textAlign = 'left'; b.style.marginBottom = '5px';
            b.addEventListener('click', function () { fn(b); });
            body.appendChild(b);
        }
        function stamp() {
            var d = new Date();
            return d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') +
                   String(d.getDate()).padStart(2, '0');
        }
    }

    // ---- hand the selection to the map ----------------------------------
    var MAP_ID_LIMIT = 1200;   // beyond this the fragment gets unwieldy

    function showOnMap() {
        if (!D.data.id) return;
        if (rows.length === N) { window.open(XP_MAP, '_blank', 'noopener'); return; }
        if (rows.length > MAP_ID_LIMIT) {
            alert('That is ' + rows.length + ' records — too many to hand to the map ' +
                  'in a URL. Narrow the selection below ' + MAP_ID_LIMIT + ' first.');
            return;
        }
        if (!rows.length) { alert('Nothing selected.'); return; }
        window.LSTrack && LSTrack.event('explore_to_map', { n: rows.length });
        var ids = rows.map(function (i) { return D.data.id[i]; }).join(',');
        window.open(XP_MAP + '#ids=' + ids, '_blank', 'noopener');
    }

    // =====================================================================
    // URL hash — the whole view state, so any table you build is a link.
    //
    // Grammar (all values percent-encoded where they can contain a separator):
    //   q=<name search>
    //   c=<col,col,…>                     visible columns, in order
    //   s=<col:a|d,…>                     sort keys, in precedence order
    //   f=<col~in~v1|v2|_b ; col~rg~min|max|blank ; col~tx~q|blank>
    //   g=<by,by2,measure,agg>
    //   ch=<col,log>
    //   st=<footer statistic>
    //   w=<col:px,…>
    // =====================================================================
    var BLANK_TOKEN = '_b';
    var hashTimer = null;

    function writeHash() {
        clearTimeout(hashTimer);
        hashTimer = setTimeout(function () {
            var parts = [];
            if (S.q) parts.push('q=' + encodeURIComponent(S.q));
            parts.push('c=' + S.cols.join(','));
            if (S.sort.length) {
                parts.push('s=' + S.sort.map(function (x) {
                    return x.c + ':' + (x.d > 0 ? 'a' : 'd');
                }).join(','));
            }
            var fs = [];
            Object.keys(S.filters).forEach(function (n) {
                var f = S.filters[n];
                if (f.k === 'in') {
                    var vs = f.v.map(encodeURIComponent);
                    if (f.blank) vs.push(BLANK_TOKEN);
                    fs.push(n + '~in~' + vs.join('|'));
                } else if (f.k === 'rg') {
                    fs.push(n + '~rg~' + (f.min == null ? '' : f.min) + '|' +
                            (f.max == null ? '' : f.max) + '|' + f.blank);
                } else {
                    fs.push(n + '~tx~' + encodeURIComponent(f.q || '') + '|' + f.blank);
                }
            });
            if (fs.length) parts.push('f=' + fs.join(';'));
            if (S.group) {
                parts.push('g=' + [S.group.by, S.group.by2 || '',
                                   S.group.measure || '', S.group.agg].join(','));
            }
            if (S.chart) parts.push('ch=' + S.chart.c + ',' + (S.chart.log ? '1' : '0'));
            if (S.stat !== 'sum') parts.push('st=' + S.stat);
            var ws = Object.keys(S.widths).map(function (n) { return n + ':' + S.widths[n]; });
            if (ws.length) parts.push('w=' + ws.join(','));
            history.replaceState(null, '', '#' + parts.join('&'));
        }, 180);
    }

    function readHash() {
        var h = location.hash.replace(/^#/, '');
        if (!h) return false;
        var got = false;
        h.split('&').forEach(function (kv) {
            var i = kv.indexOf('=');
            if (i < 0) return;
            var k = kv.slice(0, i), v = kv.slice(i + 1);
            if (k === 'q') { S.q = decodeURIComponent(v).toLowerCase(); got = true; }
            else if (k === 'c') {
                var cs = v.split(',').filter(function (n) { return COL[n]; });
                if (cs.length) { S.cols = cs; got = true; }
            } else if (k === 's') {
                S.sort = v.split(',').map(function (t) {
                    var b = t.split(':');
                    return COL[b[0]] ? { c: b[0], d: b[1] === 'd' ? -1 : 1 } : null;
                }).filter(Boolean);
                got = true;
            } else if (k === 'f') {
                v.split(';').forEach(function (t) {
                    var b = t.split('~');
                    if (b.length < 3 || !COL[b[0]]) return;
                    var name = b[0], kind = b[1], pay = b.slice(2).join('~');
                    if (kind === 'in') {
                        var vs = pay === '' ? [] : pay.split('|');
                        var blank = vs.indexOf(BLANK_TOKEN) >= 0;
                        S.filters[name] = {
                            k: 'in',
                            v: vs.filter(function (x) { return x !== BLANK_TOKEN; })
                               .map(decodeURIComponent),
                            blank: blank
                        };
                    } else if (kind === 'rg') {
                        var pr = pay.split('|');
                        var dateish = isDateType(COL[name].type);
                        S.filters[name] = {
                            k: 'rg',
                            min: pr[0] === '' ? null : (dateish ? pr[0] : parseFloat(pr[0])),
                            max: pr[1] === '' ? null : (dateish ? pr[1] : parseFloat(pr[1])),
                            blank: pr[2] || 'out'
                        };
                    } else if (kind === 'tx') {
                        var pt = pay.split('|');
                        S.filters[name] = { k: 'tx', q: decodeURIComponent(pt[0] || ''),
                                            blank: pt[1] || 'out' };
                    }
                });
                got = true;
            } else if (k === 'g') {
                var gb = v.split(',');
                if (COL[gb[0]]) {
                    S.group = { by: gb[0], by2: COL[gb[1]] ? gb[1] : null,
                                measure: COL[gb[2]] ? gb[2] : null, agg: gb[3] || 'count' };
                    got = true;
                }
            } else if (k === 'ch') {
                var cb = v.split(',');
                if (COL[cb[0]]) { S.chart = { c: cb[0], log: cb[1] === '1' }; got = true; }
            } else if (k === 'st') { S.stat = v; got = true; }
            else if (k === 'w') {
                v.split(',').forEach(function (t) {
                    var b = t.split(':');
                    if (COL[b[0]] && isFinite(parseInt(b[1], 10))) {
                        S.widths[b[0]] = parseInt(b[1], 10);
                    }
                });
                got = true;
            }
        });
        return got;
    }

    // =====================================================================
    // Render orchestration
    // =====================================================================
    function render() {
        var host = document.getElementById('xp-pivot-host');
        if (host) host.remove();
        if (S.group) {
            elTable.hidden = true;
            renderPivot();
        } else {
            elTable.hidden = false;
            renderHead(); renderBody(); renderFoot();
        }
        renderSummary(); renderChips(); renderChart();
        $('xp-group-btn').classList.toggle('xp-on', !!S.group);
        var nf = Object.keys(S.filters).length;
        $('xp-reset-btn').disabled = !nf && !S.q && !S.sort.length && !S.group && !S.chart;
    }

    function refresh() { recompute(); render(); writeHash(); }

    // =====================================================================
    // Boot
    // =====================================================================
    function init(payload) {
        D = payload;
        N = D.n;
        D.columns.forEach(function (c) { COL[c.name] = c; NAMES.push(c.name); });

        // Distinct-value count per column, used to decide what can be a
        // grouping axis. One pass, done once.
        D.columns.forEach(function (c) {
            var seen = {}, n = 0;
            for (var i = 0; i < N; i++) {
                var k = keyOf(c, i);
                if (k == null) continue;
                var arr = Array.isArray(k) ? k : [k];
                for (var j = 0; j < arr.length; j++) {
                    if (!seen[arr[j]]) { seen[arr[j]] = 1; n++; }
                }
            }
            c.ndist = n;
        });

        S.cols = D.presets['default'].slice();
        readHash();
        if ($('xp-q')) $('xp-q').value = S.q;

        recompute();
        render();
        elScroll.addEventListener('scroll', function () {
            if (!S.group) renderBody();
        });
        var rsz = null;
        window.addEventListener('resize', function () {
            if (!S.group) renderBody();
            clearTimeout(rsz);
            rsz = setTimeout(renderChart, 150);   // viewBox is pixel-sized
        });
    }

    function wire() {
        elScroll   = $('xp-scroll');   elTable  = $('xp-table');
        elColgroup = $('xp-colgroup'); elThead  = $('xp-thead');
        elTbody    = $('xp-tbody');    elTfoot  = $('xp-tfoot');
        elChips    = $('xp-chips');    elSummary = $('xp-summary');
        elEmpty    = $('xp-empty');    elPop    = $('xp-pop');
        elTip      = $('xp-tip');      elChartbox = $('xp-chartbox');

        function anchor(id, fn) {
            $(id).addEventListener('click', function (e) {
                e.stopPropagation();
                var open = elPop.firstChild;
                closePop();
                if (!open || $(id).dataset.open !== '1') { fn($(id)); }
                // Track which button owns the open panel so a second click
                // on the same button closes rather than reopens it.
                Array.prototype.forEach.call(
                    document.querySelectorAll('.xp-toolbar .xp-btn'),
                    function (b) { b.dataset.open = '0'; });
                if (elPop.firstChild) $(id).dataset.open = '1';
            });
        }
        anchor('xp-cols-btn',   openColumns);
        anchor('xp-preset-btn', openPresets);
        anchor('xp-group-btn',  openGroup);
        anchor('xp-chart-btn',  openChart);
        anchor('xp-dl-btn',     openDownload);
        $('xp-map-btn').addEventListener('click', showOnMap);
        $('xp-reset-btn').addEventListener('click', function () {
            S.q = ''; S.filters = {}; S.sort = []; S.group = null; S.chart = null;
            $('xp-q').value = '';
            refresh();
        });

        var qt = null;
        $('xp-q').addEventListener('input', function (e) {
            clearTimeout(qt);
            var v = e.target.value.trim().toLowerCase();
            qt = setTimeout(function () { S.q = v; refresh(); }, 140);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        wire();
        fetch(XP_API + '?v=' + encodeURIComponent(XP_VERSION), { credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(init)
            .catch(function (err) {
                clear(elSummary);
                var e = el('span', 'xp-loading',
                    'Could not load the inventory table (' + err.message + '). Reload to retry.');
                elSummary.appendChild(e);
                console.error('explore: load failed', err);
            });
    });
})();
