"""Build the reclassification comparison surfaces as DEV-ONLY /lidar/ datasets.

    python model/viewer_build.py <final_tag> [site ...]

Per site, three surfaces on the same 1 m grid:
  rc_<site>_grewingk   Grewingk 2021 ground TIN RAISED by the site's strict offset, so a
                       difference against KBay shows classification, not datum
  rc_<site>_vendor     KBay 2023, NV5 ground (class 2) TIN
  rc_<site>_final      KBay 2023, reclassified: edited ground + crest fill + water returns
                       (no flattening) + certainty-weighted surface inside zones (<final_tag>)

Tiles are made by tools/lidar/build_lidar.py's own build_web/build_pmtiles (per-zoom warp
with -s_srs EPSG:6334 -novshift, terrain-RGB), with its output redirected:
  work  -> kbay_reclass_test/viewer_build/   (never the shared lidar_build/ top level)
  pmtiles -> <repo>/data/lidar_dev/pmtiles/  (OUTSIDE data/lidar/: RESUME.md's redeploy rsync
                                              copies all of data/lidar/ to the droplet)
and a catalogue at data/lidar_dev/catalog.geojson, served only when DEBUG is on.
"""
import json, os, sys
from pathlib import Path
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)

HERE = Path('/Volumes/Nunatak/lidar_build/kbay_reclass_test')
REPO = Path('/Users/Hig/Claude_projects/landslidescience')
os.environ['LIDAR_BUILD'] = str(HERE / 'viewer_build')
os.environ['LIDAR_PM_OUT'] = str(REPO / 'data' / 'lidar_dev' / 'pmtiles')
sys.path.insert(0, str(REPO / 'tools' / 'lidar')); import build_lidar as B
sys.path.insert(0, str(HERE / 'model')); os.chdir(HERE)
import sitekit as K
import rasterio
from rasterio.warp import transform_bounds

MINZ, MAXZ = 12, 17
LABEL = {'patch1': 'Patch 1 (steep, dense brush)', 'alder': 'Alder thicket', 'island': 'Lake island',
         'bare_gentle': 'Outwash / lake edge', 'vendor_poor': 'Vendor-poor spot (no Grewingk)', 'woz_boulders': 'Upper Woz boulder pile (no Grewingk)', 'forest_tall': 'Tall forest', 'meadow_shrub': 'Meadow / shrub', 'tides': 'Tide-level overlap (no Grewingk)', 'snow': 'Thick snow (no Grewingk)', 'marsh': 'Marsh channels, brush + trees (no Grewingk)', 'tides2': 'Tides 2, 59.51931 -151.30463 (no Grewingk)'}


def write_raised(src, dst, off):
    with rasterio.open(src) as s:
        a = s.read(1); prof = s.profile; nd = s.nodata
    ok = a != nd if nd is not None else np.isfinite(a)
    prof.update(crs='EPSG:6334')
    with rasterio.open(dst, 'w', **prof) as d:
        d.write(np.where(ok, a + off, nd).astype('float32'), 1)


def as_6334(src, dst):
    with rasterio.open(src) as s:
        a = s.read(1); prof = s.profile
    prof.update(crs='EPSG:6334')          # horizontal only: the grids are NAVD88 heights already
    with rasterio.open(dst, 'w', **prof) as d: d.write(a, 1)


VEG_BANDS = [  # (lo, hi, RGBA, legend label); heights in m above THIS surface
    (-1e9, -0.3, (209, 31, 160, 255), 'surface above every return'),   # (cell max of per-return height < -0.3)
    (-0.3, 0.25, (0, 0, 0, 0), None),
    (0.25, 0.5, (255, 247, 188, 255), '0.25-0.5 m'), (0.5, 1, (254, 227, 145, 255), '0.5-1'),
    (1, 2, (199, 233, 192, 255), '1-2'), (2, 5, (116, 196, 118, 255), '2-5'),
    (5, 10, (35, 139, 69, 255), '5-10'), (10, 20, (0, 90, 50, 255), '10-20'), (20, 1e9, (0, 50, 30, 255), '> 20 m')]


def build_veg(site, kind, surf_path, ds_id):
    """Highest return minus this surface, coloured in fixed bands -> PNG PMTiles <ds_id>_veg."""
    import subprocess, shutil
    d = HERE / 'site' / site; work = HERE / 'viewer_build'
    with rasterio.open(d / 'kbay_max.tif') as s_:
        prof = s_.profile; NY, NX = s_.height, s_.width; T = s_.transform
    with rasterio.open(surf_path) as s_:
        Sf = s_.read(1).astype(float); nd2 = s_.nodata
    if nd2 is not None: Sf[Sf == nd2] = np.nan
    # Height of each RETURN above the surface AT THAT RETURN'S OWN POSITION, then the cell max.
    # 'highest return minus the surface at the cell centre' read a bare near-vertical cliff as
    # tens of metres of vegetation (Hig, 59.49864 -151.00824): one 1 m cell spans the whole face.
    from scipy import ndimage as ndi
    P = np.load(d / 'pts.npz'); cls = P['cls']
    ok = ~np.isin(cls, EXCL) & (cls != 9)
    x, y, z = P['x'][ok], P['y'][ok], P['z'][ok]
    row = (T.f - y) / (-T.e) - 0.5; col = (x - T.c) / T.a - 0.5
    Sfill = np.where(np.isfinite(Sf), Sf, np.nanmedian(Sf))
    # Compare with the HIGHEST surface within ~1 m (3x3 max), not the value directly beneath: a
    # 1 m raster holds one height per cell, and on a 60-88 deg face rock returns in the upper part
    # of a cell sit metres above it. Flat ground is unchanged; low vegetation on moderate slopes
    # reads up to ~tan(slope) x 0.7 m shorter -- so it is applied only where the slope is >= 45 deg
    # (alder: whole-envelope cut vegetation > 2 m from 74% to 64% of cells).
    gyS, gxS = np.gradient(Sfill); steepS = ndi.maximum_filter(np.degrees(np.arctan(np.hypot(gxS, gyS))), 3) >= 45
    Senv = np.where(steepS, ndi.maximum_filter(Sfill, 3), Sfill)      # envelope ONLY on steep ground (>= 45 deg)
    hp = z - ndi.map_coordinates(Senv, [row, col], order=1, mode='nearest')
    cid = np.clip(row.round().astype(int), 0, NY-1) * NX + np.clip(col.round().astype(int), 0, NX-1)
    h = np.full(NX * NY, -np.inf); np.maximum.at(h, cid, hp); h = h.reshape(NY, NX)
    h[np.isinf(h)] = np.nan; h[~np.isfinite(Sf)] = np.nan
    rgba = np.zeros((4,) + h.shape, np.uint8)
    for lo, hi, col, _ in VEG_BANDS:
        m = (h >= lo) & (h < hi)
        for k in range(4): rgba[k][m] = col[k]
    rgba[:, ~np.isfinite(h)] = 0
    prof.update(count=4, dtype='uint8', nodata=None, crs='EPSG:6334', compress='deflate', photometric='RGB', alpha='YES')  # ALPHA=YES: else band 4 is 'undefined' and the tiler adds a 5th
    tif = work / 'src' / f'{ds_id}_veg.tif'
    with rasterio.open(tif, 'w', **prof) as dst:
        dst.write(rgba)
        dst.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    tiles = work / f'{ds_id}_veg_tiles'; shutil.rmtree(tiles, ignore_errors=True)
    B.run([B.GDAL_BIN / 'gdal', 'raster', 'tile', '--min-zoom', MINZ, '--max-zoom', MAXZ, '-r', 'nearest',
           '--convention', 'xyz', '--skip-blank', '--webviewer', 'none', '-j', 'ALL_CPUS', '--no-intersection-ok',
           '-f', 'PNG', tif, tiles], env=B.clean_env())
    vds = dict(id=f'{ds_id}_veg', title=f'{ds_id} vegetation height', min_zoom=MINZ, max_zoom=MAXZ)
    mb = work / f'{ds_id}_veg.mbtiles'
    B.dir_to_mbtiles(tiles, mb, vds, 'png', description='vegetation height above the surface')
    out = REPO / 'data' / 'lidar_dev' / 'pmtiles' / f'{ds_id}_veg.pmtiles'
    B.run([B.PMTILES, 'convert', mb, out]); mb.unlink(missing_ok=True); shutil.rmtree(tiles, ignore_errors=True)
    return f'/lidar/dev/pmtiles/{ds_id}_veg.pmtiles'


def build(site, final_tag):
    d = HERE / 'site' / site; work = HERE / 'viewer_build' / 'src'; work.mkdir(parents=True, exist_ok=True)
    F = K.offset_grid(str(d))
    feats = []
    for kind, src, title, note in [
        ('grewingk', d / 'grewingk_dtm.tif', f'Grewingk 2021 (+ offset field {np.nanmin(F):.2f}-{np.nanmax(F):.2f} m)',
         'DGGS RDF 2023-2 ground TIN raised by the KBay-Grewingk OFFSET FIELD (bare-in-both ground, 50 m blocks, '
         '150 m smoothing, pulled to the site median where support is thin). Catalogue year 2023 on purpose: '
         '/lidar/ shows different-year differences as a per-year rate; equal years give plain metres, active minus other.'),
        ('vendor', d / 'vendor_dtm.tif', 'KBay 2023 vendor ground', 'NV5 class 2, TIN.'),
        ('blend', d / 'dtm_final.tif', 'KBay 2023 BLEND (+ guards, rock)',
         'Best (slope-adaptive trimming; vendor ground never dropped in single-return cells) + w*(v2a - Best), w '
         'learned leave-one-site-out; v2a may raise > 0.5 m only over a supported raised area and may not lower '
         '> 0.5 m in single-return cells (boulders); ROCK: in dark, all-single-return cells whose vendor surface '
         'sits > 0.3 m below every return, every return is ground.')]:
        if kind == 'grewingk' and (d / 'NO_GREWINGK_grewingk_dtm_is_a_template_copy_of_vendor').exists():
            continue                                               # no Grewingk here (apply.py site)
        ds_id = f'rc_{site}_{kind}'
        arch = work / f'{ds_id}.tif'
        if kind == 'grewingk':
            with rasterio.open(src) as s_:
                a_ = s_.read(1); prof = s_.profile; nd = s_.nodata
            prof.update(crs='EPSG:6334')
            ok = a_ != nd if nd is not None else np.isfinite(a_)
            with rasterio.open(arch, 'w', **prof) as d_: d_.write(np.where(ok, a_ + F, nd).astype('float32'), 1)
        else:
            as_6334(src, arch)
        ds = dict(id=ds_id, title=f'{LABEL.get(site, site)}: {title}', min_zoom=MINZ, max_zoom=MAXZ, target_epsg=6334)
        tiles = B.build_web(ds, arch, B.clean_env())
        out = B.build_pmtiles(ds, tiles)
        with rasterio.open(arch) as s_:
            b_ = transform_bounds(s_.crs, 'EPSG:4326', *s_.bounds); a_ = s_.read(1, masked=True)
        feats.append({'type': 'Feature',
                      'geometry': {'type': 'Polygon', 'coordinates': [[[b_[0], b_[1]], [b_[2], b_[1]], [b_[2], b_[3]], [b_[0], b_[3]], [b_[0], b_[1]]]]},
                      'properties': dict(id=ds_id, title=ds['title'], region='Reclassification test (dev)', year=2023,
                                         product='DTM (test)', native_res_m=1.0, coverage_km2=round(float((~a_.mask).sum())*1e-6, 2),
                                         horizontal_crs='EPSG:6334', vertical_datum='NAVD88', min_zoom=MINZ, max_zoom=MAXZ,
                                         bounds=[round(v, 6) for v in b_], pmtiles_url=f'/lidar/dev/pmtiles/{ds_id}.pmtiles',
                                         pmtiles_bytes=out.stat().st_size, tiles_url=None, slope_tiles_url=None,
                                         slope_url=None, slope_step=0.5, fill_mode='holes', ortho_url=None, cog_url=None,
                                         z_min=float(a_.min()), z_max=float(a_.max()), source='KBay reclassification test',
                                         notes=note)})
        if kind in ('vendor', 'blend'):
            feats[-1]['properties']['veg_url'] = build_veg(site, kind, src, ds_id)
            feats[-1]['properties']['veg_legend'] = [['#%02x%02x%02x' % c[:3], lab] for lo, hi, c, lab in VEG_BANDS if lab]
    return feats


if __name__ == '__main__':
    tag = sys.argv[1]; sites = sys.argv[2:] or ['patch1', 'alder', 'island', 'bare_gentle']
    cat = REPO / 'data' / 'lidar_dev' / 'catalog.geojson'
    fc = {'type': 'FeatureCollection', 'features': []}          # fresh each run: no accumulation (Hig)
    # 3D terrain outside a survey comes from the catalogue's `context` (the baked Alaska 3DEP
    # archive). Without it the page fell back to the live ImageServer and the ground around
    # each patch sat flat (Hig, 2026-09-26) -- carry the public catalogue's context over.
    fc['context'] = json.loads((REPO / 'data' / 'lidar' / 'catalog.geojson').read_text()).get('context')
    for old in (REPO / 'data' / 'lidar_dev' / 'pmtiles').glob('*.pmtiles'): old.unlink()
    for s in sites:
        new = build(s, tag)
        ids = {f['properties']['id'] for f in new}
        fc['features'] = [f for f in fc['features'] if f['properties']['id'] not in ids] + new
        # atomic replace (new file, then rename): an in-place rewrite let Docker's file
        # sharing serve new bytes at the old length -> truncated, invalid JSON
        tmp = cat.with_suffix('.tmp'); tmp.write_text(json.dumps(fc)); os.replace(tmp, cat)
        print('built', s, flush=True)
