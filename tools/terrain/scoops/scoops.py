#!/opt/anaconda3/bin/python3
"""Python face of scoops_core.c: a Scoops3D-style search in this repo's stack.

    grid = Grid(dem_south_up, dx)
    single(grid, xc, yc, zc, R, c, phi, gamma)     # one trial surface
    search(grid, ..., phis=[...], edges=[...])     # a box search, many processes

Compiles the C on first use (`cc`, no other dependency). Each worker fills its
own shared arrays over every Nth lattice node, and the parent merges them.
"""
import ctypes as C
import multiprocessing as mp
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC, LIB = HERE / 'scoops_core.c', HERE / '_scoops_core.so'


def _load():
    if not LIB.exists() or LIB.stat().st_mtime < SRC.stat().st_mtime:
        subprocess.run(['cc', '-O3', '-mcpu=native', '-shared', '-fPIC', '-o', str(LIB), str(SRC), '-lm'], check=True)
    return C.CDLL(str(LIB))


class Single(C.Structure):
    _fields_ = [(n, C.c_double) for n in ('v', 'ah', 'area', 'weight', 'f_bishop', 'f_ordinary', 'delta_deg', 'sigma', 'tau')] + \
               [('ncols', C.c_int)]


class Stats(C.Structure):
    _fields_ = [(n, C.c_longlong) for n in ('surfaces', 'columns', 'bishop_failed', 'req_filtered', 'wrapped')]


_d = C.POINTER(C.c_double)
_f = C.POINTER(C.c_float)


def _dp(a):
    return a.ctypes.data_as(_d)


def _fp(a):
    return None if a is None else a.ctypes.data_as(_f)


@dataclass
class Grid:
    """dem[j, i], j = 0 the SOUTH row; x = (i + .5) dx from the west edge."""
    dem: np.ndarray
    dx: float


def single(g, xc, yc, zc, R, c, phi, gamma, delta=999.0):
    lib = _load()
    dem = np.ascontiguousarray(g.dem, dtype=np.float64)
    ny, nx = dem.shape
    lib.scoops_setup(_dp(dem), ny, nx, C.c_double(g.dx))
    o = Single()
    lib.scoops_single(*[C.c_double(v) for v in (xc, yc, zc, R, c, phi, gamma, delta)], C.byref(o))
    return {f[0]: getattr(o, f[0]) for f in Single._fields_}


def arr_of(shm, name):
    if name not in shm:
        return None
    buf, sh = shm[name]
    return np.frombuffer(buf, dtype=np.float32).reshape(sh)


# scoops_core.c's extra maps, in its order (band 0 of `xf`).
XF_NAMES = ('gate', 'csin', 'cground', 'local', 'h_a', 'hmax_a', 'sina_a', 'h_c', 'hmax_c',
            'h_any', 'hmax_any', 'h_win', 'hmax_win')
XF_FOLLOW_A = ('h_a', 'hmax_a', 'sina_a')      # belong to the surface that set creq
XF_FOLLOW_C = ('h_c', 'hmax_c')                # belong to the surface that set csin


def merge_extras(creqs, xfs):
    """Several runs' `creq` [ny, nx] and `xf` [n, ny, nx] -> one of each: highest
    values, with each depth taken from the run holding its winning surface."""
    creqs, xfs = np.stack(creqs), np.stack(xfs)
    out = np.max(xfs, axis=0)
    ia = {n: i for i, n in enumerate(XF_NAMES)}
    wa = np.argmax(creqs, axis=0)[None]
    wc = np.argmax(xfs[:, ia['csin']], axis=0)[None]
    for n in XF_FOLLOW_A:
        out[ia[n]] = np.take_along_axis(xfs[:, ia[n]], wa, axis=0)[0]
    for n in XF_FOLLOW_C:
        out[ia[n]] = np.take_along_axis(xfs[:, ia[n]], wc, axis=0)[0]
    return np.max(creqs, axis=0), out


ENV_D = ('hist', 'tile_tau', 'tile_ratio', 'tile_cord', 'tile_top')   # scoops_env_set's order
ENV_NB, ENV_NTOP = 128, 8
ENV_LO, ENV_PERDEC = 1.0, 32.0            # sigma, tau bins: 10^(LO + i / PERDEC) kPa


def _worker(k, nw, g_dem, shape, dx, xn, yn, a, shm, q, envbufs=None):
    lib = _load()
    ny, nx = shape
    dem = np.frombuffer(g_dem, dtype=np.float64).reshape(shape)
    lib.scoops_setup(_dp(dem), ny, nx, C.c_double(dx))
    lib.scoops_extras_set(_fp(arr_of(shm, 'xf')))
    if a.get('final') is not None:
        lib.scoops_final_set(_fp(np.frombuffer(a['final'], dtype=np.float32)))
    lib.scoops_extras_iderr.restype = C.c_double
    if a.get('env') is not None:
        e, b = a['env'], envbufs[k]
        view = {n: np.frombuffer(b[n], dtype=np.float64) for n in ENV_D}
        cr = np.frombuffer(b['cell_ratio'], dtype=np.float32)
        lib.scoops_env_set(C.c_double(e['x0']), C.c_double(e['y0']), C.c_double(e['size']), e['nt'], len(a['phis']),
                           *[_dp(view[n]) for n in ENV_D], _fp(cr))
    xs, ys = np.array(xn[k::nw]), np.array(yn[k::nw])
    nphi, nbin = len(a['phis']), len(a['edges']) - 1
    arr = {}
    for name, (buf, sh) in shm.items():
        arr[name] = np.frombuffer(buf, dtype=np.float32).reshape(sh)
    st = Stats()
    best = np.full(len(xs), -1e30, np.float32)
    phis = np.array(a['phis'], float) if nphi else np.zeros(1)
    edges = np.array(a['edges'], float) if nbin > 0 else np.zeros(2)
    dbl = C.c_double
    lib.scoops_search(_dp(xs), _dp(ys), C.c_long(len(xs)),
                      dbl(a['zmin']), dbl(a['zmax']), dbl(a['dz']), dbl(a['dr']),
                      dbl(a['vmin']), dbl(a['vmax']), dbl(a['tol']), a['minc'], dbl(a['gamma']),
                      nphi, _dp(phis), nbin, _dp(edges),
                      dbl(a['c']), dbl(a['phi_f']),
                      _fp(arr.get('creq')), _fp(arr.get('creq_vol')), _fp(arr.get('creq_ord')),
                      _fp(arr.get('fmin')), _fp(arr.get('fvol')), _fp(arr.get('fmin_ord')),
                      dbl(a['mincellfrac']), dbl(a['zspan']), _fp(best), C.byref(st))
    np.frombuffer(a['node_best'], dtype=np.float32)[k::nw] = best
    res = {f[0]: getattr(st, f[0]) for f in Stats._fields_}
    if 'xf' in shm:
        res['iderr'] = lib.scoops_extras_iderr()
    q.put(res)


def search(g, xn, yn, zmin, zmax, dz, dr, vmin, vmax, tol, gamma=26.0, minc=0, phis=(), edges=(),
           c=-1.0, phi_f=0.0, workers=6, mincellfrac=0.0, ordinary=False, zspan=0.0, extras=False, final=None, env=None):
    """Run a box search. xn, yn: flat arrays of lattice node positions (metres).

    phis + edges: solve for required cohesion, arrays [phi, bin, ny, nx] in kPa
        (`creq`; `creq_ord` for the Ordinary method; `creq_vol` the critical volume).
    c >= 0: also a forward run, F for this c (kPa) and phi_f (deg): `fmin` etc.
    zspan > 0: at each node search only this far above the first node over the
        ground, not all the way to zmax: a lattice that follows the terrain.
    extras: also `xf`, [len(XF_NAMES), ny, nx] for phis[0] and the first class.
    final: with extras, the finished creq [ny, nx] of this same search: a second pass
        that fills only h_win and hmax_win (see scoops_core.c).
    env: dict(x0, y0, size, nt): the strength envelope of the first class, by tiles of
        `size` m from (x0, y0) (see scoops_core.c); returned as out['env'].
    Returns dict of arrays, plus 'stats'. Cells no surface reached: 0 for creq
    (no surface found needs any cohesion), inf for fmin.
    """
    dem = np.ascontiguousarray(g.dem, dtype=np.float64)
    shape = dem.shape
    ny, nx = shape
    raw = mp.RawArray('d', dem.size)
    np.frombuffer(raw, dtype=np.float64)[:] = dem.ravel()
    xs, ys = mp.RawArray('d', len(xn)), mp.RawArray('d', len(yn))
    np.frombuffer(xs)[:] = np.asarray(xn, float); np.frombuffer(ys)[:] = np.asarray(yn, float)
    a = dict(zmin=zmin, zmax=zmax, dz=dz, dr=dr, vmin=vmin, vmax=vmax, tol=tol, gamma=gamma, minc=minc, zspan=zspan,
             phis=list(phis), edges=list(edges), c=c, phi_f=phi_f, mincellfrac=mincellfrac)
    if final is not None:
        a['final'] = mp.RawArray('f', dem.size)
        np.frombuffer(a['final'], dtype=np.float32)[:] = np.asarray(final, np.float32).ravel()
    nphi, nbin = len(phis), max(len(edges) - 1, 0)
    fields = {}
    if nphi and nbin:
        fields['creq'] = ((nphi, nbin, ny, nx), 0.0)
        fields['creq_vol'] = ((nphi, nbin, ny, nx), 0.0)
        if ordinary:
            fields['creq_ord'] = ((nphi, nbin, ny, nx), 0.0)
        if extras:
            fields['xf'] = ((len(XF_NAMES), ny, nx), 0.0)
    if c >= 0:
        fields['fmin'] = (shape, np.inf)
        fields['fvol'] = (shape, 0.0)
        fields['fmin_ord'] = (shape, np.inf)
    a['node_best'] = mp.RawArray('f', len(xn))
    ctx = mp.get_context('fork')
    q = ctx.Queue()
    procs, stores = [], []
    envbufs = None
    if env is not None:
        a['env'] = env
        nt2, nph = env['nt'] ** 2, len(phis)
        sz = dict(hist=ENV_NB ** 2, tile_tau=nt2 * ENV_NB, tile_ratio=nt2, tile_cord=nt2 * nph, tile_top=nt2 * nph * ENV_NTOP)
        envbufs = [{n: mp.RawArray('d', sz[n]) for n in ENV_D} for _ in range(workers)]
        for b in envbufs:
            b['cell_ratio'] = mp.RawArray('f', dem.size)
            np.frombuffer(b['tile_cord'])[:] = -1e30
    for k in range(workers):
        shm = {}
        for name, (sh, fill) in fields.items():
            buf = mp.RawArray('f', int(np.prod(sh)))
            np.frombuffer(buf, dtype=np.float32)[:] = fill
            shm[name] = (buf, sh)
        stores.append(shm)
        p = ctx.Process(target=_worker, args=(k, workers, raw, shape, g.dx, xs, ys, a, shm, q, envbufs))
        p.start(); procs.append(p)
    stats = [q.get() for _ in procs]
    for p in procs:
        p.join()
        if p.exitcode:
            raise RuntimeError(f'worker exit {p.exitcode}')
    out = {}
    for name, (sh, _) in fields.items():
        arrs = [np.frombuffer(s[name][0], dtype=np.float32).reshape(sh) for s in stores]
        if name in ('creq', 'creq_ord'):
            out[name] = np.max(arrs, axis=0)
        elif name in ('fmin', 'fmin_ord'):
            out[name] = np.min(arrs, axis=0)
    if 'creq' in fields:                       # volume of the surface that won
        arrs = [np.frombuffer(s['creq'][0], dtype=np.float32).reshape(fields['creq'][0]) for s in stores]
        vols = [np.frombuffer(s['creq_vol'][0], dtype=np.float32).reshape(fields['creq'][0]) for s in stores]
        win = np.argmax(arrs, axis=0)
        out['creq_vol'] = np.take_along_axis(np.stack(vols), win[None], axis=0)[0]
    if 'fmin' in fields:
        arrs = [np.frombuffer(s['fmin'][0], dtype=np.float32).reshape(shape) for s in stores]
        vols = [np.frombuffer(s['fvol'][0], dtype=np.float32).reshape(shape) for s in stores]
        win = np.argmin(arrs, axis=0)
        out['fvol'] = np.take_along_axis(np.stack(vols), win[None], axis=0)[0]
    if 'xf' in fields:
        cs = [np.frombuffer(s['creq'][0], dtype=np.float32).reshape(fields['creq'][0])[0, 0] for s in stores]
        xs_ = [np.frombuffer(s['xf'][0], dtype=np.float32).reshape(fields['xf'][0]) for s in stores]
        out['xf'] = merge_extras(cs, xs_)[1]
    if envbufs:
        nt2, nph = env['nt'] ** 2, len(phis)
        get = lambda n: np.stack([np.frombuffer(b[n], dtype=np.float64) for b in envbufs])
        top = get('tile_top').reshape(workers, nt2, nph, ENV_NTOP)
        win = np.argmax(top[..., 0], axis=0)[None, ..., None]
        out['env'] = dict(hist=get('hist').sum(0).reshape(ENV_NB, ENV_NB),
                          tile_tau=get('tile_tau').max(0).reshape(nt2, ENV_NB),
                          tile_ratio=get('tile_ratio').max(0),
                          tile_cord=get('tile_cord').max(0).reshape(nt2, nph),
                          tile_top=np.take_along_axis(top, win, axis=0)[0],
                          cell_ratio=np.max([np.frombuffer(b['cell_ratio'], dtype=np.float32) for b in envbufs], axis=0).reshape(shape))
    out['node_best'] = np.frombuffer(a['node_best'], dtype=np.float32).copy()
    out['stats'] = {k: sum(s[k] for s in stats) for k in stats[0] if k != 'iderr'}
    if 'iderr' in stats[0]:
        out['stats']['iderr'] = max(s['iderr'] for s in stats)
    return out


def lattice(nx, ny, dx, ismin=1, jsmin=1, ismax=None, jsmax=None, step=1):
    """Scoops3D's horizontal lattice: 1-based i, j, nodes above cell centres."""
    ismax = ismax or nx; jsmax = jsmax or ny
    ii, jj = np.meshgrid(np.arange(ismin, ismax + 1, step), np.arange(jsmin, jsmax + 1, step))
    return (ii.ravel() - .5) * dx, (jj.ravel() - .5) * dx
