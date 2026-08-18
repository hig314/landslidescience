"""Pack the fit's per-record verdicts + model coefficients for the pairs viewer.

Gives /glaciers/pairs/ two modes beyond the raw data:

  filtered  — the same measurements, minus the ones the robust fit rejected.
              Each record in the vector bundle is scored against its own
              cell's fitted model (along-/cross-flow residual vs the same
              tolerance the fit used) and flagged kept/rejected, so the
              viewer can show or hide blunders interactively instead of
              taking the cleaning on faith.

  fitted    — the model itself, evaluated anywhere in time. Shipping the 8
              coefficients per cell (~0.9 MB) instead of a field per month
              (~13 MB) lets the browser evaluate
                  v(t) = v0 + k*(t-tref) + a_cos*cos(2pi t) + a_sin*sin(2pi t)
              at whatever instant the slider is on, including for cells that
              only exist because of tier-2 extrapolation or tier-3 spatial
              fill — which is what makes the interpolated coverage visible.

Outputs (alongside the vector bundle):
    <name>_verdict.bin.gz   uint8 per record: 0 rejected, 1 kept, 2 no model
    <name>_model.bin.gz     float32 [ny][nx][8] coefficients, then
                            uint8 [ny][nx] provenance (0 hole .. 3 filled)
    <name>_model.json       header

Usage: python tools/build_fit_overlay_bundle.py [--name=columbia_pairs]
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
TOL_FLOOR = 25.0
TOL_FRAC = 0.35
TUKEY_C = 3.0


def main(name):
    hdr = json.loads((ROOT / f'{name}_vectors.json').read_text())
    n = hdr['n']
    g = hdr['grid']
    raw = gzip.open(ROOT / f'{name}_vectors.bin.gz', 'rb').read()

    def view(key, dtype):
        o = hdr['offsets'][key]
        return np.frombuffer(raw, dtype, count=o[1], offset=o[0])

    I = view('i', '<i2').astype(int)
    J = view('j', '<i2').astype(int)
    VX = view('vx', '<i2').astype(np.float64)
    VY = view('vy', '<i2').astype(np.float64)
    TM = view('t_mid', '<f4').astype(np.float64)
    DT = view('dt', '<u2').astype(np.float64)

    cz = np.load(ROOT / f'{name}_coef.npz', allow_pickle=True)
    coef = cz['coef']
    t_ref = float(cz['t_ref'])
    fit = np.load(ROOT / f'{name}_fit.npz', allow_pickle=True)
    source = np.nan_to_num(fit['source'], nan=0).astype(np.uint8)
    ny, nx = source.shape
    print(f'== {name}: {n:,} records, grid {nx} x {ny}, t_ref {t_ref}')
    for t in (1, 2, 3):
        print(f'   source tier {t}: {int((source == t).sum()):,} cells')
    print(f'   holes: {int((source == 0).sum()):,}')

    # Interval-mean of the model over each record's own window — the same
    # linear form the fit solved, so "kept" means exactly what the fit meant.
    t1 = TM - DT / 2 / 365.25
    t2 = TM + DT / 2 / 365.25
    tp = 2 * np.pi
    dty = np.maximum(t2 - t1, 1e-6)
    Sc = (np.sin(tp * t2) - np.sin(tp * t1)) / (tp * dty)
    Ss = (np.cos(tp * t1) - np.cos(tp * t2)) / (tp * dty)
    A = np.column_stack([np.ones_like(TM), TM - t_ref, Sc, Ss])

    C = coef[I, J]                       # [n, 8]
    ok_model = np.isfinite(C[:, 0])
    verdict = np.full(n, 2, np.uint8)     # 2 = no model at this cell
    px = np.einsum('ij,ij->i', A, C[:, :4])
    py = np.einsum('ij,ij->i', A, C[:, 4:])
    rx = VX - px
    ry = VY - py
    sp = np.hypot(px, py) + 1e-9
    ux, uy = px / sp, py / sp
    r_along = rx * ux + ry * uy
    r_cross = -rx * uy + ry * ux
    tol = np.maximum(TOL_FLOOR, TOL_FRAC * sp)
    zscore = np.hypot(r_along / (1.6 * tol), r_cross / tol)
    keep = ok_model & (zscore <= TUKEY_C)
    verdict[ok_model] = 0
    verdict[keep] = 1
    print(f'   kept {int((verdict == 1).sum()):,} '
          f'({100 * (verdict == 1).mean():.1f}%), '
          f'rejected {int((verdict == 0).sum()):,}, '
          f'no model {int((verdict == 2).sum()):,}')

    with gzip.open(ROOT / f'{name}_verdict.bin.gz', 'wb', compresslevel=6) as f:
        f.write(verdict.tobytes())

    cflat = np.nan_to_num(coef, nan=0.0).astype('<f4')
    with gzip.open(ROOT / f'{name}_model.bin.gz', 'wb', compresslevel=6) as f:
        f.write(cflat.tobytes())
        f.write(source.tobytes())
    (ROOT / f'{name}_model.json').write_text(json.dumps({
        'name': name, 'grid': g, 't_ref': t_ref,
        't_window': [2015.0, t_ref + 1.0],
        'coef_count': int(cflat.size), 'source_count': int(source.size),
        'bin': f'{name}_model.bin',
        'verdict_bin': f'{name}_verdict.bin',
        'tiers': {'1': 'fit, recent record', '2': 'fit, full record extrapolated',
                  '3': 'spatial fill', '0': 'no data'},
    }))
    sz = (ROOT / f'{name}_model.bin.gz').stat().st_size / 1e6
    vz = (ROOT / f'{name}_verdict.bin.gz').stat().st_size / 1e6
    print(f'   model {sz:.2f} MB gz, verdict {vz:.2f} MB gz')


if __name__ == '__main__':
    name = 'columbia_pairs'
    for a in sys.argv[1:]:
        if a.startswith('--name='):
            name = a.split('=', 1)[1]
    main(name)
