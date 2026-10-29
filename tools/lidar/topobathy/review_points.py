#!/usr/bin/env python3
"""REVIEW POINT CLOUD: one self-contained HTML page showing a fusion step as a 3D point cloud, so a reviewer can see
at a cell WHY each observation was kept or rejected (built 2026-10-26, Hig: "a point-cloud view of this step, coloured
by source and verdict" -- the companion of joint.py fuse --dump, which writes the points.npz this reads).

    /opt/anaconda3/bin/python3 topobathy/review_points.py <points.npz> [--stage DIR] [--water-surface TIF]
        [--center LAT LON --half M] [--title T] [--out page.html] [--step K] [--final joint_v3.tif]

points.npz (joint.py fuse --dump): the window, its grid (rows from the TOP down), and per source n its 2 m cell
elevation z__n, support n__n, sigma sg__n and verdict bits why__n (0 kept; 1 high, 2 low, 4 over water, 8 superseded,
16 outside its allowed area, 32 its own water surface), the lidars' water returns zw__n/nw__n, and the pooled result
joint_z / joint_cls / joint_src / water_z.

--stage DIR adds the composite's stage rasters over the same window (final DEM, source_index, shore_label, fill_mask,
class_now, water_bodies, shore_level, fill -- whichever exist): as surfaces to toggle, and as the per-cell context the
hover tooltip prints (what the shore pass called the cell, whether the fill touched it, what the final DEM says).

The page is ONE file: every array gzip-compressed and base64-embedded, decoded in the browser
(DecompressionStream), drawn with deck.gl (pinned 9.0.38 from unpkg -- the only thing fetched).  Arrays travel as
DENSE GRIDS, not point lists: the browser derives each point's x/y from its cell index (so 4 bytes of position per
cell, not 12), and NaN runs compress to nothing.  Nothing is opened or served here (a browser launched from a tool
pops dialogs on Hig's Mac): open the page by hand.
"""
from __future__ import annotations
import argparse, base64, gzip, json, sys
from pathlib import Path
import numpy as np

DEF_STAGE = "/Volumes/Nunatak/lidar_build/composite/joint_v3/out/"
DEF_WSURF = "/Volumes/Nunatak/lidar_build/composite/joint_v3/water/water_surface.tif"
DECK_URL = "https://unpkg.com/deck.gl@9.0.38/dist.min.js"

# stage rasters: key in the page, file, dtype.  Floats are surfaces (nodata -> NaN); integer codes are the per-cell
# context.  The code tables live in the page (JS) and mirror: shore_label shore.py (0 none 1 land 2 water 3 floating
# 4 glacier), fill_mask fill.py (1 band 2 interior 3 conditioned by soundings 4 margin 5 lake 6 channel 7 tidal),
# class_now axes.CLASSES (0 void .. 9 snow), source_index = the composite's layer index (255 nothing).
STAGE = [("final", None, "f4"), ("source_index", "source_index.tif", "u1"), ("shore_label", "shore_label.tif", "u1"),
         ("fill_mask", "fill_mask.tif", "u1"), ("class_now", "class_now.tif", "u1"), ("water_bodies", "water_bodies.tif", "i4"),
         ("shore_level", "shore_level.tif", "f4"), ("fill", "fill.tif", "f4")]
WHY_BITS = {1: "rejected high", 2: "rejected low", 4: "over water", 8: "superseded by newer", 16: "outside its allowed area",
            32: "its own water surface"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_stage(path: Path, win, res, shape, dtype):
    """One stage raster over the npz window on its 2 m grid; None if absent.  Boundless, so a window that runs past
    the raster's edge reads nodata there rather than failing (water_surface.tif stops 438 m short of the others' east
    edge)."""
    import rasterio
    from rasterio.windows import from_bounds
    if not path.exists():
        return None
    with rasterio.open(path) as ds:
        if abs(ds.res[0] - res) > 1e-6:
            log(f"  {path.name}: {ds.res[0]} m grid, not {res} m -- resampled (nearest) onto the window")
        w = from_bounds(*win, transform=ds.transform)
        nd = ds.nodata
        a = ds.read(1, window=w, out_shape=shape, boundless=True, fill_value=nd if nd is not None else 0)
    if dtype == "f4":
        a = a.astype(np.float32)
        if nd is not None:
            a[a == nd] = np.nan
        return a
    # integer codes keep their nodata as a code: 0 IS "none" in class_now / fill_mask, 255 "nothing" in source_index
    return a.astype({"u1": np.uint8, "i4": np.int32}[dtype])


def blob(a: np.ndarray, dtype: str):
    """gzip + base64 of a little-endian typed array: the page decodes it with DecompressionStream('gzip')."""
    a = np.ascontiguousarray(a.astype({"f4": "<f4", "u1": "u1", "u2": "<u2", "i4": "<i4"}[dtype]))
    return dict(t=dtype, b=base64.b64encode(gzip.compress(a.tobytes(), compresslevel=6, mtime=0)).decode("ascii"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz")
    ap.add_argument("--stage", default=DEF_STAGE, help="composite out/ dir (stage rasters); '' to skip")
    ap.add_argument("--water-surface", default=DEF_WSURF, help="water_surface.tif; '' to skip")
    ap.add_argument("--final", default="joint_v3.tif", help="the final DEM's file name inside --stage")
    ap.add_argument("--center", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--half", type=float, default=250.0, help="half-width (m) of the --center crop")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--step", type=int, default=1, help="keep every K-th cell of the stage SURFACES (observations are never decimated)")
    args = ap.parse_args()
    from rasterio.warp import transform as warp

    src = Path(args.npz)
    d = np.load(src)
    win = [float(v) for v in d["win"]]; res = float(d["res"]); epsg = int(d["epsg"])
    names = [str(n) for n in d["names"]]; dates = [float(v) for v in d["dates"]]
    kinds = [str(v) for v in d["kinds"]]; classes = [str(v) for v in d["classes"]]; derived = [bool(v) for v in d["derived"]]
    H, W = d["joint_z"].shape
    if abs((win[2] - win[0]) / res - W) > 0.5 or abs((win[3] - win[1]) / res - H) > 0.5:
        sys.exit(f"window {win} at {res} m is not the arrays' {H}x{W}")

    # CROP to a square about a lat/lon, snapped to whole cells and clipped to the window
    rs, cs = slice(0, H), slice(0, W)
    if args.center:
        (cx,), (cy,) = warp("EPSG:4326", f"EPSG:{epsg}", [args.center[1]], [args.center[0]])
        j0 = int(np.floor((cx - args.half - win[0]) / res)); j1 = int(np.ceil((cx + args.half - win[0]) / res))
        i0 = int(np.floor((win[3] - (cy + args.half)) / res)); i1 = int(np.ceil((win[3] - (cy - args.half)) / res))
        j0, j1, i0, i1 = max(j0, 0), min(j1, W), max(i0, 0), min(i1, H)
        if j1 <= j0 or i1 <= i0:
            sys.exit(f"--center {args.center} (E {cx:.0f} N {cy:.0f}) is outside the window {win}")
        rs, cs = slice(i0, i1), slice(j0, j1)
        win = [win[0] + j0 * res, win[3] - i1 * res, win[0] + j1 * res, win[3] - i0 * res]
        H, W = i1 - i0, j1 - j0
        log(f"  crop: {W}x{H} cells, {win}")
    g = lambda k: d[k][rs, cs]

    A = {}                                          # page arrays: key -> blob
    counts = {}                                     # per source: observations, kept, and each verdict bit
    for n in names:
        z = g(f"z__{n}").astype(np.float32); why = g(f"why__{n}").astype(np.uint8); fin = np.isfinite(z)
        counts[n] = dict(obs=int(fin.sum()), kept=int((fin & (why == 0)).sum()),
                         **{lbl: int((fin & ((why & b) > 0)).sum()) for b, lbl in WHY_BITS.items()})
        A[f"z__{n}"] = blob(z, "f4"); A[f"n__{n}"] = blob(g(f"n__{n}"), "f4")
        A[f"sg__{n}"] = blob(g(f"sg__{n}"), "f4"); A[f"why__{n}"] = blob(why, "u1")
        if f"zw__{n}" in d.files:
            zw = g(f"zw__{n}").astype(np.float32)
            counts[n]["water_returns"] = int(np.isfinite(zw).sum())
            A[f"zw__{n}"] = blob(zw, "f4"); A[f"nw__{n}"] = blob(g(f"nw__{n}"), "f4")
    jz = g("joint_z").astype(np.float32)
    A["joint_z"] = blob(jz, "f4"); A["joint_cls"] = blob(g("joint_cls"), "u1")
    A["joint_src"] = blob(g("joint_src"), "u2"); A["water_z"] = blob(g("water_z"), "f4")

    # STAGE rasters (optional, each skipped if absent)
    stage, zall = [], [jz]
    keep_lattice = np.zeros((H, W), bool); keep_lattice[::max(args.step, 1), ::max(args.step, 1)] = True
    files = []
    if args.stage:
        sd = Path(args.stage)
        if not sd.is_dir():
            log(f"  --stage {sd}: not a directory, skipped")
        else:
            files = [(k, sd / (args.final if f is None else f), t) for k, f, t in STAGE]
    if args.water_surface:
        files.append(("wsurf", Path(args.water_surface), "f4"))
    for k, p, t in files:
        a = read_stage(p, win, res, (H, W), t)
        if a is None:
            log(f"  {p.name}: absent, skipped"); continue
        if t == "f4":
            a = np.where(keep_lattice, a, np.nan).astype(np.float32); zall.append(a)
        A[k] = blob(a, t); stage.append(k)
    log(f"  stage: {', '.join(stage) or 'none'}")

    # the display frame: x/y about the window centre, z about the median pooled elevation (so the exaggeration
    # scales about the middle of the scene, not about sea level 300 m below it)
    xc, yc = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
    zs = np.concatenate([a[np.isfinite(a)].ravel() for a in zall + [g(f"z__{n}") for n in names]])
    zref = float(np.median(zs)) if zs.size else 0.0
    # lat/lon for the tooltip: the centre exactly, elsewhere a local linear map (the Jacobian from 100 m steps).
    # Good to a few cm across a few km -- display only.
    lon, lat = warp(f"EPSG:{epsg}", "EPSG:4326", [xc, xc + 100, xc], [yc, yc, yc + 100])
    jac = [(lon[1] - lon[0]) / 100, (lon[2] - lon[0]) / 100, (lat[1] - lat[0]) / 100, (lat[2] - lat[0]) / 100]

    title = args.title or "Fusion point review"
    meta = dict(title=title, npz=str(src), epsg=epsg, res=res, H=H, W=W, win=win, xc=xc, yc=yc, zref=zref,
                lon0=lon[0], lat0=lat[0], jac=jac, names=names, dates=dates, kinds=kinds, classes=classes,
                derived=derived, stage=stage, counts=counts, step=args.step,
                water_files={n: f"zw__{n}" in d.files for n in names})
    out = Path(args.out) if args.out else src.parent / "review.html"
    page = (PAGE.replace("__TITLE__", _esc(title if len(title.split()) <= 4 else "Fusion point review"))
                .replace("__DECK__", DECK_URL)
                .replace("__META__", json.dumps(meta).replace("</", "<\\/"))
                .replace("__DATA__", json.dumps(A)))
    out.write_text(page, encoding="utf-8")
    log(f"  wrote {out}  {out.stat().st_size / 1e6:.1f} MB  ({W}x{H} cells, {len(names)} sources)")
    for n in names:
        c = counts[n]
        log(f"    {n:14s} obs {c['obs']:8d}  kept {c['kept']:8d}  " + "  ".join(f"{k} {v}" for k, v in c.items() if k not in ("obs", "kept") and v))


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------------------------------------------------------
# THE PAGE.  Plain JS, no build step.  The data arrive as dense grids (cell index = row * W + col, rows from the top);
# each "set" (a source's observations, its water returns, the pooled surface, a stage surface) becomes one deck.gl
# PointCloudLayer of the cells it has a value at, with a deterministic sub-cell jitter (<= 0.4 m, different per set)
# so co-located sources stand side by side instead of hiding one another.
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f6f4;--panel:#ffffff;--ink:#1d1d1b;--mute:#6b6b66;--line:#d9d9d4;--accent:#2f6fb3;--scene:#eceae4;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121212;--panel:#1c1c1c;--ink:#e8e8e3;--mute:#9a9a94;--line:#333;--accent:#7fb2ea;--scene:#0b0b0d;}}
:root[data-theme="dark"]{--bg:#121212;--panel:#1c1c1c;--ink:#e8e8e3;--mute:#9a9a94;--line:#333;--accent:#7fb2ea;--scene:#0b0b0d;}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
#app{display:grid;grid-template-columns:300px 1fr;grid-template-rows:auto 1fr;height:100vh}
header{grid-column:1/3;padding:8px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:15px;margin:0 0 2px}
header .sub{color:var(--mute);font-size:12px;overflow-wrap:anywhere}
aside{overflow:auto;padding:10px 16px;border-right:1px solid var(--line);background:var(--panel)}
main{position:relative;background:var(--scene);min-height:300px}
#view{position:absolute;inset:0}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:14px 0 4px}
label{display:flex;align-items:center;gap:6px;margin:2px 0;cursor:pointer}
label.dim{opacity:.45}
select,button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:3px 6px}
input[type=range]{width:100%}
.row{display:flex;justify-content:space-between;color:var(--mute);font-size:12px}
.sw{display:inline-block;width:11px;height:11px;border-radius:2px;flex:none;border:1px solid rgba(128,128,128,.4)}
.lg{display:flex;align-items:center;gap:6px;font-size:12px;margin:1px 0}
.lg .n{margin-left:auto;color:var(--mute);font-variant-numeric:tabular-nums}
.ramp{height:10px;border-radius:2px;margin:4px 0 2px}
.cnt{font-size:11px;color:var(--mute);font-variant-numeric:tabular-nums}
table{border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;width:100%}
td,th{padding:1px 4px;border-bottom:1px solid var(--line);text-align:right}
td:first-child,th:first-child{text-align:left}
#msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:24px;text-align:center;color:var(--mute)}
.help{font-size:11px;color:var(--mute);margin-top:12px}
@media (max-width:760px){#app{grid-template-columns:1fr;grid-template-rows:auto 60vh auto;height:auto}
  header{grid-column:1}main{grid-row:2}aside{grid-row:3;border-right:0;border-top:1px solid var(--line)}}
</style></head>
<body><div id="app">
<header><h1 id="h1"></h1><div class="sub" id="hsub"></div><div class="sub" id="hcounts"></div></header>
<aside>
  <h2>Colour by</h2>
  <select id="mode">
    <option value="set">source</option><option value="verdict">verdict</option><option value="class">class</option>
    <option value="date">date</option><option value="sigma">sigma</option><option value="elev">elevation</option>
    <option value="shore">shore label</option><option value="fill">fill mask</option><option value="sidx">composite layer (source_index)</option>
  </select>
  <div id="legend" style="margin-top:6px"></div>
  <h2>Sets</h2><div id="sets"></div>
  <h2>Verdicts (source observations)</h2><div id="verdicts"></div>
  <h2>Elevation range</h2>
  <div class="row"><span>min <b id="zlo_v"></b></span><span>max <b id="zhi_v"></b></span></div>
  <input type="range" id="zlo"><input type="range" id="zhi">
  <h2>Display</h2>
  <div class="row"><span>vertical exaggeration</span><b id="ex_v">1×</b></div>
  <input type="range" id="ex" min="1" max="5" step="0.5" value="1">
  <div class="row"><span>point size (px)</span><b id="ps_v">2</b></div>
  <input type="range" id="ps" min="1" max="10" step="0.5" value="2">
  <div style="margin-top:6px;display:flex;gap:6px"><button id="reset">Reset view</button><button id="top">Top down</button></div>
  <details style="margin-top:12px"><summary>Counts per source and verdict</summary><div id="ctable"></div></details>
  <div class="help">Drag to orbit · Shift-drag or right-drag to pan · scroll to zoom · hover a point for its cell.
  A cell may carry several verdict bits; filter and colour use the first of: outside, superseded, own water, over water,
  high, low. Points are jittered ≤ 0.4 m within their cell so co-located sources show.</div>
</aside>
<main><div id="view"></div><div id="msg">Loading…</div></main>
</div>
<script type="application/json" id="meta">__META__</script>
<script type="application/json" id="data">__DATA__</script>
<script src="__DECK__"></script>
<script>
"use strict";
const META = JSON.parse(document.getElementById('meta').textContent);
const $ = id => document.getElementById(id);
const msg = t => { const m = $('msg'); if (t === null) { m.style.display = 'none'; } else { m.style.display = 'flex'; m.textContent = t; } };
const fmt = (v, d = 2) => (v === null || v === undefined || !isFinite(v)) ? '–' : Number(v).toFixed(d);
const N = META.W * META.H;
const dark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark'
  : window.matchMedia('(prefers-color-scheme: dark)').matches;

// ---- code tables (mirror joint.py CLS / axes.CLASSES, shore.py, fill.py; see review_points.py STAGE)
const hex = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const SRC_PAL = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#9a6324', '#469990', '#dcbeff', '#800000'];
const VERD = [  // order = priority when a cell carries several bits
  {bit: 0, label: 'kept', c: '#2ca02c'}, {bit: 16, label: 'outside its allowed area', c: '#8c564b'},
  {bit: 8, label: 'superseded by newer', c: '#9a9a9a'}, {bit: 32, label: 'its own water surface', c: '#17becf'},
  {bit: 4, label: 'over water', c: '#1f5fbf'}, {bit: 1, label: 'rejected high', c: '#d62728'}, {bit: 2, label: 'rejected low', c: '#ff9f1a'}];
const VLUT = new Uint8Array(64);   // why byte -> index into VERD
for (let w = 1; w < 64; w++) VLUT[w] = VERD.findIndex((v, i) => i > 0 && (w & v.bit));
const CLASS = {0: ['void', '#7f7f7f'], 1: ['ground', '#a6773f'], 2: ['water', '#1f5fbf'], 3: ['bed', '#2a9d8f'], 4: ['ice', '#9ecae1'],
  5: ['subglacial', '#6a51a3'], 6: ['canopy', '#31a354'], 7: ['built', '#636363'], 8: ['modelled', '#e377c2'], 9: ['snow', '#d9d9d9']};
const CLSNUM = {void: 0, ground: 1, water: 2, bed: 3, ice: 4};
const SHORE = {0: ['none', '#7f7f7f'], 1: ['land', '#a6773f'], 2: ['water', '#1f5fbf'], 3: ['floating', '#9e9ac8'], 4: ['glacier', '#9ecae1'], 255: ['nodata', '#444444']};
const FILLM = {0: ['not filled', '#7f7f7f'], 1: ['band', '#ff7f0e'], 2: ['interior', '#d62728'], 3: ['conditioned by soundings', '#2ca02c'],
  4: ['margin', '#9467bd'], 5: ['lake', '#1f77b4'], 6: ['channel', '#17becf'], 7: ['tidal', '#bcbd22']};
const NA = [128, 128, 128];
// a viridis-like ramp for the continuous modes
const RAMP = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'].map(hex);
const rampCss = 'linear-gradient(90deg,' + RAMP.map(c => `rgb(${c})`).join(',') + ')';
function ramp(t) {
  t = Math.min(1, Math.max(0, t)) * (RAMP.length - 1); const i = Math.min(RAMP.length - 2, Math.floor(t)), f = t - i;
  const a = RAMP[i], b = RAMP[i + 1]; return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

// ---- decode the embedded arrays (gzip + base64, little-endian)
async function decode(e) {
  const bin = atob(e.b), u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const buf = await new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  return new ({f4: Float32Array, u1: Uint8Array, u2: Uint16Array, i4: Int32Array}[e.t])(buf);
}

let A = {}, SETS = [], deckgl = null, VSTATE = null;
const verdOn = VERD.map(() => true);

function defineSets() {
  const M = META, theme = dark();
  M.names.forEach((n, k) => {
    const lbl = n + (M.derived[k] ? ' (derived)' : '');
    SETS.push({id: 'o' + k, label: lbl, kind: 'obs', k, z: A['z__' + n], color: hex(SRC_PAL[k % SRC_PAL.length]), on: true});
  });
  M.names.forEach((n, k) => {
    if (!A['zw__' + n]) return;
    const c = hex(SRC_PAL[k % SRC_PAL.length]).map((v, i) => Math.round(v * 0.45 + [0, 170, 255][i] * 0.55));
    SETS.push({id: 'w' + k, label: n + ' water returns', kind: 'wat', k, z: A['zw__' + n], color: c, on: true});
  });
  SETS.push({id: 'joint', label: 'pooled joint_z', kind: 'joint', z: A.joint_z, color: theme ? [245, 245, 245] : [25, 25, 25], on: false});
  SETS.push({id: 'jw', label: 'water_z (pooled water returns)', kind: 'jw', z: A.water_z, color: [79, 195, 247], on: false});
  const S = {final: ['final DEM', [170, 170, 170]], wsurf: ['water surface', [30, 144, 255]], shore_level: ['shore level', [0, 206, 209]], fill: ['fill', [255, 105, 180]]};
  for (const k of ['final', 'wsurf', 'shore_level', 'fill']) if (A[k]) SETS.push({id: k, label: S[k][0], kind: 'surf', z: A[k], color: S[k][1], on: false});
  SETS.forEach((s, i) => { s.si = i; s.cells = new Uint32Array(0); s.n = 0; });
}

// a deterministic hash -> jitter in [-0.4, 0.4] m, different per set so co-located points separate
function hj(c, s) { let x = (Math.imul(c, 2654435761) ^ Math.imul(s + 1, 40503)) >>> 0; x ^= x >>> 16; x = Math.imul(x, 0x45d9f3b) >>> 0; x ^= x >>> 16; return (x / 4294967296 - 0.5) * 0.8; }

// ---- colour modes: each returns, for a set and a cell, a category key (cat) or a value (ramp)
function obsVerdict(s, c) { return VLUT[A['why__' + META.names[s.k]][c] & 63]; }
function stageCode(key, c) { return A[key] ? A[key][c] : null; }
const MODES = {
  set: {cat: true, cats: () => SETS.map(s => [s.label, s.color]), key: (s, c) => s.si},
  verdict: {cat: true, cats: () => VERD.map(v => [v.label, hex(v.c)]).concat([['not a source observation', NA]]),
            key: (s, c) => s.kind === 'obs' ? obsVerdict(s, c) : VERD.length},
  class: {cat: true, cats: () => Object.values(CLASS).map(v => [v[0], hex(v[1])]).concat([['unknown', NA]]),
          key: (s, c) => { let v;
            if (s.kind === 'obs') v = CLSNUM[META.classes[s.k]]; else if (s.kind === 'wat' || s.kind === 'jw' || s.id === 'wsurf') v = 2;
            else if (s.kind === 'joint') v = A.joint_cls[c]; else if (s.id === 'final' && A.class_now) v = A.class_now[c];
            return (v === undefined || v === null || v > 9) ? 10 : v; }},
  shore: {cat: true, codes: SHORE, key: (s, c) => stageCode('shore_label', c)},
  fill: {cat: true, codes: FILLM, key: (s, c) => stageCode('fill_mask', c)},
  sidx: {cat: true, codes: null, key: (s, c) => stageCode('source_index', c)},
  date: {cat: false, unit: '', val: (s, c) => (s.kind === 'obs' || s.kind === 'wat') ? META.dates[s.k] : NaN, dec: 2},
  sigma: {cat: false, unit: ' m', log: true, val: (s, c) => s.kind === 'obs' ? A['sg__' + META.names[s.k]][c] : NaN, dec: 3},
  elev: {cat: false, unit: ' m', val: (s, c) => s.z[c], dec: 1},
};
// code-table modes (shore, fill, source_index) have a sparse code space: categories are the codes present
function codeCats(mode) {
  const tab = MODES[mode].codes, out = [], index = {};
  const present = new Set(); const key = mode === 'shore' ? 'shore_label' : mode === 'fill' ? 'fill_mask' : 'source_index';
  if (A[key]) for (let c = 0; c < N; c++) present.add(A[key][c]);
  [...present].sort((a, b) => a - b).forEach(v => {
    index[v] = out.length;
    if (tab && tab[v]) out.push([v + ' ' + tab[v][0], hex(tab[v][1])]);
    else if (v === 255) out.push(['255 nothing', [68, 68, 68]]);
    else out.push(['layer ' + v, hex(SRC_PAL[v % SRC_PAL.length])]);
  });
  out.push([A[key] ? 'n/a' : `(no ${key}.tif in --stage)`, NA]);
  return {cats: out, index};
}

// ---- build every visible set's point arrays (positions + colours) under the current filters and colour mode
function build() {
  const mode = $('mode').value, M = MODES[mode], res = META.res, W = META.W;
  const zlo = +$('zlo').value + META.zref, zhi = +$('zhi').value + META.zref;
  const x0 = META.win[0] - META.xc, y1 = META.win[3] - META.yc;
  let cats = null, cidx = null;
  if (M.cat) { if (M.codes !== undefined) { const r = codeCats(mode); cats = r.cats; cidx = r.index; } else cats = M.cats(); }
  const catN = cats ? new Array(cats.length).fill(0) : null;
  // pass 1: the cells of each set that pass the filters (and, for a ramp, the value range)
  let vmin = Infinity, vmax = -Infinity, nval = 0, nnone = 0;
  const tmp = new Uint32Array(N);
  for (const s of SETS) {
    s.n = 0;
    if (!s.on) { s.cells = new Uint32Array(0); continue; }
    const z = s.z, why = s.kind === 'obs' ? A['why__' + META.names[s.k]] : null;
    let n = 0;
    for (let c = 0; c < N; c++) {
      const v = z[c]; if (!(v >= zlo && v <= zhi)) continue;      // NaN fails both
      if (why && !verdOn[VLUT[why[c] & 63]]) continue;
      tmp[n++] = c;
    }
    s.cells = tmp.slice(0, n); s.n = n;
    if (!M.cat) for (let i = 0; i < n; i++) { let v = M.val(s, s.cells[i]); if (M.log) v = v > 0 ? Math.log10(v) : NaN;
      if (isFinite(v)) { if (v < vmin) vmin = v; if (v > vmax) vmax = v; nval++; } else nnone++; }
  }
  if (mode === 'elev') { vmin = Math.max(vmin, zlo); vmax = Math.min(vmax, zhi); }
  const span = vmax > vmin ? vmax - vmin : 1;
  // pass 2: positions and colours
  const layers = [];
  for (const s of SETS) {
    if (!s.n) continue;
    const pos = new Float32Array(s.n * 3), col = new Uint8Array(s.n * 4);
    for (let i = 0; i < s.n; i++) {
      const c = s.cells[i], r = (c / W) | 0, q = c - r * W;
      pos[3 * i] = x0 + (q + 0.5) * res + hj(c, 2 * s.si); pos[3 * i + 1] = y1 - (r + 0.5) * res + hj(c, 2 * s.si + 1);
      pos[3 * i + 2] = s.z[c] - META.zref;
      let rgb;
      if (M.cat) { let k = M.key(s, c); if (cidx) k = (k === null || cidx[k] === undefined) ? cats.length - 1 : cidx[k];
        rgb = cats[k][1]; catN[k]++; }
      else { let v = M.val(s, c); if (M.log) v = v > 0 ? Math.log10(v) : NaN; rgb = isFinite(v) ? ramp((v - vmin) / span) : NA; }
      col[4 * i] = rgb[0]; col[4 * i + 1] = rgb[1]; col[4 * i + 2] = rgb[2]; col[4 * i + 3] = 255;
    }
    layers.push(new deck.PointCloudLayer({
      id: s.id, data: {length: s.n, attributes: {getPosition: {value: pos, size: 3}, getColor: {value: col, size: 4}}},
      coordinateSystem: deck.COORDINATE_SYSTEM.CARTESIAN, pointSize: +$('ps').value, sizeUnits: 'pixels', material: false,
      pickable: true, modelMatrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, +$('ex').value, 0, 0, 0, 0, 1]}));
  }
  deckgl.setProps({layers});
  // legend
  const L = $('legend'); L.innerHTML = '';
  if (M.cat) cats.forEach((ct, i) => { if (!catN[i] && mode !== 'set' && mode !== 'verdict') return;
    L.insertAdjacentHTML('beforeend', `<div class="lg"><span class="sw" style="background:rgb(${ct[1]})"></span>${ct[0]}<span class="n">${catN[i].toLocaleString()}</span></div>`); });
  else {
    const f = v => M.log ? fmt(Math.pow(10, v), M.dec) : fmt(v, M.dec);
    L.innerHTML = nval ? `<div class="ramp" style="background:${rampCss}"></div><div class="row"><span>${f(vmin)}${M.unit}</span><span>${M.log ? 'log scale' : ''}</span><span>${f(vmax)}${M.unit}</span></div>`
      + `<div class="cnt">${nval.toLocaleString()} points with a value · ${nnone.toLocaleString()} without (grey)</div>` : '<div class="cnt">no points carry this value</div>';
  }
  SETS.forEach(s => { const e = $('n_' + s.id); if (e) e.textContent = s.on ? s.n.toLocaleString() : ''; });
}

// ---- the hover tooltip: the point, then everything known at its cell
function tooltip(info) {
  if (!info || info.index < 0 || !info.layer) return null;
  const s = SETS.find(t => t.id === info.layer.id); if (!s) return null;
  const c = s.cells[info.index]; if (c === undefined) return null;
  const M = META, r = (c / M.W) | 0, q = c - r * M.W;
  const dx = M.win[0] + (q + 0.5) * M.res - M.xc, dy = M.win[3] - (r + 0.5) * M.res - M.yc;
  const lon = M.lon0 + M.jac[0] * dx + M.jac[1] * dy, lat = M.lat0 + M.jac[2] * dx + M.jac[3] * dy;
  const why2txt = w => w === 0 ? 'kept' : VERD.filter(v => v.bit && (w & v.bit)).map(v => v.label).join(', ');
  let h = `<b>${s.label}</b> z ${fmt(s.z[c], 2)} m`;
  if (s.kind === 'obs') { const n = M.names[s.k];
    h += `<br>n ${fmt(A['n__' + n][c], 1)} · σ ${fmt(A['sg__' + n][c], 3)} m · ${M.dates[s.k].toFixed(2)} · ${M.kinds[s.k]}/${M.classes[s.k]}<br>verdict: <b>${why2txt(A['why__' + n][c])}</b>`; }
  else if (s.kind === 'wat') h += `<br>water returns n ${fmt(A['nw__' + M.names[s.k]][c], 0)} · ${M.dates[s.k].toFixed(2)}`;
  h += `<br><span style="opacity:.75">E ${(M.xc + dx).toFixed(1)} N ${(M.yc + dy).toFixed(1)} (EPSG:${M.epsg}) · ${lat.toFixed(6)}, ${lon.toFixed(6)} · row ${r} col ${q}</span>`;
  h += '<hr style="border:0;border-top:1px solid #666;margin:4px 0">';
  M.names.forEach((n, k) => { const z = A['z__' + n][c]; const zw = A['zw__' + n] ? A['zw__' + n][c] : NaN;
    if (isFinite(z) || isFinite(zw)) h += `${n}: ${isFinite(z) ? fmt(z, 2) + ' (' + why2txt(A['why__' + n][c]) + ')' : '–'}${isFinite(zw) ? ' · water ' + fmt(zw, 2) : ''}<br>`; });
  const jc = A.joint_cls[c], js = A.joint_src[c];
  h += `joint ${fmt(A.joint_z[c], 2)} (${CLASS[jc] ? CLASS[jc][0] : jc}${js ? '; ' + M.names.filter((n, k) => js & (1 << k)).join('+') : ''})`;
  if (isFinite(A.water_z[c])) h += ` · water_z ${fmt(A.water_z[c], 2)}`;
  const st = [];
  if (A.final) st.push(`final ${fmt(A.final[c], 2)}`);
  if (A.class_now) st.push(`class ${CLASS[A.class_now[c]] ? CLASS[A.class_now[c]][0] : A.class_now[c]}`);
  if (A.shore_label) st.push(`shore ${SHORE[A.shore_label[c]] ? SHORE[A.shore_label[c]][0] : A.shore_label[c]}`);
  if (A.fill_mask) st.push(`fill ${FILLM[A.fill_mask[c]] ? FILLM[A.fill_mask[c]][0] : A.fill_mask[c]}`);
  if (A.source_index) st.push(`layer ${A.source_index[c] === 255 ? 'none' : A.source_index[c]}`);
  if (A.water_bodies && A.water_bodies[c]) st.push(`water body ${A.water_bodies[c]}`);
  if (A.wsurf && isFinite(A.wsurf[c])) st.push(`water surface ${fmt(A.wsurf[c], 2)}`);
  if (A.shore_level && isFinite(A.shore_level[c])) st.push(`shore level ${fmt(A.shore_level[c], 2)}`);
  if (A.fill && isFinite(A.fill[c])) st.push(`fill z ${fmt(A.fill[c], 2)}`);
  if (st.length) h += '<br>stage: ' + st.join(' · ');
  return {html: h, style: {background: dark() ? '#222' : '#fff', color: dark() ? '#eee' : '#111', font: '12px/1.35 -apple-system,sans-serif',
    padding: '6px 8px', border: '1px solid #888', borderRadius: '4px', maxWidth: '380px'}};
}

function header() {
  const M = META;
  $('h1').textContent = M.title;
  const w = M.win;
  $('hsub').textContent = `${M.npz} · EPSG:${M.epsg} E ${w[0]}–${w[2]} N ${w[1]}–${w[3]} (${(w[2] - w[0]).toFixed(0)} × ${(w[3] - w[1]).toFixed(0)} m, ${M.W}×${M.H} cells at ${M.res} m)`
    + ` · centre ${M.lat0.toFixed(5)}, ${M.lon0.toFixed(5)} · z about ${M.zref.toFixed(1)} m` + (M.stage.length ? ` · stage: ${M.stage.join(', ')}` : ' · no stage rasters')
    + (M.step > 1 ? ` · surfaces every ${M.step}th cell` : '');
  $('hcounts').textContent = M.names.map(n => { const c = M.counts[n]; const rej = c.obs - c.kept;
    return `${n}: ${c.obs.toLocaleString()} obs, ${c.kept.toLocaleString()} kept, ${rej.toLocaleString()} not`; }).join(' · ');
  const cols = ['obs', 'kept'].concat(VERD.slice(1).map(v => v.label));
  let t = '<table><tr><th></th>' + cols.map(k => `<th title="${k}">${k.split(' ')[k.startsWith('rejected') ? 1 : 0]}</th>`).join('') + '</tr>';
  M.names.forEach(n => { t += `<tr><td>${n}</td>` + cols.map(k => `<td>${(M.counts[n][k] || 0).toLocaleString()}</td>`).join('') + '</tr>'; });
  $('ctable').innerHTML = t + '</table><div class="cnt">verdict bits overlap: a cell can count in more than one column</div>';
}

function controls() {
  const sets = $('sets');
  SETS.forEach(s => {
    const l = document.createElement('label');
    l.innerHTML = `<input type="checkbox" ${s.on ? 'checked' : ''}><span class="sw" style="background:rgb(${s.color})"></span>${s.label}<span class="n cnt" id="n_${s.id}" style="margin-left:auto"></span>`;
    l.querySelector('input').onchange = e => { s.on = e.target.checked; build(); };
    sets.appendChild(l);
  });
  const vd = $('verdicts');
  VERD.forEach((v, i) => {
    const l = document.createElement('label');
    l.innerHTML = `<input type="checkbox" checked><span class="sw" style="background:${v.c}"></span>${v.label}`;
    l.querySelector('input').onchange = e => { verdOn[i] = e.target.checked; build(); };
    vd.appendChild(l);
  });
  // z range across every set (absolute metres shown; sliders work about zref)
  let lo = Infinity, hi = -Infinity;
  for (const s of SETS) for (let c = 0; c < N; c++) { const v = s.z[c]; if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) { lo = META.zref; hi = META.zref + 1; }
  lo = Math.floor(lo - META.zref); hi = Math.ceil(hi - META.zref);
  for (const id of ['zlo', 'zhi']) { const e = $(id); e.min = lo; e.max = hi; e.step = 0.1; e.value = id === 'zlo' ? lo : hi; }
  const zlab = () => { $('zlo_v').textContent = (+$('zlo').value + META.zref).toFixed(1); $('zhi_v').textContent = (+$('zhi').value + META.zref).toFixed(1); };
  zlab();
  let tm = null; const later = () => { clearTimeout(tm); tm = setTimeout(build, 120); };
  $('zlo').oninput = () => { if (+$('zlo').value > +$('zhi').value) $('zlo').value = $('zhi').value; zlab(); later(); };
  $('zhi').oninput = () => { if (+$('zhi').value < +$('zlo').value) $('zhi').value = $('zlo').value; zlab(); later(); };
  $('ex').oninput = () => { $('ex_v').textContent = $('ex').value + '×'; later(); };
  $('ps').oninput = () => { $('ps_v').textContent = $('ps').value; later(); };
  $('mode').onchange = build;
  $('reset').onclick = () => deckgl.setProps({initialViewState: {...VSTATE, transitionDuration: 300}});
  $('top').onclick = () => deckgl.setProps({initialViewState: {...VSTATE, rotationX: 90, rotationOrbit: 0, transitionDuration: 300}});
}

function hasWebGL2() { try { return !!document.createElement('canvas').getContext('webgl2'); } catch (e) { return false; } }

(async function main() {
  if (typeof deck === 'undefined') return msg('deck.gl did not load (offline, or unpkg.com blocked): this page needs __DECK__.');
  if (!hasWebGL2()) return msg('This browser has no WebGL2, which the 3D view needs. Try a current Chrome, Firefox or Safari, or enable hardware acceleration.');
  if (typeof DecompressionStream === 'undefined') return msg('This browser cannot decompress the embedded data (no DecompressionStream). Try a current Chrome, Firefox or Safari.');
  try {
    const raw = JSON.parse(document.getElementById('data').textContent);
    const keys = Object.keys(raw);
    const vals = await Promise.all(keys.map(k => decode(raw[k])));
    keys.forEach((k, i) => { A[k] = vals[i]; });
  } catch (e) { return msg('Could not decode the embedded data: ' + e); }
  defineSets(); header(); controls();
  const el = $('view'), w = Math.max(el.clientWidth, 300), h = Math.max(el.clientHeight, 300);
  const ext = Math.max(META.win[2] - META.win[0], META.win[3] - META.win[1]);
  VSTATE = {target: [0, 0, 0], rotationX: 40, rotationOrbit: -25, zoom: Math.log2(0.85 * Math.min(w, h) / ext), minZoom: -12, maxZoom: 12};
  try {
    deckgl = new deck.Deck({parent: el, views: new deck.OrbitView({orbitAxis: 'Z', fovy: 40}), initialViewState: VSTATE,
      controller: true, getTooltip: tooltip, onError: e => msg('3D view error: ' + e)});
  } catch (e) { return msg('Could not start the 3D view (WebGL): ' + e); }
  msg(null); build();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const j = SETS.find(s => s.id === 'joint'); if (j) j.color = dark() ? [245, 245, 245] : [25, 25, 25]; build(); });
})();
</script></body></html>
"""

if __name__ == "__main__":
    main()
