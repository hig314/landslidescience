#!/usr/bin/env python3
"""The water surface as a DEM of its own, built to be drawn over the bed it belongs to.

    water_surface.py --bed dem.tif --wse wse.tif [--sea sea.tif] --out DIR [--pad-m 24] [--smooth-m 4]

Writes DIR/water_surface.tif (float32, NAVD88, -9999 where there is no water near) and, when given,
DIR/sea.tif (the sea mask, cropped to the same grid). The viewer pairs them with the bed DEM: water is
drawn wherever the surface stands above the bed, so the shoreline is a comparison of two surfaces at
every zoom, never the edge of a mask (2026-10-20, Hig: water as a DEM linked to the topobathy DEM,
drawn see-through over it, eventually ice the same way).

Three things make a surface fit to be shaded and meshed:

  * Smooth. A hillshade magnifies roughness, and water returns are noisy (waves, sparse returns, the
    0.1 m steps of a coarse encoding). The surface is a normalised Gaussian over water cells only
    (sigma --smooth-m), so a flat lake stays exactly flat and a river keeps its gradient.
  * Padded. The surface continues --pad-m past the water's edge at the nearest water level, held
    just below the bed wherever the bed is lower than that level (ground the source called dry stays
    dry). At full resolution the padding is invisible -- it is all below the bed -- but when a zoom
    averages cells the shoreline pixel still has a water level to compare, and the 3D mesh has
    vertices on both sides of the shore to interpolate the crossing between.
  * Finely encoded. The pyramid is terrarium (1/256 m) rather than Mapbox terrain-RGB (0.1 m): a
    river falling 1 m in 100 m would otherwise shade as a stripe every 10 m.
"""
import argparse, json
from pathlib import Path
import numpy as np, rasterio
from rasterio.windows import Window
from scipy import ndimage

ND = -9999.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bed', required=True); ap.add_argument('--wse', required=True)
    ap.add_argument('--sea', default=None); ap.add_argument('--out', required=True)
    ap.add_argument('--pad-m', type=float, default=24.0); ap.add_argument('--smooth-m', type=float, default=4.0)
    ap.add_argument('--below-bed-m', type=float, default=0.05)
    ap.add_argument('--keep', default=None, help='uint8 raster (1 = keep): cells inside the sea mask whose inland level stands -- rivers, lakes, '
                    'perched pools; every other sea-mask cell is floored to --low-tide, so the sea the survey saw at its stands '
                    '(0.6-3 m on different flight lines) is not drawn as topography over the tide the viewer chooses (2026-10-25)')
    ap.add_argument('--low-tide', type=float, default=None, help='the floor for the sea (MLLW in the DEM datum)')
    ap.add_argument('--lake-frac', type=float, default=0.3); ap.add_argument('--lake-tol-m', type=float, default=0.1)
    ap.add_argument('--enclosed', type=float, default=0.8)
    ap.add_argument('--flood-to', type=float, default=None,
                    help='grow the sea mask over every bed cell below this height (m) that connects to it: the intertidal zone '
                         'and the tidal reach of a river, so the tide the viewer chooses floods them (MHHW + a margin)')
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    with rasterio.open(a.wse) as s:
        prof = s.profile.copy(); res = abs(s.transform.a); T = s.transform
        w = s.read(1).astype(np.float32); wnd = s.nodata
    water = np.isfinite(w) & ((w != wnd) if wnd is not None else True)
    if not water.any():
        raise SystemExit('no water in the surface raster')
    pad = int(np.ceil(a.pad_m / res)) + 2
    rows = np.where(water.any(1))[0]; cols = np.where(water.any(0))[0]
    r0, r1 = max(0, rows[0] - pad), min(w.shape[0], rows[-1] + pad + 1)
    c0, c1 = max(0, cols[0] - pad), min(w.shape[1], cols[-1] + pad + 1)
    win = Window(c0, r0, c1 - c0, r1 - r0)
    w = w[r0:r1, c0:c1]; water = water[r0:r1, c0:c1]
    with rasterio.open(a.bed) as s:
        if abs(s.transform.a - res) > 1e-9 or s.transform.c != T.c or s.transform.f != T.f:
            raise SystemExit('bed and water surface must share one grid')
        bed = s.read(1, window=win).astype(np.float32)
        if s.nodata is not None: bed[bed == s.nodata] = np.nan

    # A LAKE IS FLAT (Hig, 2026-10-20, Grewingk Lake): returns standing above a lake's level and surrounded by
    # it are floating ice, not the water surface -- 345 lumps of 0.3-15 m on Grewingk, from 2021's returns off
    # bergs. A body is a lake where at least --lake-frac of its cells share one level (to 1 cm); a patch above
    # that level by more than --lake-tol-m whose rim is at least --enclosed of the lake at its level is set to
    # the level. A river running into the lake rises above it too, but its rim runs on to the bank: kept.
    lab, nb = ndimage.label(water, structure=np.ones((3, 3), bool))
    flat_n = 0; flat_p = 0
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        m = lab[sl] == i; v = w[sl][m]
        if v.size < 100:
            continue
        q = np.round(v * 100).astype(np.int64)
        vals, cnt = np.unique(q, return_counts=True)
        if cnt.max() < a.lake_frac * v.size:
            continue
        lev = vals[np.argmax(cnt)] / 100.0
        sub = np.where(m, w[sl], np.nan)
        at = m & (np.abs(sub - lev) < 0.005)
        hi = m & (sub > lev + a.lake_tol_m)
        hl, hn = ndimage.label(hi, structure=np.ones((3, 3), bool))
        if not hn:
            continue
        ring = ndimage.binary_dilation(hi, structure=np.ones((3, 3), bool)) & ~hi
        own = ndimage.grey_dilation(hl, size=3)
        idx = np.arange(1, hn + 1)
        n_ring = ndimage.sum(ring, own, idx); n_at = ndimage.sum(ring & at, own, idx)
        ice = np.isin(hl, idx[np.asarray(n_at) >= a.enclosed * np.asarray(n_ring)])
        if ice.any():
            blk = w[sl]; blk[ice] = lev; w[sl] = blk
            flat_n += int(ice.sum()); flat_p += int(len(np.unique(hl[ice])))
    if flat_n:
        print(f'lakes flattened: {flat_p} raised patches enclosed by a lake (floating ice), {flat_n} cells set to its level')

    # smooth over water cells only (normalised), so edges are not pulled toward zero
    sg = a.smooth_m / res
    if sg > 0:
        z = np.where(water, w, 0.0).astype(np.float64)
        num = ndimage.gaussian_filter(z, sg, mode='nearest')
        den = ndimage.gaussian_filter(water.astype(np.float64), sg, mode='nearest')
        sm = np.where(water, num / np.maximum(den, 1e-6), np.nan).astype(np.float32)
    else:
        sm = np.where(water, w, np.nan).astype(np.float32)
    # a lake is flat: where the source is flat within 1 mm over the kernel, keep the source value exactly
    keep = water & (np.abs(sm - w) < 0.001)
    sm[keep] = w[keep]

    # pad: nearest water level out to pad_m, held below the bed where the bed is lower than that level
    dist, (ir, ic) = ndimage.distance_transform_edt(~water, return_indices=True)
    near = sm[ir, ic]
    padz = (~water) & (dist * res <= a.pad_m)
    # no padding over a void in the bed: water there would be a claim the source never made
    held = np.where(np.isfinite(bed), np.minimum(near, bed - a.below_bed_m), np.nan)
    surf = np.where(water, sm, np.where(padz, held, np.nan)).astype(np.float32)
    # THE SEA IS THE TIDE, NOT THE STANDS THE SURVEY SAW (2026-10-25, Hig: topography on the low-tide ocean surface --
    # the lidar's water returns at 0.6-3 m on different flight lines, each the sea at its own stand, drawn over the
    # tide the viewer chooses because the viewer takes the higher of the two). Inside the sea mask every cell not in
    # --keep (rivers, lakes, perched pools, whose own level stands) is floored to --low-tide, so the sea reads as the
    # chosen tide and only a river's low-tide surface rises above it.
    floored = 0
    if a.keep and a.sea and a.low_tide is not None:
        with rasterio.open(a.sea) as s_:
            sea_ = s_.read(1, window=win)
        with rasterio.open(a.keep) as s_:
            keep_ = s_.read(1, window=win)
        fl = (sea_ > 0) & (keep_ != 1) & np.isfinite(surf)
        surf[fl] = float(a.low_tide); floored = int(fl.sum())
        print(f'sea floored to the tide: {floored * res * res / 1e6:.3f} km2 of the sea mask set to {a.low_tide} m (rivers, lakes and pools kept)')

    T2 = rasterio.windows.transform(win, T)
    prof.update(driver='GTiff', width=win.width, height=win.height, transform=T2, dtype='float32', nodata=ND,
                tiled=True, blockxsize=512, blockysize=512, compress='deflate', predictor=3)
    with rasterio.open(out / 'water_surface.tif', 'w', **prof) as d:
        d.write(np.where(np.isfinite(surf), surf, ND).astype(np.float32), 1)
    rep = dict(res_m=res, window=[int(c0), int(r0), int(win.width), int(win.height)],
               water_km2=round(float(water.sum()) * res * res / 1e6, 3),
               padded_km2=round(float(padz.sum()) * res * res / 1e6, 3),
               held_below_bed_km2=round(float((padz & np.isfinite(bed) & (bed < near)).sum()) * res * res / 1e6, 3),
               smooth_m=a.smooth_m, pad_m=a.pad_m, lake_ice_cells=flat_n, lake_ice_patches=flat_p, sea_floored_cells=floored,
               smoothing_change_m_p50_p99=np.round(np.nanpercentile(np.abs(sm - w)[water], [50, 99]), 3).tolist())
    if a.sea:
        with rasterio.open(a.sea) as s:
            sp = s.profile.copy(); sea = s.read(1)
        # THE SEA IS WHEREVER THE TIDE CAN REACH (2026-10-23, run 14, Hig: the ocean a step up from the river at its mouth,
        # and dots of water standing at the sea's height out on the flats). The viewer draws the chosen tide over the sea
        # mask where it stands above the inland surface; a mask of the water BODIES alone -- the sea the survey saw, tidal
        # ponds -- left the river's tidal reach at its surveyed low-tide surface beside a sea raised to MHW, and each pond
        # at the tide while the flat around it stayed dry. The mask grows over every bed cell below --flood-to that
        # connects to the sea (8-connected): the flats and the river mouth flood to the tide, and only above it.
        if a.flood_to is not None:
            with rasterio.open(a.bed) as s:
                bfull = s.read(1).astype(np.float32)
                if s.nodata is not None: bfull[bfull == s.nodata] = np.nan
            low = np.isfinite(bfull) & (bfull < a.flood_to); del bfull
            was = sea > 0
            lab2, n2 = ndimage.label(low | was, structure=np.ones((3, 3), bool))
            keep = np.unique(lab2[was]); keep = keep[keep > 0]
            grown = np.isin(lab2, keep) & (low | was); del lab2
            rep['sea_km2'] = round(float(was.sum()) * res * res / 1e6, 3)
            rep['sea_flooded_to_m'] = a.flood_to; rep['sea_grown_km2'] = round(float((grown & ~was).sum()) * res * res / 1e6, 3)
            print(f"sea mask grown to the tide's reach (< {a.flood_to} m, connected): {rep['sea_km2']} -> {rep['sea_km2'] + rep['sea_grown_km2']:.3f} km2")
            sea = grown.astype(sea.dtype)
        sp.update(driver='GTiff', tiled=True, blockxsize=512, blockysize=512, compress='deflate')
        with rasterio.open(out / 'sea.tif', 'w', **sp) as d:
            d.write(sea, 1)
    (out / 'water_surface.json').write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == '__main__':
    main()
