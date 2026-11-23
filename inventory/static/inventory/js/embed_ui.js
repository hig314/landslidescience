/* embed_ui.js — the reader's controls, embed mode and the deck protocol for
 * the /inventory/ map (docs/MAP_UI.md, "Embeds and control subsets"; the
 * comparison page has the same scheme in its own template).
 *
 * THE READER'S CONTROLS. Every control a link can give or withhold has an id
 * in CTL below. `ui=<id,id…>` in the hash names the ones the reader gets;
 * no `ui=` means all of them (every link written before this), `ui=none`
 * means none. Hiding is presentation, not enforcement: the reader can edit
 * the link. What must not be seen is kept off the page by the server.
 *
 * An element belongs to a control by `data-ctl="<id>"` in home.html, or,
 * for what MapLibre and the tool modules build, by the selector in CTL.
 * `manage.py check_invariants` checks the template's ids against this list.
 *
 * Layout follows what is left: a sidebar tab with nothing in it goes, a
 * lone tab loses its tab bar, a sidebar left with only the pinned strip
 * becomes a small box over the map, and one with nothing is removed.
 *
 * EMBED MODE (/inventory/?embed=1, window.LS_EMBED). The server lets that
 * response be framed and renders it signed-out; the site header is hidden;
 * nothing is read from or written to this browser's remembered views
 * (map.js EMBED). When the frame's parent is a deck, vendor/deck-embed.js
 * carries whole-view strings (map.js 'deck' mode) each way.
 *
 * map.js calls LSEmbedUI.set / spec / on / changed directly (the `ui` row of
 * _VIEW_PARAMS, the record click) and init(ctx) once at the end of start-up.
 */
(function () {
    'use strict';

    // id, label for the picker, the sidebar tab it lives in (if any), and a
    // selector for elements this page does not write itself.
    var CTL = [
        { id: 'types',      label: 'Slow / Catastrophic boxes', strip: true },
        { id: 'traces',     label: 'Traces box', strip: true },
        { id: 'filternote', label: 'Filtered / Unfiltered note', strip: true },
        { id: 'limitview',  label: 'Limit counts to map view', tab: 'inventory' },
        { id: 'explore',    label: 'Outside-inventory switch', tab: 'inventory' },
        { id: 'classes',    label: 'Class list and counts', tab: 'inventory' },
        { id: 'features',   label: 'Landslide features filter', tab: 'inventory' },
        { id: 'include',    label: 'Include-only boxes', tab: 'inventory' },
        { id: 'sliders',    label: 'Area, volume, age and susceptibility sliders', tab: 'inventory' },
        { id: 'refmaps',    label: 'Reference maps tab (basemaps, overlays, wiper, lidar)', tab: 'refmaps' },
        { id: 'analysis',   label: 'Analysis tab', tab: 'analysis' },
        { id: 'zoom',       label: 'Zoom buttons', sel: '.maplibregl-ctrl-group:has(> .maplibregl-ctrl-zoom-in)' },
        { id: 'goto',       label: 'Go to coordinates', sel: '.ls-goto' },
        { id: 'tools',      label: 'Map tools (measure, profile, InSAR, image export)', sel: '.inv-tools-ctrl' },
        { id: 'scale',      label: 'Scale bars', sel: '.maplibregl-ctrl-scale' },
        { id: 'hint',       label: 'Zoom hint and how-to link' },
        { id: 'record',     label: 'Click a landslide to open its panel' }       // behaviour: no element
    ];
    var IDS = CTL.map(function (c) { return c.id; });
    var TABS = ['inventory', 'refmaps', 'analysis'];

    var ui = null;            // null = all controls; else the ids given
    var ctx = null, picking = false, styleEl = null;

    function on(id) { return ui == null || ui.indexOf(id) >= 0; }
    function spec() { return ui == null ? null : (ui.join(',') || 'none'); }
    function set(str) {
        if (str == null) ui = null;
        else {
            var want = String(str).split(',');
            ui = IDS.filter(function (id) { return want.indexOf(id) >= 0; });   // unknown ids (and "none") drop out
        }
        apply();
        syncPicker();
    }

    // One rule per selector: a browser that cannot parse one (:has) must
    // not lose the other with it.
    function selsFor(c) { return ['[data-ctl~="' + c.id + '"]'].concat(c.sel ? [c.sel] : []); }

    function apply() {
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = 'ls-uistyle';
            document.head.appendChild(styleEl);
        }
        var off = CTL.filter(function (c) { return !on(c.id); });
        // While an admin is choosing, what the reader will not get is dimmed
        // in place rather than removed, so the page stays whole to pick from.
        var body = picking ? ' { opacity: .3 !important; outline: 1px dashed #b3261e; }'
                           : ' { display: none !important; }';
        styleEl.textContent = off.map(function (c) {
            return selsFor(c).map(function (sel) { return sel + body; }).join('\n');
        }).join('\n');

        var b = document.body.classList, hard = !picking && ui != null;
        var tabsOn = TABS.filter(function (t) {
            return CTL.some(function (c) { return c.tab === t && on(c.id); });
        });
        var stripOn = CTL.some(function (c) { return c.strip && on(c.id); });
        TABS.forEach(function (t) { b.toggle('ui-notab-' + t, hard && tabsOn.indexOf(t) < 0); });
        b.toggle('ui-onetab', hard && tabsOn.length === 1);
        b.toggle('ui-nopanels', hard && tabsOn.length === 0);
        b.toggle('ui-strip', hard && tabsOn.length === 0 && stripOn);
        b.toggle('ui-nosidebar', hard && tabsOn.length === 0 && !stripOn);
        if (hard && ctx && tabsOn.length && tabsOn.indexOf(ctx.activeSidebarTab()) < 0) ctx.setSidebarTab(tabsOn[0]);
        if (ctx && ctx.map) { try { ctx.map.resize(); } catch (e) {} }
    }

    // ---- Analytics: that a pared-down or embedded map was opened, and where
    // it is framed. Never the view itself.
    function trackOpen() {
        if (!ctx || !window.LSTrack || (!ctx.embed && ui == null)) return;
        var framed = window.parent !== window, host = '';
        if (framed) { try { host = new URL(document.referrer).hostname; } catch (e) {} }
        window.LSTrack.event('map_embed', { embed: ctx.embed ? 1 : 0, framed: framed ? 1 : 0,
                                             host: host, ui: spec() || 'all' });
    }

    // ---- The admin's picker (the button exists only for site admins, and
    // never in embed mode: home.html).
    var panel = null;
    function copy(txt, what, btn) {
        var done = function () {
            say('Copied ' + what + '.');
            if (btn && !panel.contains(btn)) {          // a top-bar button answers on itself
                var was = btn.textContent;
                btn.textContent = 'copied'; btn.classList.add('done');
                setTimeout(function () { btn.textContent = was; btn.classList.remove('done'); }, 1500);
            }
        };
        var fall = function () {
            var ta = document.createElement('textarea');
            ta.value = txt; document.body.appendChild(ta); ta.select();
            try { document.execCommand('copy'); done(); } catch (e) { say('Could not copy.'); }
            ta.remove();
        };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, fall);
        else fall();
    }
    function say(msg) {
        var el = panel && panel.querySelector('.uip-status');
        if (el) { el.textContent = msg; clearTimeout(say.t); say.t = setTimeout(function () { el.textContent = ''; }, 4000); }
    }
    function base() { return location.origin + location.pathname; }
    function buildPanel() {
        panel = document.createElement('div');
        panel.id = 'uipanel';
        panel.className = 'float-panel';
        var h = '<div class="float-header uip-head"><strong>Reader’s controls</strong>' +
                '<button type="button" class="uip-close" title="Close: the page then shows what the reader will get">&times;</button></div>' +
                '<div class="uip-body"><p class="uip-note">Tick what a reader of the link is given. Unticked controls are dimmed while this is open and hidden when it closes. ' +
                '<a href="#" class="uip-all">all</a> / <a href="#" class="uip-none">none</a></p>';
        CTL.forEach(function (c) {
            h += '<label><input type="checkbox" value="' + c.id + '"> ' + c.label + ' <code>' + c.id + '</code></label>';
        });
        h += '<div class="uip-row"><button type="button" data-copy="link" title="This view with these controls, as an ordinary link.">Copy link</button>' +
             '<button type="button" data-copy="embed" title="The same as a page that may be framed on another site: signed-out, no site header.">Copy embed link</button></div>' +
             '<div class="uip-row"><span>Presentation:</span>' +
             '<button type="button" data-copy="slide" title="A live figure for the Presentation tool: paste it onto a slide.">Copy slide</button>' +
             '<button type="button" data-copy="step" title="This view as the next step of a figure already on a slide: select the figure there and paste.">Copy step</button></div>' +
             '<div class="uip-status"></div></div>';
        panel.innerHTML = h;
        document.body.appendChild(panel);
        panel.addEventListener('change', function (e) {
            if (!e.target.matches('input[type=checkbox]')) return;
            var sel = [];
            panel.querySelectorAll('input[type=checkbox]').forEach(function (cb) { if (cb.checked) sel.push(cb.value); });
            ui = sel.length === IDS.length ? null : sel;
            apply();
            ctx.writeHashState();
        });
        panel.addEventListener('click', function (e) {
            var t = e.target;
            if (t.matches('.uip-close')) { e.preventDefault(); togglePicker(false); return; }
            if (t.matches('.uip-all, .uip-none')) {
                e.preventDefault();
                ui = t.matches('.uip-all') ? null : [];
                apply(); syncPicker(); ctx.writeHashState();
                return;
            }
            var what = t.getAttribute && t.getAttribute('data-copy');
            if (what) copyAs(what, t);
        });
    }
    // link | embed | slide | step: the panel's buttons and the top bar's Deck pair.
    function copyAs(what, btn) {
        if (!panel) { buildPanel(); panel.classList.add('hidden'); }
        ctx.writeHashState();          // a page not yet touched has no hash to copy
        var st = ctx.getState();
        if (what === 'link') copy(location.href, 'the link', btn);
        else if (what === 'embed') copy(base() + '?embed=1#' + st, 'the embed link', btn);
        else if (what === 'step') copy(JSON.stringify(st), 'the step', btn);
        else if (what === 'slide') copy(JSON.stringify({ type: 'live', src: base() + '?embed=1#' + st, states: [st],
                                                         x: 0, y: 0, w: 1920, h: 1080 }, null, 1), 'the slide', btn);
        if (window.LSTrack) window.LSTrack.event('map_embed_copy', { what: what });
    }
    function syncPicker() {
        if (!panel) return;
        panel.querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.checked = on(cb.value); });
    }
    function togglePicker(open) {
        if (!panel) buildPanel();
        picking = open == null ? !picking : !!open;
        panel.classList.toggle('hidden', !picking);
        syncPicker();
        apply();
    }

    // ---- Deck protocol (vendor/deck-embed.js, loaded only in embed mode).
    function changed() {
        if (ctx && ctx.embed && window.DeckEmbed) window.DeckEmbed.changed();
    }
    function wireDeck() {
        if (!ctx.embed || !window.DeckEmbed) return;
        window.DeckEmbed.init({ getState: ctx.getState, setState: ctx.setState });
        // Ready when the map first comes to rest with its tiles in; a frame
        // that is not yet on screen may never draw, so do not wait for ever.
        var sent = false;
        var ready = function () { if (!sent) { sent = true; window.DeckEmbed.ready(); } };
        ctx.map.once('idle', ready);
        setTimeout(ready, 8000);
    }

    function init(c) {
        ctx = c;
        apply();
        var btn = document.getElementById('embed-ui-btn');
        if (btn) btn.addEventListener('click', function () { togglePicker(true); });
        document.querySelectorAll('[data-deck-copy]').forEach(function (b) {
            b.addEventListener('click', function () { copyAs(b.getAttribute('data-deck-copy'), b); });
        });
        wireDeck();
        setTimeout(trackOpen, 1500);
    }

    window.LSEmbedUI = { CTL: CTL, init: init, on: on, set: set, spec: spec, changed: changed };
})();
