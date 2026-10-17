#!/usr/bin/env node
/* The grammar check CLAUDE.md asks for before a change to ls_hash.js ships
 * ("Additive only"): the parser as deployed and the new one must read every
 * stored or shared string the same way, and the new codec must round-trip
 * its own output.
 *
 *   git show main:inventory/static/inventory/js/ls_hash.js > /tmp/ls_hash_old.js
 *   node tools/hash_codec_check.js /tmp/ls_hash_old.js \
 *        inventory/static/inventory/js/ls_hash.js [strings.txt]
 *
 * strings.txt: one view string per line, with or without the leading '#'
 * (production's `SELECT default_map_view FROM landslides WHERE
 * default_map_view <> ''`, shared links from the field). The LEGACY forms
 * below, one per grammar feature, are always included. Exit 1 on the first
 * difference, with the string and the field that differs.
 *
 * The new parser may ADD fields (bearing and pitch arrived 2026-10-17); it may
 * not change or drop one the old parser produced. */
'use strict';
var fs = require('fs'), vm = require('vm');

var LEGACY = [
    'map=13.00/59.6200/-151.1900',
    'map=13/59.62/-151.19&base=esri-imagery&id=123',
    'map=4/62/-153&site=grewingk&t=12.50',
    'map=12.50/59.6200/-151.1900&base=usgs-topo&swipe=esri-imagery&sx=50&ov=dist~2024-06-01.l80r40,itslive~s.l60&li=homer_2019.hl100pr80,kbay_2023.pl60&ext=usgs/landslides&im=12.l70&ref=faults,circles&tab=refmaps&an=hist,timing&sc=0',
    'map=14.00/59.6000/-151.2000&li=homer_2019.kl100',          // retired k code
    'map=14.00/59.6000/-151.2000&ov=&li=&ext=&im=&ref=',          // present-but-empty lists
    'map=13.00/59.6200/-151.1900&ty=1,2&c=5&f=4096&sa=10,40&lv=1&pm=count&pr=59.62,-151.19,59.63,-151.18&pw=0,500&pve=0&smp=59.62,-151.19,2&ax=lw,n10&bx=0,1,0,1&ls=count&gr=3',
    'map=15.20/59.62001/-151.19002/45.5/60&t3d=1&ve=1.5&sh=p&op=60&project=tutka-tusk',   // the camera form
    'map=15.20/59.62001/-151.19002/-90/0',
    'map=16.00/59.620012/-151.190023/0/88&li=kbay_2023.pl60&align=12',
    'map=abc', 'map=13/99/-151', 'novalue', 'ov=x', '',
];

function load(path) {
    var ctx = { window: {} };
    vm.runInNewContext(fs.readFileSync(path, 'utf8'), ctx, { filename: path });
    if (!ctx.window.LSHash) throw new Error(path + ' did not define window.LSHash');
    return ctx.window.LSHash;
}
function canon(v) {   // stable JSON: sorted keys
    if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
    if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ':' + canon(v[k]); }).join(',') + '}';
    return JSON.stringify(v);
}
function fail(s, why) { console.error('DIFFERS  ' + JSON.stringify(s) + '\n         ' + why); process.exit(1); }

var argv = process.argv.slice(2);
if (argv.length < 2) { console.error('usage: hash_codec_check.js OLD.js NEW.js [strings.txt]'); process.exit(2); }
var oldC = load(argv[0]), newC = load(argv[1]);
var strings = LEGACY.slice();
if (argv[2]) strings = strings.concat(fs.readFileSync(argv[2], 'utf8').split('\n').filter(function (l) { return l.trim(); }));

strings.forEach(function (s) {
    var a = oldC.parse(s), b = newC.parse(s);
    Object.keys(a).forEach(function (k) {
        if (canon(a[k]) !== canon(b[k])) fail(s, 'field ' + k + ': old ' + canon(a[k]) + ' new ' + canon(b[k]));
    });
    // Round trip: encode is idempotent over parse, and parse(encode(parse(s)))
    // keeps every key (values may be rounded to the writer's precision).
    var e1 = newC.encode(b), e2 = newC.encode(newC.parse(e1));
    if (e1 !== e2) fail(s, 'encode not idempotent: ' + e1 + ' -> ' + e2);
    var again = newC.parse(e1);
    Object.keys(b).forEach(function (k) {
        if (k === 'extras') { Object.keys(b.extras).forEach(function (x) { if (again.extras[x] !== b.extras[x]) fail(s, 'extra ' + x + ' lost in round trip'); }); return; }
        if ((b[k] == null) !== (again[k] == null)) fail(s, 'key ' + k + ' lost in round trip: ' + e1);
    });
});
console.log('ok  ' + strings.length + ' strings: old and new parsers agree, the new codec round-trips.');
