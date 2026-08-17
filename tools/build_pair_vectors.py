"""Pack raw image-pair measurements into a browser-loadable vector bundle.

Feeds the /glaciers/pairs/ viewer: the LITERAL view of ITS_LIVE Level-2
data — every mark on screen is one measured displacement over its own
explicit time interval, with no gridding, fitting, or integration.

Registration (from autoRIFT source + Lei 2021 / Gardner 2025):
the map grid node G is the centre of the SEARCH window in image 2, while
the template chip in image 1 sits at G - D0, where D0 is the a-priori
reference displacement for that pair. D0 is an intermediate product and
is NOT published, so we use the standard approximation

    arrival   (at t2) ~ G
    departure (at t1) ~ G - v * dt

whose error is (v - v_ref) * dt — small where the reference mosaic is
good, larger on surging or strongly seasonal ice. Every displacement
therefore ENDS at its grid node and reaches backwards in time.

Sampling: the swept box holds ~2e8 valid measurements, so we take every
STRIDE-th grid node and, at each, a year-stratified sample of up to
PER_NODE pairs (stratification keeps the pair-rich 2018+ era from
swamping the sparse 1990s).

Output: data/glaciers/experiments/<name>_vectors.{json,bin.gz}
    struct-of-arrays, all little-endian:
      i, j      int16   grid node (row from north, col from west)
      vx, vy    int16   m/yr
      t_mid     float32 decimal year of the pair midpoint
      dt        uint16  separation, days
      v_error   uint16  reported error, m/yr (see caveat below)

Caveat worth carrying into the UI: v_error is a SCENE-level statistic
measured over stable ground and scaled as 1/dt, so it does NOT flag
skip/lock blunders — the most wrong values advertise the smallest
errors. It is included for display, not for filtering.

Usage: python tools/build_pair_vectors.py [--name=columbia_pairs]
                                          [--stride=2] [--per-node=60]
"""
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import zarr

ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
STRIDE = 2
PER_NODE = 60
VMAX = 20000.0


def build(name, stride, per_node):
    src = ROOT / f'{name}.zarr'
    z = zarr.open(str(src), mode='r')
    g = z.attrs['grid']
    ny, nx = g['ny'], g['nx']
    md = np.asarray(z['mid_date'])
    ddt = np.asarray(z['date_dt']).astype(np.float32)
    base = dt.date(1970, 1, 1)
    tmid = np.array([(base + dt.timedelta(days=float(x))).year +
                     ((base + dt.timedelta(days=float(x))).timetuple().tm_yday - 1) / 365.25
                     for x in md], np.float32)
    yr = tmid.astype(int)
    print(f'== {name}: grid {nx} x {ny}, {md.size} pairs, '
          f'stride {stride}, <= {per_node}/node', flush=True)

    rng = np.random.default_rng(12345)
    out_i, out_j, out_vx, out_vy, out_t, out_dt, out_e = [], [], [], [], [], [], []

    rows = list(range(0, ny, stride))
    for n_done, i in enumerate(rows, 1):
        # Read a whole grid row at once (chunks are [all_pairs, 10, 10],
        # so a row costs the same chunks as a single node would).
        vx_r = np.asarray(z['vx'][:, i, ::stride]).astype(np.int16)
        vy_r = np.asarray(z['vy'][:, i, ::stride]).astype(np.int16)
        er_r = np.asarray(z['v_error'][:, i, ::stride]).astype(np.int16)
        cols = np.arange(0, nx, stride)
        for c, j in enumerate(cols):
            vx = vx_r[:, c]; vy = vy_r[:, c]
            ok = np.nonzero((vx != -32767) & (vy != -32767) &
                            (np.abs(vx) < VMAX) & (np.abs(vy) < VMAX))[0]
            if ok.size == 0:
                continue
            if ok.size > per_node:
                # Year-stratified: even share per year present, remainder random.
                yrs_here = yr[ok]
                uy = np.unique(yrs_here)
                quota = max(1, per_node // max(1, uy.size))
                picks = []
                for y in uy:
                    idx = ok[yrs_here == y]
                    picks.append(rng.choice(idx, min(quota, idx.size), replace=False))
                sel = np.unique(np.concatenate(picks))
                if sel.size > per_node:
                    sel = rng.choice(sel, per_node, replace=False)
                elif sel.size < per_node:
                    rest = np.setdiff1d(ok, sel, assume_unique=False)
                    if rest.size:
                        sel = np.concatenate([sel, rng.choice(
                            rest, min(per_node - sel.size, rest.size), replace=False)])
                ok = sel
            out_i.append(np.full(ok.size, i, np.int16))
            out_j.append(np.full(ok.size, j, np.int16))
            out_vx.append(vx[ok]); out_vy.append(vy[ok])
            out_t.append(tmid[ok]); out_dt.append(ddt[ok])
            out_e.append(np.clip(er_r[ok, ...] if er_r.ndim == 1 else er_r[ok, c], 0, 65535))
        if n_done % 10 == 0 or n_done == len(rows):
            n = sum(a.size for a in out_i)
            print(f'   row {n_done}/{len(rows)}  records so far {n:,}', flush=True)

    I = np.concatenate(out_i); J = np.concatenate(out_j)
    VX = np.concatenate(out_vx); VY = np.concatenate(out_vy)
    T = np.concatenate(out_t); DT = np.concatenate(out_dt)
    E = np.concatenate(out_e).astype(np.uint16)
    # Sort by interval START so the viewer can binary-search a time window.
    t1 = T - DT / 2 / 365.25
    order = np.argsort(t1)
    I, J, VX, VY, T, DT, E = (a[order] for a in (I, J, VX, VY, T, DT, E))

    blocks = [('i', I.astype('<i2')), ('j', J.astype('<i2')),
              ('vx', VX.astype('<i2')), ('vy', VY.astype('<i2')),
              ('t_mid', T.astype('<f4')), ('dt', DT.astype('<u2')),
              ('v_error', E.astype('<u2'))]
    offsets, pos = {}, 0
    out_bin = ROOT / f'{name}_vectors.bin.gz'
    with gzip.open(out_bin, 'wb', compresslevel=6) as f:
        for nm, arr in blocks:
            offsets[nm] = [pos, int(arr.size), arr.dtype.str]
            pos += arr.nbytes
            f.write(arr.tobytes())

    hdr = {
        'name': name,
        'grid': g,
        'n': int(I.size),
        'stride': stride,
        'per_node': per_node,
        'offsets': offsets,
        'bin': f'{name}_vectors.bin',
        't_range': [float(t1.min()), float((T + DT / 2 / 365.25).max())],
        'dt_range': [float(DT.min()), float(DT.max())],
        'registration': 'arrival ~ grid node; departure ~ node - v*dt',
        'built': dt.date.today().isoformat(),
    }
    (ROOT / f'{name}_vectors.json').write_text(json.dumps(hdr))
    print(f'\n   {I.size:,} records -> {out_bin.stat().st_size / 1e6:.1f} MB gz')
    print(f'   time {hdr["t_range"][0]:.2f}..{hdr["t_range"][1]:.2f}, '
          f'dt {DT.min():.0f}..{DT.max():.0f} d')
    sp = np.hypot(VX.astype('f4'), VY.astype('f4'))
    print(f'   speed: median {np.median(sp):.0f}, p95 {np.percentile(sp, 95):.0f}, '
          f'max {sp.max():.0f} m/yr')


if __name__ == '__main__':
    name, stride, per_node = 'columbia_pairs', STRIDE, PER_NODE
    for a in sys.argv[1:]:
        if a.startswith('--name='):
            name = a.split('=', 1)[1]
        elif a.startswith('--stride='):
            stride = int(a.split('=', 1)[1])
        elif a.startswith('--per-node='):
            per_node = int(a.split('=', 1)[1])
    build(name, stride, per_node)
